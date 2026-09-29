"""
Cadastra, como tecido (matéria-prima) sem estoque, cada nome de malha usado
no catálogo fixo da OP 2.0 (apps.moda.services.op2_estrutura) que ainda não
existe como produto na filial.

O catálogo de "malha" da OP (PP, DRY, MOLETOM...) é uma lista de tipos
genéricos de tecido, independente do cadastro de tecidos do estoque (que
tem cor/variante no nome, ex.: "Dry (Preto)"). Este comando só GARANTE que
cada nome genérico também exista como produto -- não mexe em saldo nem
tenta casar com os tecidos já importados.

Por padrão SÓ SIMULA. Para gravar, passe --confirmar. Rodar de novo é
seguro: nome já existente na filial é pulado.

Uso:
    python manage.py cadastrar_malhas_op2 --filial-id 4 [--confirmar]
"""
from django.core.management.base import BaseCommand, CommandError

from apps.core.models import EmpresaBanco, Filial
from apps.core.tenant_context import tenant_atomic, tenant_db
from apps.core.tenant_registry import register_tenant_database
from apps.moda.services.op2_estrutura import OP2_ESTRUTURA_OPCOES
from apps.produtos.models import Produto, UnidadeMedida, UnidadeMedidaFilial


def _normalizar(nome):
    return ' '.join(str(nome).split()).casefold()


def catalogo_malhas():
    """Nomes únicos de malha do catálogo fixo da OP 2.0, em ordem alfabética."""
    nomes = set()
    for grupo in OP2_ESTRUTURA_OPCOES.values():
        for valor in grupo.get('campos', {}).get('malha', []):
            if valor not in ('N/A', 'OUTRO'):
                nomes.add(valor)
    return sorted(nomes)


class Command(BaseCommand):
    help = 'Cadastra como tecido (sem estoque) cada malha do catálogo fixo da OP 2.0 ainda não cadastrada.'

    def add_arguments(self, parser):
        parser.add_argument('--filial-id', type=int, required=True)
        parser.add_argument('--empresa-alias', default='')
        parser.add_argument('--unidade-sigla', default='KG')
        parser.add_argument('--unidade-descricao', default='Quilograma')
        parser.add_argument('--confirmar', action='store_true')

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

        malhas = catalogo_malhas()
        existentes = {
            _normalizar(descricao)
            for descricao in Produto.objects.filter(filial=filial).values_list('descricao', flat=True)
        }
        novas = [m for m in malhas if _normalizar(m) not in existentes]
        puladas = [m for m in malhas if _normalizar(m) in existentes]

        confirmar = opcoes['confirmar']
        sigla = opcoes['unidade_sigla'].strip().upper()
        unidade = UnidadeMedida.objects.filter(empresa=filial.empresa, sigla=sigla).first()
        self.stdout.write(
            f"{'GRAVANDO' if confirmar else 'SIMULAÇÃO (nada será gravado)'} — {filial} · "
            f"unidade {sigla}{'' if unidade else ' (será criada)'}"
        )
        self.stdout.write(f'Catálogo de malha da OP: {len(malhas)} nomes · {len(novas)} novos · {len(puladas)} já existem')
        for m in malhas:
            self.stdout.write(f"  {'PULA (já existe)' if m in puladas else 'novo':<17} {m}")

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
            for nome in novas:
                criar_produto_materia_prima(
                    filial, {'nome': nome, 'unidade_medida': unidade}, 'Catálogo de malha da OP 2.0',
                )
        self.stdout.write(self.style.SUCCESS(f'{len(novas)} tecidos cadastrados (sem estoque).'))
