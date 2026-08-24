"""
Apaga pedidos de produção e tudo que pende deles.

APAGAR PEDIDO NÃO É UM `delete()`. A cadeia tem seis níveis e quatro deles
são `PROTECT` — ordem, corte, expedição, inspeção e reserva travam a
exclusão do pedido. Um `PedidoProducao.objects.all().delete()` estoura com
`ProtectedError` no primeiro pedido que já virou OP, e o que estourar no
meio de uma limpeza manual deixa o banco pela metade.

DUAS COISAS NÃO VOLTAM SOZINHAS, e são o motivo de este comando existir em
vez de um `delete()` no shell:

  FINANCEIRO   o pedido gera `ContaReceber` ligada por `documento_id`, que
               é vínculo solto e não chave estrangeira. Apagar o pedido não
               apaga o título: ele fica órfão, apontando para um pedido que
               não existe — e continua entrando no DRE e no fluxo de caixa.
  ESTOQUE      a baixa do corte gravou `MovimentacaoEstoque`. Apagar o
               corte NÃO devolve o tecido: o saldo continua reduzido, agora
               sem nenhum documento que explique por quê.

  RESERVA      a reserva ativa segura `quantidade_reservada`. Apagar a
               linha deixa o material reservado para sempre, sem reserva
               que explique.

Com `--incluir-financeiro` e `--incluir-estoque` (ou `--tudo`) ele resolve
os três: apaga os títulos, devolve o tecido e libera as reservas ANTES de
apagar, usando os mesmos serviços das telas de estorno. Sem as bandeiras ele
avisa e deixa como está — desfazer estoque é decisão de quem opera.

NÃO APAGA NADA SEM `--confirmar`. Sem a bandeira ele só conta o que
apagaria. E exige `--filial`: uma limpeza que varre o banco inteiro é a que
alguém roda achando que está no ambiente de teste.

    manage.py limpar_pedidos_moda --filial 1              # ensaio
    manage.py limpar_pedidos_moda --filial 1 --tudo --confirmar
        --usuario voce@empresa.com.br
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.models import Filial
from apps.moda.models import (
    Expedicao, Inspecao, OrdemProducao, PedidoProducao, RegistroCorte,
    ReservaMaterial,
)

# A ordem IMPORTA: cada um destes segura o de baixo com `PROTECT`. Invertê-la
# é o mesmo que não ter comando nenhum -- estoura no primeiro.
ETAPAS = (
    ('Inspeções de qualidade', Inspecao, 'ordem__pedido__filial'),
    ('Registros de corte', RegistroCorte, 'ordem__pedido__filial'),
    ('Expedições', Expedicao, 'ordem__pedido__filial'),
    ('Reservas de material', ReservaMaterial, 'ordem__pedido__filial'),
    ('Ordens de produção', OrdemProducao, 'pedido__filial'),
    ('Pedidos', PedidoProducao, 'filial'),
)


class Command(BaseCommand):
    help = 'Apaga os pedidos de produção de uma filial e tudo que depende deles.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--filial', type=int, required=True,
            help='ID da filial. Obrigatório: limpeza sem escopo é a que '
                 'alguém roda achando que está no ambiente de teste.',
        )
        parser.add_argument(
            '--confirmar', action='store_true',
            help='Apaga de verdade. Sem isto, o comando só conta.',
        )
        parser.add_argument(
            '--incluir-financeiro', action='store_true',
            help='Apaga também as contas a receber geradas pelos pedidos. '
                 'Sem isto elas ficam órfãs e continuam no DRE.',
        )
        parser.add_argument(
            '--incluir-estoque', action='store_true',
            help='Devolve ao estoque o que os cortes baixaram e libera as '
                 'reservas, e depois apaga as movimentações. Sem isto o '
                 'saldo fica errado para sempre.',
        )
        parser.add_argument(
            '--usuario',
            help='E-mail de quem responde pelo estorno. Obrigatório com '
                 '--incluir-estoque: a movimentação de estoque é atribuída '
                 'a alguém, e forjar essa atribuição seria pior que exigir.',
        )
        parser.add_argument(
            '--tudo', action='store_true',
            help='Atalho para --incluir-financeiro --incluir-estoque. '
                 'É o que se quer quando o banco era só teste.',
        )

    def handle(self, *args, **opcoes):
        filial = self._filial(opcoes['filial'])
        confirmar = opcoes['confirmar']
        # `--tudo` liga os dois: é o que se quer quando o banco era só
        # teste, e obrigar a lembrar de duas bandeiras é como se esquece uma.
        if opcoes['tudo']:
            opcoes['incluir_financeiro'] = True
            opcoes['incluir_estoque'] = True

        # O estorno grava movimentação, e movimentação tem dono. Pedir o
        # usuário e' mais honesto que carimbar o primeiro admin que aparecer:
        # o estorno vai aparecer no historico com o nome de alguem.
        usuario = None
        if opcoes['incluir_estoque']:
            usuario = self._usuario(opcoes.get('usuario'))

        self.stdout.write(f'Filial: {filial} (id={filial.pk})')
        self.stdout.write('')

        contagens = [
            (rotulo, modelo, modelo.objects.filter(**{campo: filial}).count())
            for rotulo, modelo, campo in ETAPAS
        ]
        titulos = self._titulos(filial)

        for rotulo, _, quantidade in contagens:
            self.stdout.write(f'  {rotulo:26} {quantidade:>6}')
        self.stdout.write(
            f'  {"Contas a receber geradas":26} {titulos.count():>6}'
            + ('' if opcoes['incluir_financeiro'] else '   (ficam órfãs)')
        )
        self.stdout.write('')

        if not any(q for _, _, q in contagens):
            self.stdout.write(self.style.SUCCESS('Nada a apagar nesta filial.'))
            return

        self._avisar_estoque(filial, opcoes['incluir_estoque'])

        if not confirmar:
            self.stdout.write(self.style.WARNING(
                'ENSAIO — nada foi apagado. Repita com --confirmar para valer.'
            ))
            return

        # Tudo numa transação: uma limpeza que morre no meio deixa ordem sem
        # pedido e expedição sem ordem, que é pior que não ter limpado.
        with transaction.atomic():
            # O ESTOQUE VEM PRIMEIRO, enquanto corte e reserva ainda existem:
            # depois de apagados não há mais de onde saber quanto devolver.
            if opcoes['incluir_estoque']:
                self._devolver_estoque(filial, usuario)

            if opcoes['incluir_financeiro']:
                apagados, _ = titulos.delete()
                self.stdout.write(f'  Contas a receber apagadas: {apagados}')

            for rotulo, modelo, campo in ETAPAS:
                apagados, _ = modelo.objects.filter(**{campo: filial}).delete()
                self.stdout.write(f'  {rotulo:26} apagados: {apagados}')

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Limpeza concluída.'))

    # ── Apoio ────────────────────────────────────────────────────────────

    @staticmethod
    def _filial(pk):
        try:
            return Filial.objects.get(pk=pk)
        except Filial.DoesNotExist:
            raise CommandError(f'Filial {pk} não existe.')

    @staticmethod
    def _usuario(email):
        from apps.core.models import Usuario

        if not email:
            raise CommandError(
                '--incluir-estoque exige --usuario <email>: o estorno grava '
                'uma movimentação de estoque, e ela é atribuída a alguém.'
            )
        try:
            return Usuario.objects.get(email=email)
        except Usuario.DoesNotExist:
            raise CommandError(f'Usuário {email} não existe.')

    @staticmethod
    def _titulos(filial):
        """
        As contas a receber que os pedidos desta filial geraram.

        O vínculo é solto (`documento_tipo` + `documento_id`), então a busca
        é pelos IDs dos pedidos — não há chave estrangeira que o banco
        pudesse seguir sozinho.
        """
        from apps.financeiro.models import ContaReceber
        from apps.moda.services.financeiro import FinanceiroPedidoService

        ids = list(
            PedidoProducao.objects.filter(filial=filial).values_list('pk', flat=True)
        )
        return ContaReceber.objects.filter(
            documento_tipo=FinanceiroPedidoService.DOCUMENTO_TIPO,
            documento_id__in=ids,
        )

    @staticmethod
    def _cortes_baixados(filial):
        return RegistroCorte.objects.filter(
            ordem__pedido__filial=filial, estoque_baixado_em__isnull=False,
        )

    @staticmethod
    def _reservas_ativas(filial):
        return ReservaMaterial.objects.filter(
            ordem__pedido__filial=filial,
            status=ReservaMaterial.Status.ATIVA,
        )

    def _avisar_estoque(self, filial, vai_devolver):
        """
        O estoque NÃO se conserta sozinho ao apagar corte e reserva.

        São dois buracos diferentes e nenhum tem chave estrangeira para cá:
        o corte baixado reduziu `quantidade_atual`, e a reserva ativa segura
        `quantidade_reservada`. Apagar as linhas deixa os dois saldos errados
        sem documento que explique — e é melhor a pessoa saber disso antes de
        digitar `--confirmar` do que descobrir no próximo inventário.
        """
        baixados = self._cortes_baixados(filial).count()
        reservas = self._reservas_ativas(filial).count()
        if not baixados and not reservas:
            return

        partes = []
        if baixados:
            partes.append(f'{baixados} corte(s) já baixaram tecido')
        if reservas:
            partes.append(f'{reservas} reserva(s) ativa(s) seguram material')

        if vai_devolver:
            self.stdout.write(self.style.SUCCESS(
                f'Estoque: {" e ".join(partes)}.\n'
                '  Com --incluir-estoque, o comando devolve tudo antes de\n'
                '  apagar e deixa o saldo como estava.'
            ))
        else:
            self.stdout.write(self.style.WARNING(
                f'ATENÇÃO: {" e ".join(partes)}.\n'
                '  Apagar NÃO devolve o tecido nem libera a reserva — os\n'
                '  saldos ficam errados, e sem documento que explique.\n'
                '  Use --incluir-estoque (ou --tudo) para devolver antes.'
            ))
        self.stdout.write('')

    def _devolver_estoque(self, filial, usuario):
        """
        Devolve o que os cortes baixaram e libera as reservas.

        Usa os MESMOS serviços das telas (estorno do corte e cancelamento da
        reserva), e não um `UPDATE` no saldo: eles conhecem custo médio,
        tolerância e trava de baixa dupla, e reescrever essa conta aqui daria
        um saldo que só este comando entende.

        Depois de devolver, as movimentações do par (saída + entrada) são
        apagadas: elas existem para explicar um documento que está sendo
        apagado junto, e sozinhas viram ruído no extrato do produto.
        """
        from apps.estoque.models.estoque import MovimentacaoEstoque

        from apps.moda.services.integracao import IntegracaoService
        from apps.moda.services.necessidade import NecessidadeService

        ordens = list(
            OrdemProducao.objects.filter(pedido__filial=filial)
            .values_list('pk', flat=True)
        )

        estornados = 0
        for corte in self._cortes_baixados(filial).select_related('ordem'):
            IntegracaoService.estornar_estoque_do_corte(corte, usuario=usuario)
            estornados += 1
        if estornados:
            self.stdout.write(f'  Cortes estornados (tecido devolvido): {estornados}')

        liberadas = 0
        for reserva in self._reservas_ativas(filial):
            NecessidadeService.cancelar_reserva(reserva, usuario=usuario)
            liberadas += 1
        if liberadas:
            self.stdout.write(f'  Reservas liberadas: {liberadas}')

        if ordens:
            apagadas, _ = MovimentacaoEstoque.objects.filter(
                documento_tipo=MovimentacaoEstoque.DocumentoTipo.ORDEM_PRODUCAO,
                documento_id__in=ordens, filial=filial,
            ).delete()
            if apagadas:
                self.stdout.write(f'  Movimentações de estoque apagadas: {apagadas}')
