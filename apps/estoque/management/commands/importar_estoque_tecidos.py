"""
Importa uma planilha de tecidos (nome + quantidade) como matéria-prima no
estoque de um depósito.

Cada linha vira um Produto enxuto de matéria-prima (mesmo que o cadastro de
aviamentos usa: `criar_produto_materia_prima`) e o saldo entra no depósito
informado por ajuste manual, com justificativa.

Por padrão SÓ SIMULA: mostra o que faria e não grava nada. Para gravar,
passe --confirmar. Rodar de novo é seguro: tecido que já existe na filial
(mesmo nome) é pulado e listado, nunca duplicado nem somado.

Uso:
    python manage.py importar_estoque_tecidos --arquivo Estoque_Tecido.xlsx \\
        --filial-id 1 --deposito-id 3 --usuario-email fulano@empresa.com \
        [--empresa-alias empresa_xxx]   # obrigatório se a empresa tem banco próprio
    (confira a simulação, depois repita com --confirmar)
"""
from collections import OrderedDict
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError

from apps.core.models import EmpresaBanco, Filial, Usuario
from apps.core.tenant_context import tenant_atomic, tenant_db
from apps.core.tenant_registry import register_tenant_database
from apps.estoque.models import Deposito
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.produtos.models import Produto, UnidadeMedida, UnidadeMedidaFilial


def _normalizar(nome):
    return ' '.join(str(nome).split()).casefold()


def ler_planilha(caminho, duplicados='somar'):
    """Devolve lista ordenada de (nome, quantidade), juntando repetidos."""
    import openpyxl

    planilha = openpyxl.load_workbook(caminho, data_only=True).worksheets[0]
    linhas = list(planilha.iter_rows(min_row=2, values_only=True))
    tecidos = OrderedDict()
    for numero, linha in enumerate(linhas, start=2):
        nome, quantidade = (linha + (None, None))[:2]
        if nome is None or str(nome).strip() == '':
            continue
        try:
            quantidade = Decimal(str(quantidade)).quantize(Decimal('0.001'))
        except (InvalidOperation, TypeError):
            raise CommandError(f'Linha {numero}: quantidade inválida ({quantidade!r}).')
        if quantidade < 0:
            raise CommandError(f'Linha {numero}: quantidade negativa.')
        nome = ' '.join(str(nome).split())
        chave = _normalizar(nome)
        if chave in tecidos:
            if duplicados == 'somar':
                tecidos[chave]['quantidade'] += quantidade
                tecidos[chave]['linhas'].append(numero)
                continue
            # manter separados: o repetido ganha sufixo (2), (3)...
            sufixo = 2
            while _normalizar(f'{nome} ({sufixo})') in tecidos:
                sufixo += 1
            nome = f'{nome} ({sufixo})'
            chave = _normalizar(nome)
        tecidos[chave] = {'nome': nome, 'quantidade': quantidade, 'linhas': [numero]}
    return list(tecidos.values())


