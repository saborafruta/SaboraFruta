import json
from decimal import Decimal

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.decorators.http import require_POST
from django.utils.decorators import method_decorator

from apps.core.services.permissions import PermissaoRequiredMixin
from apps.core.services.tenant_public_link_service import TenantPublicLinkService
from apps.core.tenant_context import get_current_database_alias, tenant_atomic
from apps.vendas.services.venda_service import VendaService

from .forms import CatalogoConfiguracaoForm, CupomCatalogoForm
from .models import CatalogoConfiguracao, CatalogoLinkPublico, CupomCatalogo, PedidoCatalogo
from .services import enviar_atualizacao_whatsapp


COLUNAS = (
    (PedidoCatalogo.Status.AGUARDANDO_CLIENTE, 'Aguardando cliente'),
    (PedidoCatalogo.Status.AGUARDANDO_LOJA, 'Novos pedidos'),
    (PedidoCatalogo.Status.APROVADO, 'Aprovados'),
    (PedidoCatalogo.Status.PENDENTE_CAIXA, 'Pendente no caixa'),
    (PedidoCatalogo.Status.PAGO, 'Pagos · aguardando preparo'),
    (PedidoCatalogo.Status.EM_SEPARACAO, 'Em separação'),
    (PedidoCatalogo.Status.PRONTO, 'Prontos'),
    (PedidoCatalogo.Status.SAIU_ENTREGA, 'Em entrega'),
    (PedidoCatalogo.Status.ENTREGUE, 'Entregues'),
)

STATUS_MOVIMENTAVEIS = {status for status, _titulo in COLUNAS}


def _garantir_aprovacao(pedido, usuario):
    if pedido.pedido_venda_id:
        if not pedido.aprovado_loja_em:
            pedido.aprovado_loja_em = timezone.now()
        return
    venda = VendaService.criar_pedido(
        filial=pedido.filial, usuario=usuario, cliente=pedido.cliente,
        observacao=f'Pedido do catálogo {pedido.numero}. {pedido.observacao}'.strip(),
    )
    venda.origem = venda.Origem.WHATSAPP
    venda.valor_frete = pedido.valor_frete
    venda.data_entrega_prevista = pedido.entrega_em.date() if pedido.entrega_em else None
    venda.endereco_entrega_avulso = pedido.endereco_entrega or None
    venda.save(update_fields=[
        'origem', 'valor_frete', 'data_entrega_prevista',
        'endereco_entrega_avulso', 'updated_at',
    ])
    for item in pedido.itens.select_related('produto'):
        VendaService.adicionar_item(
            venda, item.produto, Decimal(item.quantidade), item.valor_unitario,
        )
    venda.valor_frete = pedido.valor_frete
    venda.recalcular_totais()
    venda.valor_desconto = pedido.valor_desconto
    venda.desconto_valor = pedido.valor_desconto
    venda.valor_total = max(
        Decimal('0'), venda.valor_produtos + venda.valor_frete - pedido.valor_desconto,
    )
    venda.save()
    VendaService.confirmar_pedido(venda, usuario)
    pedido.pedido_venda = venda
    pedido.aprovado_loja_em = timezone.now()


