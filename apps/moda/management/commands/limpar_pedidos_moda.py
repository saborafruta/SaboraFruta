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

O comando cuida do primeiro (com `--incluir-financeiro`) e AVISA sobre o
segundo, porque desfazer movimentação de estoque é decisão de quem opera,
não de um comando de limpeza.

NÃO APAGA NADA SEM `--confirmar`. Sem a bandeira ele só conta o que
apagaria. E exige `--filial`: uma limpeza que varre o banco inteiro é a que
alguém roda achando que está no ambiente de teste.

    manage.py limpar_pedidos_moda --filial 1
    manage.py limpar_pedidos_moda --filial 1 --confirmar --incluir-financeiro
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

    def handle(self, *args, **opcoes):
        filial = self._filial(opcoes['filial'])
        confirmar = opcoes['confirmar']

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

        self._avisar_estoque(filial)

        if not confirmar:
            self.stdout.write(self.style.WARNING(
                'ENSAIO — nada foi apagado. Repita com --confirmar para valer.'
            ))
            return

        # Tudo numa transação: uma limpeza que morre no meio deixa ordem sem
        # pedido e expedição sem ordem, que é pior que não ter limpado.
        with transaction.atomic():
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

    def _avisar_estoque(self, filial):
        """
        O tecido baixado pelo corte NÃO volta ao apagar o corte.

        A movimentação vive no app de estoque e não tem chave estrangeira
        para cá. Apagar o corte deixa o saldo reduzido sem documento que
        explique — e é melhor a pessoa saber disso antes de digitar
        `--confirmar` do que descobrir no próximo inventário.
        """
        baixados = RegistroCorte.objects.filter(
            ordem__pedido__filial=filial, estoque_baixado_em__isnull=False,
        ).count()
        if not baixados:
            return
        self.stdout.write(self.style.WARNING(
            f'ATENÇÃO: {baixados} corte(s) já baixaram tecido do estoque.\n'
            '  Apagar o corte NÃO devolve o tecido — o saldo continua\n'
            '  reduzido, e sem documento que explique. Se quiser o estoque\n'
            '  de volta, estorne os cortes ANTES (botão de estorno na tela\n'
            '  do corte) e só depois rode este comando.'
        ))
        self.stdout.write('')
