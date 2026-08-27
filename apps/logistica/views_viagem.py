"""Telas da viagem: lista, criação, edição e mudança de etapa."""
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from django.core.exceptions import ValidationError

from apps.core.services.exceptions import DadosInvalidosError
from apps.core.services.permissions import PermissaoRequiredMixin
from apps.logistica.forms_carga import PERFIS, ItemCargaForm
from apps.logistica.forms_viagem import ViagemForm
from apps.cadastros.models import Cliente
from apps.financeiro.constants.enums import StatusDocumentoFiscal
from apps.financeiro.models.fiscal import DocumentoFiscal
from apps.fiscal.models import NaturezaOperacao
from apps.fiscal.services.natureza_operacao_service import NaturezaOperacaoService
from apps.produtos.models import Produto
from apps.logistica.models import ItemCarga, Viagem
from apps.vendas.models.pedido import PedidoVenda
from apps.logistica.services.remessa_nfe import RemessaVendaForaService
from apps.logistica.services.vendas_para_carga import (
    CARREGAVEIS, VendasParaCargaService,
)
from apps.logistica.services.viagem import ViagemService


def _filial(request):
    return request.filial_ativa


class ViagemListView(PermissaoRequiredMixin, View):
    permissao_modulo = 'logistica'
    template_name = 'logistica/viagem/list.html'

    def get(self, request):
        filial = _filial(request)
        busca = (request.GET.get('q') or '').strip()
        status = (request.GET.get('status') or '').strip()

        viagens = (
            Viagem.objects.for_filial(filial)
            .select_related('motorista', 'veiculo', 'responsavel', 'vendedor')
            .annotate(qtd_itens=Count('itens'))
            # ORDEM EXPLICITA: `annotate` derruba a ordenacao do Meta, e paginar
            # sem ordem deixa a mesma viagem aparecer duas vezes entre paginas.
            .order_by('-data_saida', '-numero')
        )
        if busca:
            viagens = viagens.filter(
                Q(numero__icontains=busca)
                | Q(motorista_nome__icontains=busca)
                | Q(veiculo_placa__icontains=busca)
                | Q(rota__icontains=busca)
            )
        if status:
            viagens = viagens.filter(status=status)

        pagina = Paginator(viagens, 30).get_page(request.GET.get('page'))
        return render(request, self.template_name, {
            'title': 'Viagens',
            'viagens': pagina.object_list,
            'page_obj': pagina,
            'busca': busca,
            'status_filtro': status,
            'status_choices': Viagem.Status.choices,
            'kpis': cls_kpis(filial),
            'pode_agir': request.user.tem_permissao('logistica', 'criar'),
        })


def cls_kpis(filial) -> dict:
    """Os números que a lista mostra no topo."""
    base = Viagem.objects.for_filial(filial)
    na_estrada = base.filter(status__in=(
        Viagem.Status.EM_TRANSITO, Viagem.Status.EM_VENDAS, Viagem.Status.RETORNANDO,
    ))
    return {
        'total': base.count(),
        'na_estrada': na_estrada.count(),
        'aguardando_documentos': base.filter(
            status=Viagem.Status.AGUARDANDO_DOCUMENTOS,
        ).count(),
        'aguardando_conferencia': base.filter(
            status=Viagem.Status.AGUARDANDO_CONFERENCIA,
        ).count(),
        'em_poder': base.aggregate(
            total=Sum('saldos__quantidade_remetida'),
        )['total'] or 0,
    }


class ViagemCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'logistica'
    permissao_acao = 'criar'
    template_name = 'logistica/viagem/form.html'

    def get(self, request):
        filial = _filial(request)
        return render(request, self.template_name, {
            'title': 'Nova Viagem',
            'form': ViagemForm(filial=filial, initial={
                'uf_origem': (getattr(filial, 'uf', '') or '').upper(),
                'responsavel': request.user.pk,
            }),
            # O NUMERO E' MOSTRADO, NAO PEDIDO: numero repetido bate na unique
            # depois de a pessoa ja' ter preenchido tudo.
            'proximo_numero': ViagemService.proximo_numero(filial),
            'cancel_url': reverse('logistica:viagem-list'),
        })

    def post(self, request):
        filial = _filial(request)
        form = ViagemForm(request.POST, filial=filial)
        if form.is_valid():
            viagem = form.save(commit=False)
            viagem.filial = filial
            viagem.numero = ViagemService.proximo_numero(filial)
            if not viagem.responsavel_id:
                viagem.responsavel = request.user
            viagem.save()
            messages.success(request, f'Viagem #{viagem.numero:06d} criada.')
            return redirect('logistica:viagem-detail', pk=viagem.pk)
        messages.error(request, 'Revise os dados da viagem.')
        return render(request, self.template_name, {
            'title': 'Nova Viagem',
            'form': form,
            'proximo_numero': ViagemService.proximo_numero(filial),
            'cancel_url': reverse('logistica:viagem-list'),
        })


class ViagemUpdateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'logistica'
    permissao_acao = 'editar'
    template_name = 'logistica/viagem/form.html'

    def _viagem(self, request, pk):
        return get_object_or_404(Viagem.objects.for_filial(_filial(request)), pk=pk)

    def get(self, request, pk):
        viagem = self._viagem(request, pk)
        return render(request, self.template_name, {
            'title': f'Viagem #{viagem.numero:06d}',
            'form': ViagemForm(instance=viagem, filial=_filial(request)),
            'viagem': viagem,
            'proximo_numero': viagem.numero,
            'cancel_url': reverse('logistica:viagem-detail', args=[viagem.pk]),
        })

    def post(self, request, pk):
        viagem = self._viagem(request, pk)
        form = ViagemForm(request.POST, instance=viagem, filial=_filial(request))
        if form.is_valid():
            form.save()
            messages.success(request, 'Viagem atualizada.')
            return redirect('logistica:viagem-detail', pk=viagem.pk)
        messages.error(request, 'Revise os dados da viagem.')
        return render(request, self.template_name, {
            'title': f'Viagem #{viagem.numero:06d}',
            'form': form, 'viagem': viagem, 'proximo_numero': viagem.numero,
            'cancel_url': reverse('logistica:viagem-detail', args=[viagem.pk]),
        })


class ViagemDetailView(PermissaoRequiredMixin, View):
    permissao_modulo = 'logistica'
    template_name = 'logistica/viagem/detail.html'

    def get(self, request, pk):
        viagem = get_object_or_404(
            Viagem.objects.for_filial(_filial(request))
            .select_related('motorista', 'veiculo', 'responsavel', 'vendedor'),
            pk=pk,
        )
        return render(request, self.template_name, {
            'title': f'Viagem #{viagem.numero:06d}',
            'viagem': viagem,
            'resumo': ViagemService.resumo(viagem),
            'itens': viagem.itens.select_related('natureza', 'produto', 'cliente'),
            'entregas': ViagemService.entregas_por_cliente(viagem),
            'conciliacao': ViagemService.conciliacao(viagem),
            'proximos_status': viagem.proximos_status(),
            'remessa': DocumentoFiscal.objects.filter(
                origem_tipo='viagem_remessa', origem_id=viagem.pk,
            ).exclude(status=StatusDocumentoFiscal.CANCELADA).first(),
            'pendencias_remessa': (
                RemessaVendaForaService.conferir(viagem)
                if RemessaVendaForaService.itens_da_viagem(viagem) else []
            ),
            # UM FORMULARIO POR BOTAO. Cada operacao pergunta coisas
            # diferentes; um so' com seletor de natureza obrigaria quem monta a
            # carga a pensar em CFOP no meio do carregamento.
            'formularios_carga': [
                {
                    'especie': especie,
                    'perfil': PERFIS[especie],
                    'form': ItemCargaForm(viagem=viagem, especie=especie),
                }
                for especie in (
                    NaturezaOperacao.Especie.VENDA,
                    NaturezaOperacao.Especie.REMESSA_VENDA_FORA,
                    NaturezaOperacao.Especie.BONIFICACAO,
                )
            ] if viagem.editavel else [],
            'pendencias': (
                ViagemService.conferir_antes_de_fechar(viagem)
                if viagem.editavel else []
            ),
            'pode_agir': request.user.tem_permissao('logistica', 'editar'),
        })


class ViagemMudarStatusView(PermissaoRequiredMixin, View):
    permissao_modulo = 'logistica'
    permissao_acao = 'editar'

    def post(self, request, pk):
        viagem = get_object_or_404(Viagem.objects.for_filial(_filial(request)), pk=pk)
        volta = redirect('logistica:viagem-detail', pk=viagem.pk)
        try:
            ViagemService.mudar_status(
                viagem, (request.POST.get('status') or '').strip(), usuario=request.user,
            )
        except DadosInvalidosError as erro:
            messages.error(request, str(erro))
            return volta
        messages.success(request, f'Viagem em {viagem.get_status_display().lower()}.')
        return volta