class Command(BaseCommand):
    help = 'Importa tecidos e saldos de uma planilha para um depósito (simula por padrão).'

    def add_arguments(self, parser):
        parser.add_argument('--arquivo', required=True)
        parser.add_argument(
            '--empresa-alias', default='',
            help='Alias do banco da empresa (ex.: empresa_erk_...). Obrigatório para empresa '
                 'com banco próprio: os ids de filial/depósito são os do banco dela.',
        )
        parser.add_argument('--filial-id', type=int, required=True)
        parser.add_argument('--deposito-id', type=int, required=True)
        parser.add_argument('--usuario-email', required=True)
        parser.add_argument('--unidade-sigla', default='KG')
        parser.add_argument('--unidade-descricao', default='Quilograma')
        parser.add_argument(
            '--duplicados', choices=['somar', 'separar'], default='somar',
            help='Nome repetido na planilha: somar as quantidades (padrão) ou manter separado.',
        )
        parser.add_argument(
            '--confirmar', action='store_true',
            help='Grava de verdade. Sem isto, só simula.',
        )

    def handle(self, *args, **opcoes):
        alias = opcoes['empresa_alias'].strip()
        if not alias:
            return self._executar(opcoes)
        banco = EmpresaBanco.objects.using('default').filter(db_alias=alias, ativo=True).first()
        if not banco or not register_tenant_database(banco):
            raise CommandError(f'Banco {alias!r} não encontrado, inativo ou sem conexão.')
        with tenant_db(alias):
            return self._executar(opcoes)

    def _executar(self, opcoes):
        filial = Filial.objects.select_related('empresa').filter(pk=opcoes['filial_id']).first()
        if not filial:
            raise CommandError('Filial não encontrada.')
        deposito = Deposito.objects.filter(pk=opcoes['deposito_id'], filial=filial).first()
        if not deposito:
            raise CommandError('Depósito não encontrado nesta filial.')
        usuario = Usuario.objects.filter(email__iexact=opcoes['usuario_email'], ativo=True).first()
        if not usuario:
            raise CommandError('Usuário não encontrado (é ele quem assina o ajuste de estoque).')

        tecidos = ler_planilha(opcoes['arquivo'], opcoes['duplicados'])
        sigla = opcoes['unidade_sigla'].strip().upper()
        unidade = UnidadeMedida.objects.filter(empresa=filial.empresa, sigla=sigla).first()

        existentes = {
            _normalizar(descricao)
            for descricao in Produto.objects.filter(filial=filial).values_list('descricao', flat=True)
        }
        novos = [t for t in tecidos if _normalizar(t['nome'][:150]) not in existentes]
        pulados = [t for t in tecidos if _normalizar(t['nome'][:150]) in existentes]

        confirmar = opcoes['confirmar']
        self.stdout.write(
            f"{'GRAVANDO' if confirmar else 'SIMULAÇÃO (nada será gravado)'} — "
            f'{filial} · depósito "{deposito.nome}" · unidade {sigla}'
            f"{'' if unidade else ' (será criada)'}"
        )
        self.stdout.write(f'Na planilha: {len(tecidos)} tecidos · {len(novos)} novos · {len(pulados)} já existem')
        for t in tecidos:
            extra = f" (linhas {', '.join(map(str, t['linhas']))} somadas)" if len(t['linhas']) > 1 else ''
            marca = 'PULA (já existe)' if t in pulados else 'novo'
            self.stdout.write(f"  {marca:<17} {t['nome']}: {t['quantidade']} {sigla}{extra}")
        total = sum(t['quantidade'] for t in novos)
        self.stdout.write(f'Total a lançar: {total} {sigla} em {len(novos)} tecidos')

        if not confirmar:
            self.stdout.write(self.style.WARNING('Simulação concluída. Repita com --confirmar para gravar.'))
            return

        from apps.moda.views_apoio import criar_produto_materia_prima

        with tenant_atomic():
            if not unidade:
                unidade = UnidadeMedida.objects.create(
                    empresa=filial.empresa, sigla=sigla,
                    descricao=opcoes['unidade_descricao'], tipo=UnidadeMedida.Tipo.PESO,
                )
            UnidadeMedidaFilial.objects.get_or_create(unidade=unidade, filial=filial)
            for t in novos:
                produto = criar_produto_materia_prima(
                    filial, {'nome': t['nome'], 'unidade_medida': unidade}, 'Importação de Tecidos',
                )
                if t['quantidade'] > 0:
                    MovimentacaoService.ajustar_manual(
                        produto_id=produto.pk, filial_id=filial.pk,
                        quantidade_nova=t['quantidade'], usuario_id=usuario.pk,
                        justificativa=(
                            'Saldo inicial importado da planilha de estoque de tecidos '
                            f'no depósito {deposito.nome}.'
                        ),
                        deposito_id=deposito.pk,
                    )
        self.stdout.write(self.style.SUCCESS(f'{len(novos)} tecidos importados, {total} {sigla} lançados.'))