def _mover_pedido(pedido, novo_status, usuario):
    if novo_status not in STATUS_MOVIMENTAVEIS:
        raise ValueError('A etapa escolhida não faz parte do fluxo de pedidos.')
    if pedido.status == novo_status:
        return False
    if pedido.status == PedidoCatalogo.Status.CANCELADO:
        raise ValueError('Pedidos cancelados não podem ser movimentados.')
    if pedido.venda_pdv_id and novo_status in {
        PedidoCatalogo.Status.AGUARDANDO_CLIENTE,
        PedidoCatalogo.Status.AGUARDANDO_LOJA,
        PedidoCatalogo.Status.APROVADO,
        PedidoCatalogo.Status.PENDENTE_CAIXA,
    }:
        raise ValueError('O pagamento já foi registrado; escolha uma etapa de preparo ou entrega.')
    if novo_status == PedidoCatalogo.Status.PENDENTE_CAIXA and pedido.venda_pdv_id:
        raise ValueError('Este pedido já foi recebido no PDV.')

    etapas_apos_aprovacao = {
        PedidoCatalogo.Status.APROVADO,
        PedidoCatalogo.Status.PENDENTE_CAIXA,
        PedidoCatalogo.Status.PAGO,
        PedidoCatalogo.Status.EM_SEPARACAO,
        PedidoCatalogo.Status.PRONTO,
        PedidoCatalogo.Status.SAIU_ENTREGA,
        PedidoCatalogo.Status.ENTREGUE,
    }
    if novo_status in etapas_apos_aprovacao:
        _garantir_aprovacao(pedido, usuario)
    if novo_status == PedidoCatalogo.Status.PAGO and not pedido.venda_pdv_id:
        raise ValueError('O pagamento deve ser registrado pelo PDV antes de mover para Pago.')
    if novo_status in {PedidoCatalogo.Status.SAIU_ENTREGA, PedidoCatalogo.Status.ENTREGUE} and not pedido.venda_pdv_id:
        raise ValueError('Registre o pagamento no PDV antes de concluir a entrega.')
    if novo_status in {PedidoCatalogo.Status.EM_SEPARACAO, PedidoCatalogo.Status.PRONTO}:
        venda = pedido.pedido_venda
        if not pedido.venda_pdv_id and venda and venda.pode_separar:
            VendaService.separar_pedido(venda, usuario)

    pedido.status = novo_status
    campos = ['status', 'updated_at']
    if pedido.pedido_venda_id:
        campos.append('pedido_venda')
    if pedido.aprovado_loja_em:
        campos.append('aprovado_loja_em')
    pedido.save(update_fields=list(dict.fromkeys(campos)))
    return True


class CatalogoPainelView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'ver'

    def get(self, request):
        config, _ = CatalogoConfiguracao.objects.get_or_create(filial=request.filial_ativa)
        link = CatalogoLinkPublico.objects.filter(filial=request.filial_ativa, ativo=True).first()
        return render(request, 'catalogo/painel.html', {
            'configuracao': config,
            'form': CatalogoConfiguracaoForm(instance=config),
            'link_publico': request.build_absolute_uri(
                reverse('catalogo_publico:catalogo', args=[link.token])
            ) if link else '',
            'cupom_form': CupomCatalogoForm(),
            'cupons': CupomCatalogo.objects.filter(filial=request.filial_ativa),
        })

    def post(self, request):
        config, _ = CatalogoConfiguracao.objects.get_or_create(filial=request.filial_ativa)
        form = CatalogoConfiguracaoForm(request.POST, instance=config)
        if form.is_valid():
            item = form.save(commit=False)
            item.filial = request.filial_ativa
            item.save()
            messages.success(request, 'Configurações do catálogo e da entrega salvas.')
            return redirect('catalogo:painel')
        return render(request, 'catalogo/painel.html', {
            'configuracao': config, 'form': form,
            'link_publico': '',
            'cupom_form': CupomCatalogoForm(),
            'cupons': CupomCatalogo.objects.filter(filial=request.filial_ativa),
        }, status=400)


@method_decorator(require_POST, name='dispatch')
class CupomCatalogoCriarView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'editar'

    def post(self, request):
        form = CupomCatalogoForm(request.POST)
        if form.is_valid():
            cupom = form.save(commit=False)
            cupom.filial = request.filial_ativa
            if CupomCatalogo.objects.filter(filial=request.filial_ativa, codigo__iexact=cupom.codigo).exists():
                form.add_error('codigo', 'Já existe um cupom com este código nesta filial.')
            else:
                cupom.save()
                messages.success(request, f'Cupom {cupom.codigo} criado com sucesso.')
                return redirect('catalogo:painel')
        config, _ = CatalogoConfiguracao.objects.get_or_create(filial=request.filial_ativa)
        link = CatalogoLinkPublico.objects.filter(filial=request.filial_ativa, ativo=True).first()
        return render(request, 'catalogo/painel.html', {
            'configuracao': config, 'form': CatalogoConfiguracaoForm(instance=config),
            'link_publico': request.build_absolute_uri(
                reverse('catalogo_publico:catalogo', args=[link.token])
            ) if link else '',
            'cupom_form': form,
            'cupons': CupomCatalogo.objects.filter(filial=request.filial_ativa),
        }, status=400)