class ViagemFecharCargaView(PermissaoRequiredMixin, View):
    permissao_modulo = 'logistica'
    permissao_acao = 'editar'

    def post(self, request, pk):
        viagem = get_object_or_404(Viagem.objects.for_filial(_filial(request)), pk=pk)
        volta = redirect('logistica:viagem-detail', pk=viagem.pk)
        try:
            ViagemService.fechar_carga(viagem, usuario=request.user)
        except DadosInvalidosError as erro:
            messages.error(request, str(erro))
            return volta
        messages.success(
            request,
            'Carga fechada: a mercadoria saiu do estoque e os documentos '
            'estão pendentes de emissão.',
        )
        return volta


class ViagemCancelarView(PermissaoRequiredMixin, View):
    permissao_modulo = 'logistica'
    permissao_acao = 'editar'

    def post(self, request, pk):
        viagem = get_object_or_404(Viagem.objects.for_filial(_filial(request)), pk=pk)
        volta = redirect('logistica:viagem-detail', pk=viagem.pk)
        try:
            ViagemService.cancelar(viagem, motivo=(request.POST.get('motivo') or '').strip())
        except DadosInvalidosError as erro:
            messages.error(request, str(erro))
            return volta
        messages.success(request, 'Viagem cancelada.')
        return volta


class ViagemItemCreateView(PermissaoRequiredMixin, View):
    """Põe uma linha na carga, com a natureza que o botão escolheu."""

    permissao_modulo = 'logistica'
    permissao_acao = 'editar'

    def post(self, request, pk, especie):
        viagem = get_object_or_404(Viagem.objects.for_filial(_filial(request)), pk=pk)
        volta = redirect('logistica:viagem-detail', pk=viagem.pk)
        if especie not in PERFIS:
            messages.error(request, 'Operação desconhecida para a carga.')
            return volta

        form = ItemCargaForm(request.POST, viagem=viagem, especie=especie)
        if not form.is_valid():
            # O ERRO PRECISA DIZER QUAL CAMPO. "Revise os dados" manda a pessoa
            # procurar sozinha no formulario que ela nem tem mais na tela.
            detalhe = '; '.join(
                f'{form.fields[campo].label or campo}: {erros[0]}'
                for campo, erros in form.errors.items() if campo in form.fields
            ) or ' '.join(form.non_field_errors())
            messages.error(request, f'Item não adicionado. {detalhe}'.strip())
            return volta

        try:
            ViagemService.adicionar_item(viagem, {
                'natureza': form.cleaned_data['natureza'],
                'produto': form.cleaned_data['produto'],
                'lote': form.cleaned_data.get('lote'),
                'cliente': form.cleaned_data.get('cliente'),
                'pedido_venda': form.cleaned_data.get('pedido_venda'),
                'quantidade': form.cleaned_data['quantidade'],
                'valor_unitario': form.cleaned_data.get('valor_unitario') or 0,
                'peso_kg': form.cleaned_data.get('peso_kg') or 0,
                'observacao': form.cleaned_data.get('observacao') or '',
            })
        except (DadosInvalidosError, ValidationError) as erro:
            messages.error(request, str(getattr(erro, 'message', erro)))
            return volta

        messages.success(
            request, f'{PERFIS[especie]["titulo"]}: item incluído na carga.',
        )
        return volta


class ViagemItemDeleteView(PermissaoRequiredMixin, View):
    permissao_modulo = 'logistica'
    permissao_acao = 'editar'

    def post(self, request, pk, item_pk):
        viagem = get_object_or_404(Viagem.objects.for_filial(_filial(request)), pk=pk)
        item = get_object_or_404(ItemCarga.objects.filter(viagem=viagem), pk=item_pk)
        volta = redirect('logistica:viagem-detail', pk=viagem.pk)
        try:
            ViagemService.remover_item(viagem, item)
        except DadosInvalidosError as erro:
            messages.error(request, str(erro))
            return volta
        messages.success(request, 'Item removido da carga.')
        return volta


