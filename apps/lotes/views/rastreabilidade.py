"""Rastreabilidade bidirecional de lotes."""
from django.db.models import Q
from django.shortcuts import render
from django.views import View

from apps.core.services.permissions import PermissaoRequiredMixin
from apps.estoque.models import LoteProduto, MovimentacaoEstoque


class LoteRastreabilidadeView(PermissaoRequiredMixin, View):
    permissao_modulo = 'estoque'
    template_name = 'lotes/rastreabilidade.html'

    def get(self, request):
        filial = request.filial_ativa
        busca = request.GET.get('q', '').strip()
        lote_selecionado = None
        contexto = {}

        lotes_encontrados = []
        if busca:
            lotes_encontrados = list(
                LoteProduto.objects.for_filial(filial)
                .filter(
                    Q(numero_lote__icontains=busca)
                    | Q(produto__descricao__icontains=busca)
                    | Q(produto__codigo__icontains=busca)
                )
                .select_related('produto', 'fornecedor')
                .order_by('data_validade', 'numero_lote')[:20]
            )
            if len(lotes_encontrados) == 1:
                lote_selecionado = lotes_encontrados[0]

        lote_pk = request.GET.get('lote')
        if lote_pk:
            try:
                lote_selecionado = (
                    LoteProduto.objects.for_filial(filial)
                    .select_related('produto', 'fornecedor')
                    .get(pk=lote_pk)
                )
            except LoteProduto.DoesNotExist:
                pass

        if lote_selecionado:
            contexto = self._montar_rastreio(lote_selecionado)

        return render(request, self.template_name, {
            'busca': busca,
            'lotes_encontrados': lotes_encontrados,
            'lote': lote_selecionado,
            **contexto,
        })

    def _montar_rastreio(self, lote):
        """
        As duas travessias, mais o que a tela já mostrava.

        `componentes_consumidos` CONTINUA SENDO A FICHA, e continua rotulado
        como BOM: serve para conferir a formulação contra o que saiu. O que
        ele nunca foi é resposta de recall -- a ficha diz "manga", e o recall
        precisa do lote e do produtor. Essa resposta agora vem do
        `RastreioService`, ao lado, em vez de no lugar.
        """
        from apps.lotes.models import InspecaoLote
        from apps.lotes.services.rastreio import RastreioService

        origem = RastreioService.de_onde_veio(lote)
        destino = RastreioService.para_onde_foi(lote)

        # O primeiro elo é o próprio lote; a tela já o mostra no cabeçalho.
        elo_raiz = origem[0] if origem else None
        ordem_producao = elo_raiz.ordem if elo_raiz else None
        item_entrada = elo_raiz.entrada if elo_raiz else None
        recebimento = elo_raiz.recebimento if elo_raiz else None

        apontamentos = []
        componentes_consumidos = []
        if ordem_producao is not None:
            try:
                from apps.producao.models import ApontamentoProducao
                apontamentos = list(
                    ApontamentoProducao.objects
                    .filter(ordem_producao=ordem_producao)
                    .select_related('operador')
                    .order_by('data_hora_inicio')
                )
            except Exception:  # noqa: BLE001
                apontamentos = []
            if ordem_producao.ficha_tecnica_id:
                componentes_consumidos = list(
                    ordem_producao.ficha_tecnica.itens
                    .select_related('materia_prima')
                    .all()
                )

        inspecoes = list(
            InspecaoLote.objects
            .filter(lote=lote)
            .select_related('responsavel')
            .order_by('-data_inspecao')
        )

        movimentacoes = list(
            MovimentacaoEstoque.objects
            .filter(lote=lote)
            .select_related('usuario')
            .order_by('-created_at')[:30]
        )

        itens_separacao = [e.separacao for e in destino if e.separacao is not None]
        resumo = RastreioService.resumo(origem, destino)

        return {
            'origem': origem,
            'destino': destino,
            'resumo': resumo,
            # A lista de clientes vem do resumo agora, e não de um agrupamento
            # próprio da view: a mesma pergunta respondida em dois lugares é
            # a que diverge no dia em que alguém corrige um dos dois.
            'clientes_atendidos': resumo['clientes'],
            'item_entrada': item_entrada,
            'recebimento': recebimento,
            'ordem_producao': ordem_producao,
            'apontamentos': apontamentos,
            'componentes_consumidos': componentes_consumidos,
            'inspecoes': inspecoes,
            'movimentacoes': movimentacoes,
            'itens_separacao': itens_separacao,
        }