@method_decorator(require_POST, name='dispatch')
class CupomCatalogoAlternarView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'editar'

    def post(self, request, pk):
        cupom = get_object_or_404(
            CupomCatalogo, pk=pk, filial=request.filial_ativa,
        )
        cupom.ativo = not cupom.ativo
        cupom.save(update_fields=['ativo', 'updated_at'])
        estado = 'ativado' if cupom.ativo else 'desativado'
        messages.success(request, f'Cupom {cupom.codigo} {estado}.')
        return redirect('catalogo:painel')


class PedidosCatalogoView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'ver'

    def get(self, request):
        pedidos = list(
            PedidoCatalogo.objects.for_filial(request.filial_ativa)
            .select_related('cliente', 'pedido_venda', 'venda_pdv')
            .prefetch_related('itens')[:200]
        )
        por_status = {status: [] for status, _ in COLUNAS}
        for pedido in pedidos:
            status_visual = pedido.status
            if status_visual in por_status:
                por_status[status_visual].append(pedido)
        return render(request, 'catalogo/pedidos.html', {
            'colunas': [(status, titulo, por_status[status]) for status, titulo in COLUNAS],
            'etapas_movimentacao': COLUNAS,
            'total_abertos': sum(
                len(por_status[status]) for status, _ in COLUNAS
                if status != PedidoCatalogo.Status.ENTREGUE
            ),
        })


class PedidoImpressaoView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'ver'

    def get(self, request, pk):
        pedido = get_object_or_404(
            PedidoCatalogo.objects.for_filial(request.filial_ativa)
            .select_related('cliente', 'filial', 'filial__empresa')
            .prefetch_related('itens'),
            pk=pk,
        )
        resposta = render(request, 'catalogo/pedido_impressao.html', {'pedido': pedido})
        resposta['Cache-Control'] = 'private, no-store'
        return resposta


@method_decorator(require_POST, name='dispatch')
class CatalogoLinkView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'editar'

    def post(self, request):
        link, _ = CatalogoLinkPublico.objects.get_or_create(filial=request.filial_ativa)
        if not link.ativo:
            link.ativo = True
            link.save(update_fields=['ativo', 'updated_at'])
        TenantPublicLinkService.register(
            kind='catalogo', token=link.token, db_alias=get_current_database_alias(),
        )
        messages.success(request, 'Link público do catálogo pronto para compartilhar.')
        return redirect('catalogo:painel')