class ViagemVendasView(PermissaoRequiredMixin, View):
    """
    O seletor de vendas já realizadas.

    ESCOLHE-SE A VENDA, NÃO O PRODUTO. Redigitar produto e quantidade para
    mercadoria que já foi vendida é uma chance de a carga sair diferente do que
    o cliente comprou.
    """

    permissao_modulo = 'logistica'
    permissao_acao = 'editar'
    template_name = 'logistica/viagem/vendas.html'

    def _viagem(self, request, pk):
        return get_object_or_404(Viagem.objects.for_filial(_filial(request)), pk=pk)

    def get(self, request, pk):
        viagem = self._viagem(request, pk)
        busca = (request.GET.get('q') or '').strip()
        return render(request, self.template_name, {
            'title': f'Adicionar vendas — Viagem #{viagem.numero:06d}',
            'viagem': viagem,
            'vendas': VendasParaCargaService.disponiveis(
                _filial(request), busca=busca, viagem=viagem,
            ),
            'busca': busca,
            'cancel_url': reverse('logistica:viagem-detail', args=[viagem.pk]),
        })

    def post(self, request, pk):
        viagem = self._viagem(request, pk)
        volta = redirect('logistica:viagem-detail', pk=viagem.pk)

        # OS PEDIDOS SAO BUSCADOS PELA FILIAL, e nao so' pelos ids do
        # formulario: id colado a mao carregaria venda de outra unidade, e o
        # caminhao sairia com mercadoria que nao e' desta casa.
        ids = [i for i in request.POST.getlist('pedidos') if i.isdigit()]
        pedidos = list(
            PedidoVenda.objects
            .filter(filial=_filial(request), pk__in=ids, status__in=CARREGAVEIS)
            .select_related('cliente').prefetch_related('itens__produto')
        )
        try:
            criados = VendasParaCargaService.adicionar_vendas(viagem, pedidos)
        except (DadosInvalidosError, ValidationError) as erro:
            messages.error(request, str(getattr(erro, 'message', erro)))
            return volta
        messages.success(
            request,
            f'{len(pedidos)} venda(s) na carga — {criados} item(ns) incluído(s).',
        )
        return volta


class ViagemTratamentoFiscalJsonView(PermissaoRequiredMixin, View):
    """
    O que vai sair na nota deste item, antes de ele entrar na carga.

    MOSTRAR ANTES, E NÃO DEPOIS. O CFOP não é digitado — vem da parametrização
    da natureza — mas quem monta a carga precisa VER qual saiu, e com que CST.
    Descobrir que a regra estava errada na hora de transmitir é tarde: a
    mercadoria já saiu do estoque e o caminhão está esperando.

    Também é aqui que a falta de regra aparece cedo, com o texto que diz onde
    cadastrar.
    """

    permissao_modulo = 'logistica'

    def get(self, request, pk):
        filial = _filial(request)
        viagem = get_object_or_404(Viagem.objects.for_filial(filial), pk=pk)

        produto = (
            Produto.objects.for_filial(filial)
            .filter(pk=request.GET.get('produto') or 0).first()
        )
        natureza = (
            NaturezaOperacao.objects.for_filial(filial)
            .filter(pk=request.GET.get('natureza') or 0).first()
        )
        if produto is None or natureza is None:
            return JsonResponse({'ok': False, 'erro': 'Escolha o produto.'})

        cliente = (
            Cliente.objects.for_filial(filial)
            .filter(pk=request.GET.get('cliente') or 0).first()
        )
        try:
            fiscal = NaturezaOperacaoService.para_item(
                natureza=natureza, filial=filial, produto=produto,
                cliente=cliente, data=viagem.data_saida,
            )
        except DadosInvalidosError as erro:
            return JsonResponse({'ok': False, 'erro': str(erro)})

        return JsonResponse({
            'ok': True,
            'produto': {
                'descricao': produto.descricao,
                'ncm': produto.ncm or '',
                'cest': produto.cest or '',
                'unidade': (
                    produto.unidade_medida.sigla if produto.unidade_medida_id else ''
                ),
                'preco_venda': str(produto.preco_venda or 0),
            },
            'fiscal': {
                'natureza': fiscal.natureza_operacao,
                'cfop': fiscal.cfop,
                'cst_icms': fiscal.cst_icms,
                'csosn': fiscal.csosn,
                'cst_pis': fiscal.cst_pis,
                'cst_cofins': fiscal.cst_cofins,
                'aliquota_icms': (
                    str(fiscal.aliquota_icms) if fiscal.aliquota_icms is not None else ''
                ),
                # A TRILHA DE POR QUE ESTA REGRA. Sem isso, "por que saiu 6910
                # nesta nota?" vira arqueologia de banco seis meses depois.
                'justificativa': fiscal.justificativa,
            },
        })


class ViagemEmitirRemessaView(PermissaoRequiredMixin, View):
    """Emite a NF-e de remessa para venda fora do estabelecimento."""

    permissao_modulo = 'logistica'
    permissao_acao = 'editar'

    def post(self, request, pk):
        viagem = get_object_or_404(Viagem.objects.for_filial(_filial(request)), pk=pk)
        volta = redirect('logistica:viagem-detail', pk=viagem.pk)
        try:
            documento = RemessaVendaForaService.emitir(viagem, usuario=request.user)
        except DadosInvalidosError as erro:
            messages.error(request, str(erro))
            return volta
        messages.success(
            request,
            f'Remessa {documento.numero}/{documento.serie} gerada e pendente '
            'de transmissão à SEFAZ.',
        )
        return volta
