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
from apps.estoque.models import LoteProduto
from apps.financeiro.models import CondicaoPagamento, FormaPagamento
from apps.logistica.models import (
    EntregaBonificacao, ItemCarga, VendaViagem, Viagem,
)
from apps.vendas.models.pedido import PedidoVenda
from apps.logistica.services.estoque_transito import EstoqueEmTransitoService
from apps.logistica.services.remessa_nfe import RemessaVendaForaService
from apps.logistica.services.vendas_para_carga import (
    CARREGAVEIS, VendasParaCargaService,
)
from apps.logistica.services.bonificacao_nfe import BonificacaoNFeService
from apps.logistica.services.estoque_viagem import EstoqueViagemService
from apps.logistica.services.entrega_bonificacao import (
    EntregaBonificacaoService,
)
from apps.logistica.services.mdfe_viagem import MDFeViagemService
from apps.logistica.services.historico_bonificacao import (
    HistoricoBonificacaoService,
)
from apps.logistica.services.rastreabilidade import RastreabilidadeService
from apps.logistica.services.retorno_nfe import RetornoVendaForaService
from apps.logistica.services.retorno_viagem import RetornoViagemService
from apps.logistica.services.venda_fora_nfe import VendaForaNFeService
from apps.logistica.services.vinculo_remessa import VinculoRemessaService
from apps.logistica.services.venda_viagem import (
    VIAGENS_QUE_VENDEM as VIAGENS_QUE_ENTREGAM, VendaViagemService,
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
        vinculos = VinculoRemessaService.linhas(viagem)
        quadro = EstoqueViagemService.quadro(viagem)
        bonificacoes = HistoricoBonificacaoService.linhas(viagem)
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
            'vendas_viagem': viagem.vendas.select_related('cliente', 'pedido_venda')
                .prefetch_related('itens__produto'),
            'resumo_vendas': VendaViagemService.resumo(viagem),
            # A CADEIA INTEIRA NUMA TABELA: remessa -> viagem -> produto ->
            # venda -> nota da venda. Cada elo ja' existia guardado em
            # algum lugar; o que faltava era le-los juntos.
            # ONDE FOI PARAR CADA UNIDADE que subiu no caminhao -- a
            # pergunta que so' existe depois que a viagem anda.
            'quadro': quadro,
            'pendencias_quadro': EstoqueViagemService.pendencias(quadro),
            'vinculos': vinculos,
            # O HISTORICO DA CORTESIA: o que saiu de graca, para quem, sob
            # que nota -- e ONDE a baixa de estoque aconteceu, que e'
            # diferente entre a bonificacao da carga e a da rua.
            'bonificacoes': bonificacoes,
            'resumo_bonificacoes': HistoricoBonificacaoService.resumo(bonificacoes),
            # Os motivos ficam no MESMO formulario do botao que recusa: pedi-los
            # depois faria metade das recusas ficar sem explicacao.
            'motivos_nao_entrega': EntregaBonificacao.MotivoNaoEntrega.choices,
            'resumo_vinculos': VinculoRemessaService.resumo(vinculos),
            # OS BOTOES DA RUA so' aparecem enquanto o caminhao esta' fora:
            # antes de sair nao ha' saldo, e depois de encerrar a viagem
            # ja' prestou contas.
            'pode_entregar': viagem.status in VIAGENS_QUE_ENTREGAM,
            # O RETORNO E' O OUTRO LADO DA REMESSA: enquanto ele nao tem
            # nota, existe um documento dizendo que a mercadoria saiu e nada
            # dizendo que ela voltou.
            'retorno': RetornoVendaForaService.nota_da_viagem(viagem),
            'itens_retornados': RetornoVendaForaService.itens_do_retorno(viagem),
            'pendencias_retorno': (
                RetornoVendaForaService.conferir(viagem)
                if RetornoVendaForaService.itens_do_retorno(viagem) else []
            ),
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


class EstoqueEmTransitoView(PermissaoRequiredMixin, View):
    """
    O que saiu por remessa e ainda está na rua.

    Sem esta tela a mercadoria "some" do sistema entre a saída e o retorno, e a
    única forma de saber onde ela está é abrir viagem por viagem.
    """

    permissao_modulo = 'logistica'
    template_name = 'logistica/viagem/estoque_transito.html'

    def get(self, request):
        filial = _filial(request)
        busca = (request.GET.get('q') or '').strip()
        return render(request, self.template_name, {
            'title': EstoqueEmTransitoService.NOME,
            'linhas': EstoqueEmTransitoService.por_produto(filial, busca=busca),
            'resumo': EstoqueEmTransitoService.resumo(filial),
            'busca': busca,
        })


class ViagemVendaCreateView(PermissaoRequiredMixin, View):
    """
    Nova entrega durante a viagem: venda ou bonificação.

    O SALDO DA CARGA É O LIMITE, e quem cobra isso é o serviço -- a venda
    também pode chegar por outro caminho, e a regra tem que valer em todos.

    BONIFICAÇÃO ENTRA PELA MESMA TELA porque é a mesma entrega: mesmo
    cliente, mesmo saldo, mesmos itens. Uma tela separada obrigaria quem está
    na rua a decidir por qual porta entrar antes de saber o que vai fazer.
    """

    permissao_modulo = 'logistica'
    permissao_acao = 'editar'
    template_name = 'logistica/viagem/venda.html'

    def _viagem(self, request, pk):
        return get_object_or_404(Viagem.objects.for_filial(_filial(request)), pk=pk)

    def get(self, request, pk):
        viagem = self._viagem(request, pk)
        filial = _filial(request)
        tipo = request.GET.get('tipo') or VendaViagem.Tipo.VENDA
        if tipo not in VendaViagem.Tipo.values:
            tipo = VendaViagem.Tipo.VENDA
        bonificacao = tipo == VendaViagem.Tipo.BONIFICACAO
        return render(request, self.template_name, {
            'title': (
                f'Nova bonificação — Viagem #{viagem.numero:06d}' if bonificacao
                else f'Nova venda — Viagem #{viagem.numero:06d}'
            ),
            'tipo': tipo,
            'bonificacao': bonificacao,
            'motivos': VendaViagem.Motivo.choices,
            # A NATUREZA DO TIPO, para a tela poder MOSTRAR o CFOP e o CST
            # que vao sair na nota antes de a entrega ser registrada.
            # Descobrir a regra errada depois e' descobrir com a mercadoria
            # ja' na mao do cliente.
            'natureza_fiscal': _natureza_do_tipo(filial, tipo),
            # PEDIDO RELACIONADO, quando existe: bonificacao de compensacao e
            # de campanha quase sempre respondem a uma venda anterior.
            'pedidos': (
                PedidoVenda.objects.filter(filial=filial)
                .select_related('cliente')
                .order_by('-data_emissao', '-numero_pedido')[:200]
            ),
            'viagem': viagem,
            'disponivel': VendaViagemService.disponivel_para_venda(viagem),
            'clientes': Cliente.objects.for_filial(filial).filter(ativo=True),
            'condicoes': CondicaoPagamento.objects.filter(
                empresa=filial.empresa, ativo=True,
            ),
            'formas': FormaPagamento.objects.filter(
                empresa=filial.empresa, ativo=True,
            ).filter(Q(filial=filial) | Q(filial__isnull=True)),
            'cancel_url': reverse('logistica:viagem-detail', args=[viagem.pk]),
        })

    def post(self, request, pk):
        viagem = self._viagem(request, pk)
        filial = _filial(request)
        volta = redirect('logistica:viagem-detail', pk=viagem.pk)

        def _um(modelo, campo, **extra):
            valor = (request.POST.get(campo) or '').strip()
            if not valor.isdigit():
                return None
            return modelo.objects.filter(pk=int(valor), **extra).first()

        try:
            venda = VendaViagemService.registrar(viagem, {
                'tipo': request.POST.get('tipo'),
                'motivo': request.POST.get('motivo'),
                'pedido_venda': _um(PedidoVenda, 'pedido_venda', filial=filial),
                'produto': _um(Produto, 'produto', filial=filial),
                'lote': _um(LoteProduto, 'lote', filial=filial),
                'cliente': _um(Cliente, 'cliente', filial=filial),
                'cliente_nome': request.POST.get('cliente_nome'),
                'cliente_documento': request.POST.get('cliente_documento'),
                'endereco': request.POST.get('endereco'),
                'quantidade': request.POST.get('quantidade'),
                'valor_unitario': request.POST.get('valor_unitario'),
                'condicao_pagamento': _um(
                    CondicaoPagamento, 'condicao_pagamento', empresa=filial.empresa,
                ),
                'forma_pagamento': _um(
                    FormaPagamento, 'forma_pagamento', empresa=filial.empresa,
                ),
                'observacao': request.POST.get('observacao'),
            }, usuario=request.user)
        except DadosInvalidosError as erro:
            messages.error(request, str(erro))
            # VOLTA PARA O MESMO FORMULARIO: perder o tipo faria quem
            # registrava bonificacao reabrir a tela como venda.
            destino = reverse('logistica:viagem-venda-create', args=[viagem.pk])
            tipo = (request.POST.get('tipo') or '').strip()
            if tipo in VendaViagem.Tipo.values:
                destino = f'{destino}?tipo={tipo}'
            return redirect(destino)

        messages.success(
            request,
            f'{venda.get_tipo_display()} {venda.numero} registrada para '
            f'{venda.cliente_nome}. Saldo da carga atualizado.',
        )
        return volta


class ViagemVendaCancelarView(PermissaoRequiredMixin, View):
    permissao_modulo = 'logistica'
    permissao_acao = 'editar'

    def post(self, request, pk, venda_pk):
        viagem = get_object_or_404(Viagem.objects.for_filial(_filial(request)), pk=pk)
        venda = get_object_or_404(VendaViagem.objects.filter(viagem=viagem), pk=venda_pk)
        volta = redirect('logistica:viagem-detail', pk=viagem.pk)
        try:
            VendaViagemService.cancelar(
                venda, motivo=(request.POST.get('motivo') or '').strip(),
            )
        except DadosInvalidosError as erro:
            messages.error(request, str(erro))
            return volta
        messages.success(request, 'Venda cancelada e mercadoria devolvida ao saldo.')
        return volta


class ViagemVendaEmitirNFeView(PermissaoRequiredMixin, View):
    """
    Emite a NF-e da venda feita na rua.

    O BOTÃO FICA NA LINHA DA VENDA, e não numa tela fiscal separada: quem
    emite é quem acabou de vender, com o cliente esperando o documento na
    mão. Mandá-lo procurar a nota em outro lugar é como a venda sai sem nota.
    """

    permissao_modulo = 'logistica'
    permissao_acao = 'editar'

    def post(self, request, pk, venda_pk):
        viagem = get_object_or_404(Viagem.objects.for_filial(_filial(request)), pk=pk)
        venda = get_object_or_404(VendaViagem.objects.filter(viagem=viagem), pk=venda_pk)
        volta = redirect('logistica:viagem-detail', pk=viagem.pk)
        # CADA OPERACAO PELA SUA ROTINA. Bonificacao nao e' venda de valor
        # zero: ela tem natureza, arquivo e conferencia proprios -- inclusive
        # a exigencia do motivo, que a venda nao tem.
        rotina = (
            BonificacaoNFeService if venda.bonificacao else VendaForaNFeService
        )
        try:
            documento = rotina.emitir(venda, usuario=request.user)
        except DadosInvalidosError as erro:
            messages.error(request, str(erro))
            return volta
        messages.success(
            request,
            f'NF-e {documento.numero}/{documento.serie} '
            f'({venda.get_tipo_display().lower()} {venda.numero}) gerada e '
            'pendente de transmissão à SEFAZ.',
        )
        return volta


class ViagemEmitirRetornoView(PermissaoRequiredMixin, View):
    """
    Emite a NF-e de retorno do que não foi vendido.

    UMA POR VIAGEM, e por isso o botão fica na prestação de contas: é lá que
    se vê o que voltou, e é depois de conferir que a nota faz sentido.
    """

    permissao_modulo = 'logistica'
    permissao_acao = 'editar'

    def post(self, request, pk):
        viagem = get_object_or_404(Viagem.objects.for_filial(_filial(request)), pk=pk)
        volta = redirect('logistica:viagem-detail', pk=viagem.pk)
        try:
            documento = RetornoVendaForaService.emitir(viagem, usuario=request.user)
        except DadosInvalidosError as erro:
            messages.error(request, str(erro))
            return volta
        messages.success(
            request,
            f'Nota de retorno {documento.numero}/{documento.serie} gerada e '
            'pendente de transmissão à SEFAZ.',
        )
        return volta


def _natureza_do_tipo(filial, tipo):
    """
    A natureza cadastrada para este tipo de entrega, se houver uma.

    DEVOLVE `None` EM VEZ DE ESTOURAR: a tela do vendedor nao pode recusar
    abrir porque falta cadastro fiscal -- ela mostra o aviso e deixa
    registrar a entrega; quem recusa e' a emissao da nota, mais tarde e com
    a mensagem certa.
    """
    from apps.logistica.services.venda_fora_nfe import VendaForaNFeService

    try:
        return VendaForaNFeService.natureza(filial, tipo)
    except DadosInvalidosError:
        return None


class BonificacaoEntregaView(PermissaoRequiredMixin, View):
    """
    Move a entrega da bonificação e recebe a prova de que ela chegou.

    O CONTROLE VIVE JUNTO DA BONIFICAÇÃO, e não numa tela de entregas à
    parte: quem acompanha a cortesia está olhando a viagem que a levou.
    """

    permissao_modulo = 'logistica'
    permissao_acao = 'editar'

    def post(self, request, pk, entrega_pk):
        viagem = get_object_or_404(Viagem.objects.for_filial(_filial(request)), pk=pk)
        entrega = get_object_or_404(
            EntregaBonificacao.objects.select_related(
                'item_carga__viagem', 'entrega_rua__viagem',
            ),
            pk=entrega_pk,
        )
        volta = redirect('logistica:viagem-detail', pk=viagem.pk)

        # A ENTREGA E' BUSCADA PELA VIAGEM DA URL, e nao so' pelo id: id
        # colado a mao mexeria na bonificacao de outra viagem, e talvez de
        # outra filial.
        if entrega.viagem != viagem:
            messages.error(request, 'Esta bonificação não é desta viagem.')
            return volta

        acao = request.POST.get('acao')
        try:
            if acao == 'anexar':
                comprovante = EntregaBonificacaoService.anexar(
                    entrega,
                    tipo=request.POST.get('tipo') or '',
                    arquivo=request.FILES.get('arquivo'),
                    descricao=request.POST.get('descricao') or '',
                    usuario=request.user,
                )
                messages.success(
                    request,
                    f'{comprovante.get_tipo_display()} anexado à bonificação.',
                )
            elif acao == 'entregar':
                EntregaBonificacaoService.entregar(entrega, {
                    'destinatario_nome': request.POST.get('destinatario_nome'),
                    'destinatario_documento': request.POST.get('destinatario_documento'),
                    'quantidade_entregue': request.POST.get('quantidade_entregue'),
                    'observacao': request.POST.get('observacao'),
                }, usuario=request.user)
                messages.success(request, 'Entrega da bonificação registrada.')
            else:
                entrega = EntregaBonificacaoService.mover(
                    entrega, acao or '',
                    {
                        'observacao': request.POST.get('observacao'),
                        # O MOTIVO VEM DO MESMO FORMULARIO DO BOTAO: sem
                        # repassa-lo aqui, o servico recusava a recusa e a
                        # tela pedia o motivo que o usuario tinha acabado de
                        # escolher.
                        'motivo_nao_entrega': request.POST.get('motivo_nao_entrega'),
                    },
                    usuario=request.user,
                )
                messages.success(
                    request,
                    f'Bonificação marcada como {entrega.get_status_display().lower()}.',
                )
        except DadosInvalidosError as erro:
            messages.error(request, str(erro))

        return volta


class ViagemMDFeView(PermissaoRequiredMixin, View):
    """
    Os documentos fiscais da viagem, e o manifesto que os consolida.

    A TELA MOSTRA, QUEM MANIFESTA DECIDE. "Quando permitido pela legislação
    aplicável" depende de UF, de regime e da orientação da contabilidade —
    não de código. Vincular tudo sozinho ao abrir a tela seria decidir, em
    silêncio, uma questão que não é do software.
    """

    permissao_modulo = 'logistica'
    template_name = 'logistica/viagem/mdfe.html'

    def _viagem(self, request, pk):
        return get_object_or_404(
            Viagem.objects.for_filial(_filial(request))
            .select_related('transportadora'),
            pk=pk,
        )

    def get(self, request, pk):
        viagem = self._viagem(request, pk)
        documentos = MDFeViagemService.documentos(viagem)
        mdfe = MDFeViagemService.mdfe_da_viagem(viagem)
        painel = MDFeViagemService.painel(viagem, mdfe)
        return render(request, self.template_name, {
            'title': f'MDF-e da viagem #{viagem.numero:06d}',
            'viagem': viagem,
            'mdfe': mdfe,
            'painel': painel,
            'pendencias': MDFeViagemService.pendencias_do_painel(painel),
            'documentos': documentos,
            'resumo': MDFeViagemService.resumo(documentos),
        })

    def post(self, request, pk):
        viagem = self._viagem(request, pk)
        volta = redirect('logistica:viagem-mdfe', pk=viagem.pk)

        if not request.user.tem_permissao('logistica', 'editar'):
            messages.error(request, 'Sem permissão para mexer no manifesto.')
            return volta

        try:
            if request.POST.get('acao') == 'desvincular':
                tirou = MDFeViagemService.desvincular(
                    viagem, request.POST.get('documento'),
                )
                messages.success(
                    request,
                    'Documento retirado do manifesto.' if tirou
                    else 'Este documento não estava no manifesto.',
                )
            else:
                quantos = MDFeViagemService.vincular(
                    viagem, request.POST.getlist('documentos'), request.user,
                )
                messages.success(
                    request,
                    f'{quantos} documento(s) vinculado(s) ao MDF-e da viagem.',
                )
        except (DadosInvalidosError, ValueError) as erro:
            messages.error(request, str(erro))

        return volta


class ViagemPainelView(PermissaoRequiredMixin, View):
    """
    O painel da viagem, para ser lido de longe.

    QUEM ABRE ESTA TELA ESTÁ CONFERINDO UM CAMINHÃO — de pé, com prancheta,
    às vezes no celular na doca. Por isso poucos números, grandes, e um
    semáforo que responde a única pergunta que importa ali: a carga fecha?

    É TELA DE LEITURA. Nenhuma ação: quem confere não deve poder mexer na
    carga a partir de um número agregado, sem ver o registro que ele resume.
    """

    permissao_modulo = 'logistica'
    template_name = 'logistica/viagem/painel.html'

    def get(self, request, pk):
        viagem = get_object_or_404(
            Viagem.objects.for_filial(_filial(request))
            .select_related('motorista', 'veiculo', 'transportadora'),
            pk=pk,
        )
        quadro = EstoqueViagemService.quadro(viagem)
        return render(request, self.template_name, {
            'title': f'Painel — Viagem #{viagem.numero:06d}',
            'viagem': viagem,
            'quadro': quadro,
            'conciliacao': EstoqueViagemService.conciliacao(quadro),
            'pendencias': EstoqueViagemService.pendencias(quadro),
            'mdfe': MDFeViagemService.painel(viagem),
        })


class ViagemRetornoView(PermissaoRequiredMixin, View):
    """
    A conferência do retorno: o sistema calcula, a pessoa confere.

    O PREVISTO É O QUE A CONTA DIZ; o retorno é o que a contagem física
    encontra. Eles quase sempre coincidem — e é por isso que a diferença
    importa quando aparece: ela é quebra, furto ou erro de apontamento, e
    some se o sistema aceitar o previsto como se fosse fato.
    """

    permissao_modulo = 'logistica'
    template_name = 'logistica/viagem/retorno.html'

    def _viagem(self, request, pk):
        return get_object_or_404(
            Viagem.objects.for_filial(_filial(request)), pk=pk,
        )

    def get(self, request, pk):
        viagem = self._viagem(request, pk)
        linhas = RetornoViagemService.previsto(viagem)
        return render(request, self.template_name, {
            'title': f'Retorno — Viagem #{viagem.numero:06d}',
            'viagem': viagem,
            'linhas': linhas,
            'resumo': RetornoViagemService.resumo(linhas),
            'pendencias': RetornoViagemService.pode_encerrar(viagem),
            # A NOTA DE RETORNO VIVE AQUI porque e' aqui que a pessoa acaba
            # de conferir o que voltou: mandar procurar a emissao em outra
            # tela e' como a mercadoria volta e a nota nao sai.
            'vinculo': RetornoVendaForaService.vinculo(viagem),
            'pendencias_nota': RetornoVendaForaService.conferir(viagem),
        })

    def post(self, request, pk):
        viagem = self._viagem(request, pk)
        volta = redirect('logistica:viagem-retorno', pk=viagem.pk)

        if not request.user.tem_permissao('logistica', 'editar'):
            messages.error(request, 'Sem permissão para registrar retorno.')
            return volta

        try:
            if request.POST.get('acao') == 'tudo':
                resultado = RetornoViagemService.registrar_tudo(
                    viagem, usuario=request.user,
                )
            else:
                resultado = RetornoViagemService.registrar(
                    viagem, self._quantidades(request), usuario=request.user,
                )
        except DadosInvalidosError as erro:
            messages.error(request, str(erro))
            return volta

        messages.success(
            request,
            f'Retorno de {resultado["registrado"]} registrado e devolvido ao '
            'estoque.',
        )
        # A DIVERGENCIA VOLTA COMO AVISO, e nao como baixa automatica: baixa
        # e' declaracao de perda, com responsavel, e um sistema que a emite
        # sozinho ensina a fabrica a nao olhar.
        for divergencia in resultado['divergencias']:
            messages.warning(
                request,
                f'{divergencia["produto"]}: previstos '
                f'{divergencia["previsto"]}, conferidos '
                f'{divergencia["conferido"]} — faltam '
                f'{divergencia["diferenca"]}. Registre baixa ou corrija a '
                'contagem.',
            )
        return volta

    @staticmethod
    def _quantidades(request) -> dict:
        """
        Lê os campos `retorno-<produto>-<lote>` do formulário.

        O LOTE VIAJA NO NOME DO CAMPO porque o saldo é por produto E lote: um
        produto que saiu em dois lotes volta em duas linhas, e somá-las
        perderia de qual produção veio o que voltou.
        """
        quantidades = {}
        for campo, valor in request.POST.items():
            if not campo.startswith('retorno-'):
                continue
            partes = campo.split('-')
            if len(partes) != 3 or not partes[1].isdigit():
                continue
            produto_id = int(partes[1])
            lote_id = int(partes[2]) if partes[2].isdigit() else 0
            quantidades[(produto_id, lote_id or None)] = (
                (valor or '').replace(',', '.')
            )
        return quantidades


class ViagemAcertoView(PermissaoRequiredMixin, View):
    """
    O acerto da viagem: a conta que precisa fechar antes de encerrar.

        carga inicial = vendas + bonificações + retornos
                        + demais saídas justificadas

    POR QUE NÃO É O PAINEL. O painel é leitura de longe, na doca, enquanto a
    viagem anda: ele responde "como estamos?". Esta tela é o ato de fechar —
    é aqui que a viagem encerra, e por isso é aqui que a recusa precisa
    aparecer com a conta inteira ao lado, e não como um erro solto numa
    lista.

    OS NÚMEROS SÃO OS MESMOS, LIDOS DO MESMO LUGAR. Recalcular a conta aqui
    criaria uma segunda verdade sobre a mesma carga — e quando as duas
    divergissem ninguém saberia qual olhar.

    QUEM RECUSA O ENCERRAMENTO É O SERVIÇO, não esta tela. A tela mostra a
    recusa antes de a pessoa tentar, para não fazê-la clicar para descobrir;
    mas quem insistir pela URL leva a mesma resposta.
    """

    permissao_modulo = 'logistica'
    template_name = 'logistica/viagem/acerto.html'

    def _viagem(self, request, pk):
        return get_object_or_404(
            Viagem.objects.for_filial(_filial(request)), pk=pk,
        )

    def get(self, request, pk):
        viagem = self._viagem(request, pk)
        quadro = EstoqueViagemService.quadro(viagem)
        return render(request, self.template_name, {
            'title': f'Acerto — Viagem #{viagem.numero:06d}',
            'viagem': viagem,
            'quadro': quadro,
            'linhas': EstoqueViagemService.acerto(quadro),
            'conciliacao': EstoqueViagemService.conciliacao(quadro),
            'pendencias': ViagemService.pendencias_de_encerramento(viagem),
            'mensagem_bloqueio': ViagemService.NAO_CONCILIADA,
            'encerrada': viagem.status in (
                Viagem.Status.FINALIZADA, Viagem.Status.CANCELADA,
            ),
            'pode_agir': request.user.tem_permissao('logistica', 'editar'),
        })

    def post(self, request, pk):
        viagem = self._viagem(request, pk)
        volta = redirect('logistica:viagem-acerto', pk=viagem.pk)

        if not request.user.tem_permissao('logistica', 'editar'):
            messages.error(request, 'Sem permissão para encerrar a viagem.')
            return volta

        try:
            ViagemService.encerrar(viagem)
        except DadosInvalidosError as erro:
            messages.error(request, str(erro))
            return volta

        messages.success(
            request, f'Viagem #{viagem.numero:06d} encerrada com a carga conciliada.',
        )
        return redirect('logistica:viagem-detail', pk=viagem.pk)


class RastreabilidadeView(PermissaoRequiredMixin, View):
    """
    Onde cada caixa esteve: estoque → remessa → viagem → venda/bonificação/retorno.

    A PERGUNTA É SEMPRE FEITA NO PIOR MOMENTO — o fiscal pede a justificativa
    da remessa, o cliente reclama de um lote, o dono quer saber por que sobrou
    mercadoria no caminhão. Cada registro sabia um pedaço; esta tela é a linha
    inteira.

    É TELA DE LEITURA, e nada aqui é gravado: um histórico de rastreabilidade
    guardado à parte seria uma segunda verdade sobre a mesma caixa, e no dia
    em que divergisse do razão ninguém saberia qual acreditar.
    """

    permissao_modulo = 'logistica'
    template_name = 'logistica/rastreabilidade.html'

    def get(self, request):
        filial = _filial(request)
        produtos = RastreabilidadeService.produtos_rastreaveis(filial)

        escolhido = (request.GET.get('produto') or '').strip()
        produto = produtos.filter(pk=escolhido).first() if escolhido.isdigit() else None

        return render(request, self.template_name, {
            'title': 'Rastreabilidade',
            'produtos': produtos,
            'produto': produto,
            # SEM PRODUTO NAO HA' CADEIA, e nao uma cadeia vazia: a tela pede
            # a escolha em vez de fingir que respondeu.
            'cadeias': (
                RastreabilidadeService.do_produto(produto, filial)
                if produto is not None else []
            ),
        })
