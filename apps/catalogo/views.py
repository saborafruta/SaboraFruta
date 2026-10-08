import json
from decimal import Decimal

from django.contrib import messages
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

from .forms import CatalogoConfiguracaoForm
from .models import CatalogoConfiguracao, CatalogoLinkPublico, PedidoCatalogo
from .services import enviar_atualizacao_whatsapp


COLUNAS = (
    (PedidoCatalogo.Status.AGUARDANDO_CLIENTE, 'Aguardando cliente'),
    (PedidoCatalogo.Status.AGUARDANDO_LOJA, 'Novos pedidos'),
    (PedidoCatalogo.Status.APROVADO, 'Aprovados'),
    (PedidoCatalogo.Status.EM_SEPARACAO, 'Em separação'),
    (PedidoCatalogo.Status.PRONTO, 'Prontos'),
    (PedidoCatalogo.Status.SAIU_ENTREGA, 'Em entrega'),
    (PedidoCatalogo.Status.ENTREGUE, 'Entregues'),
)


class CatalogoPainelView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'ver'

    def get(self, request):
        config, _ = CatalogoConfiguracao.objects.get_or_create(filial=request.filial_ativa)
        link = CatalogoLinkPublico.objects.filter(filial=request.filial_ativa, ativo=True).first()
        pedidos = PedidoCatalogo.objects.for_filial(request.filial_ativa).select_related(
            'cliente', 'pedido_venda',
        ).prefetch_related('itens')[:150]
        por_status = {status: [] for status, _ in COLUNAS}
        for pedido in pedidos:
            if pedido.status in por_status:
                por_status[pedido.status].append(pedido)
        return render(request, 'catalogo/painel.html', {
            'configuracao': config,
            'form': CatalogoConfiguracaoForm(instance=config),
            'colunas': [(status, titulo, por_status[status]) for status, titulo in COLUNAS],
            'link_publico': request.build_absolute_uri(
                reverse('catalogo_publico:catalogo', args=[link.token])
            ) if link else '',
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
        pedidos = PedidoCatalogo.objects.for_filial(request.filial_ativa).prefetch_related('itens')[:150]
        por_status = {status: [] for status, _ in COLUNAS}
        for pedido in pedidos:
            if pedido.status in por_status:
                por_status[pedido.status].append(pedido)
        return render(request, 'catalogo/painel.html', {
            'configuracao': config, 'form': form,
            'colunas': [(status, titulo, por_status[status]) for status, titulo in COLUNAS],
            'link_publico': '',
        }, status=400)


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
                    venda = VendaService.criar_pedido(
                        filial=pedido.filial, usuario=request.user, cliente=pedido.cliente,
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
                    venda.save()
                    VendaService.confirmar_pedido(venda, request.user)
                    pedido.pedido_venda = venda
                    pedido.status = PedidoCatalogo.Status.APROVADO
                    pedido.aprovado_loja_em = timezone.now()
                    pedido.save(update_fields=['pedido_venda', 'status', 'aprovado_loja_em', 'updated_at'])
                messages.success(request, f'{pedido.numero} aprovado e enviado para a fila de separação.')
                alterado = True
            elif acao == 'separar' and pedido.status == PedidoCatalogo.Status.APROVADO:
                VendaService.separar_pedido(pedido.pedido_venda, request.user)
                pedido.status = PedidoCatalogo.Status.EM_SEPARACAO
                pedido.save(update_fields=['status', 'updated_at'])
                messages.success(request, f'Separação do {pedido.numero} iniciada.')
                alterado = True
            elif acao == 'pronto' and pedido.status == PedidoCatalogo.Status.EM_SEPARACAO:
                pedido.status = PedidoCatalogo.Status.PRONTO
                pedido.save(update_fields=['status', 'updated_at'])
                alterado = True
            elif acao == 'saiu' and pedido.status == PedidoCatalogo.Status.PRONTO:
                pedido.status = PedidoCatalogo.Status.SAIU_ENTREGA
                pedido.save(update_fields=['status', 'updated_at'])
                alterado = True
            elif acao == 'entregue' and pedido.status in {PedidoCatalogo.Status.PRONTO, PedidoCatalogo.Status.SAIU_ENTREGA}:
                pedido.status = PedidoCatalogo.Status.ENTREGUE
                pedido.save(update_fields=['status', 'updated_at'])
                alterado = True
            elif acao == 'cancelar' and pedido.status not in {PedidoCatalogo.Status.ENTREGUE, PedidoCatalogo.Status.CANCELADO}:
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
        return redirect('catalogo:painel')