@method_decorator(require_POST, name='dispatch')
class PedidoAcaoView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'editar'

    def post(self, request, pk, acao):
        pedido = get_object_or_404(
            PedidoCatalogo.objects.for_filial(request.filial_ativa).select_related('cliente', 'pedido_venda'),
            pk=pk,
        )
        alterado = False
        try:
            if acao == 'aprovar' and pedido.status == PedidoCatalogo.Status.AGUARDANDO_LOJA:
                with tenant_atomic():
                    _garantir_aprovacao(pedido, request.user)
                    pedido.status = PedidoCatalogo.Status.APROVADO
                    pedido.save(update_fields=['pedido_venda', 'status', 'aprovado_loja_em', 'updated_at'])
                messages.success(
                    request,
                    f'{pedido.numero} aprovado. Agora você pode receber no PDV ou iniciar a separação.',
                )
                alterado = True
            elif acao == 'separar' and pedido.status == PedidoCatalogo.Status.APROVADO:
                VendaService.separar_pedido(pedido.pedido_venda, request.user)
                pedido.status = PedidoCatalogo.Status.EM_SEPARACAO
                pedido.save(update_fields=['status', 'updated_at'])
                messages.success(request, f'Separação do {pedido.numero} iniciada.')
                alterado = True
            elif acao in {'pendencia', 'pronto'} and pedido.status == PedidoCatalogo.Status.EM_SEPARACAO:
                pedido.status = PedidoCatalogo.Status.PRONTO
                pedido.save(update_fields=['status', 'updated_at'])
                messages.success(request, f'{pedido.numero} separado e marcado como pronto.')
                alterado = True
            elif (
                acao == 'saiu'
                and pedido.status == PedidoCatalogo.Status.PRONTO
                and pedido.venda_pdv_id
            ):
                pedido.status = PedidoCatalogo.Status.SAIU_ENTREGA
                pedido.save(update_fields=['status', 'updated_at'])
                alterado = True
            elif (
                acao == 'entregue'
                and pedido.status in {
                    PedidoCatalogo.Status.PRONTO,
                    PedidoCatalogo.Status.SAIU_ENTREGA,
                }
                and pedido.venda_pdv_id
            ):
                pedido.status = PedidoCatalogo.Status.ENTREGUE
                pedido.save(update_fields=['status', 'updated_at'])
                alterado = True
            elif acao == 'cancelar' and pedido.status not in {PedidoCatalogo.Status.ENTREGUE, PedidoCatalogo.Status.CANCELADO}:
                if pedido.venda_pdv_id:
                    raise ValueError('O pagamento já foi registrado. Faça o estorno pelo PDV antes de cancelar.')
                if pedido.pedido_venda and pedido.pedido_venda.pode_cancelar:
                    VendaService.cancelar_pedido(pedido.pedido_venda, request.user, 'Cancelado no catálogo')
                pedido.status = PedidoCatalogo.Status.CANCELADO
                pedido.save(update_fields=['status', 'updated_at'])
                messages.info(request, f'{pedido.numero} cancelado.')
                alterado = True
            else:
                messages.warning(request, 'Essa ação não é permitida no estado atual do pedido.')
        except Exception as exc:
            messages.error(request, f'Não foi possível concluir a ação: {exc}')
        if alterado:
            try:
                enviar_atualizacao_whatsapp(pedido, db_alias=get_current_database_alias())
            except Exception:
                # A mudança operacional permanece válida mesmo se o canal estiver indisponível.
                messages.warning(request, 'Status atualizado, mas não foi possível avisar pelo WhatsApp agora.')
        return redirect('catalogo:pedidos')


@method_decorator(require_POST, name='dispatch')
class PedidoMoverView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'editar'

    def post(self, request, pk):
        json_esperado = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        try:
            with tenant_atomic():
                pedido = get_object_or_404(
                    PedidoCatalogo.objects.for_filial(request.filial_ativa)
                    .select_for_update().select_related('cliente'),
                    pk=pk,
                )
                alterado = _mover_pedido(
                    pedido, (request.POST.get('status') or '').strip(), request.user,
                )
            if alterado:
                try:
                    enviar_atualizacao_whatsapp(
                        pedido, db_alias=get_current_database_alias(),
                    )
                except Exception:
                    # A etapa operacional não pode ser desfeita por uma
                    # indisponibilidade momentânea do canal de mensagens.
                    if not json_esperado:
                        messages.warning(
                            request,
                            'Etapa atualizada, mas não foi possível avisar pelo WhatsApp agora.',
                        )
            mensagem = f'{pedido.numero} movido para {pedido.get_status_display()}.'
            if json_esperado:
                return JsonResponse({'ok': True, 'mensagem': mensagem, 'status': pedido.status})
            messages.success(request, mensagem)
        except Exception as exc:
            if json_esperado:
                return JsonResponse({'ok': False, 'erro': str(exc)}, status=400)
            messages.error(request, f'Não foi possível mover o pedido: {exc}')
        return redirect('catalogo:pedidos')
