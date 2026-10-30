from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Count, Prefetch, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View

from apps.cadastros.models import Cliente
from apps.core.services.permissions import PermissaoRequiredMixin
from apps.core.tenant_context import tenant_atomic
from apps.crm.forms import CarteiraClienteForm, OportunidadeCRMForm
from apps.crm.models import (
    AtividadeCRM, CarteiraCliente, EtapaPipelineCRM, OportunidadeCRM, PipelineCRM,
    RecompraCliente,
)
from apps.crm.services import CarteiraService, ComercialService
from apps.vendas.models import PedidoVenda


def _oportunidade_no_escopo(request, pk):
    queryset = OportunidadeCRM.objects.select_related(
        'cliente', 'cliente__filial', 'pipeline', 'etapa', 'responsavel', 'filial',
    ).prefetch_related('tags')
    queryset = CarteiraService.filtrar_oportunidades(
        queryset, usuario=request.user, empresa=request.filial_ativa.empresa,
    )
    return get_object_or_404(
        queryset,
        pk=pk, filial=request.filial_ativa,
    )


def _form_edicao_oportunidade(*args, **kwargs):
    form = OportunidadeCRMForm(*args, **kwargs)
    # Etapas só mudam pelo endpoint transacional, que registra o histórico e
    # atualiza fechado_em. O formulário edita os demais dados do negócio.
    form.fields['pipeline'].disabled = True
    form.fields['etapa'].disabled = True
    return form


class FunilComercialView(PermissaoRequiredMixin, View):
    permissao_modulo = 'crm'
    permissao_acao = 'ver'
    template_name = 'crm/funil.html'

    def get(self, request):
        filial = request.filial_ativa
        ComercialService.obter_pipeline_padrao(filial)
        pipelines = PipelineCRM.objects.filter(filial=filial, ativo=True).order_by('nome')
        pipeline_id = request.GET.get('pipeline', '').strip()
        pipeline = pipelines.filter(pk=pipeline_id).first() if pipeline_id else None
        pipeline = pipeline or pipelines.filter(padrao=True).first() or pipelines.first()

        oportunidades = OportunidadeCRM.objects.filter(
            filial=filial, pipeline=pipeline,
        ).select_related('cliente', 'responsavel', 'etapa').prefetch_related('tags')
        oportunidades = CarteiraService.filtrar_oportunidades(
            oportunidades, usuario=request.user, empresa=filial.empresa,
        )
        responsavel = request.GET.get('responsavel', '').strip()
        prioridade = request.GET.get('prioridade', '').strip()
        busca = request.GET.get('q', '').strip()
        if responsavel:
            oportunidades = oportunidades.filter(responsavel_id=responsavel)
        if prioridade:
            oportunidades = oportunidades.filter(prioridade=prioridade)
        if busca:
            oportunidades = oportunidades.filter(
                models.Q(titulo__icontains=busca)
                | models.Q(cliente__razao_social__icontains=busca)
                | models.Q(cliente__nome_fantasia__icontains=busca)
                | models.Q(cliente__cpf_cnpj__icontains=busca)
            )
        etapas = pipeline.etapas.filter(ativo=True).prefetch_related(
            Prefetch('oportunidades', queryset=oportunidades.order_by('-updated_at')),
        )
        resumo = oportunidades.aggregate(total=Count('id'), valor=Sum('valor_estimado'))
        return render(request, self.template_name, {
            'pipelines': pipelines,
            'pipeline': pipeline,
            'etapas': etapas,
            'total': resumo['total'] or 0,
            'valor_total': resumo['valor'] or Decimal('0'),
            'filtros': {'responsavel': responsavel, 'prioridade': prioridade, 'q': busca},
            'responsaveis': (
                request.user.__class__.objects.filter(empresa=filial.empresa, ativo=True)
                if CarteiraService.pode_supervisionar(request.user)
                else request.user.__class__.objects.filter(pk=request.user.pk)
            ).order_by('nome'),
            'prioridades': OportunidadeCRM.Prioridade.choices,
            'pode_supervisionar': CarteiraService.pode_supervisionar(request.user),
        })


class OportunidadeCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'crm'
    permissao_acao = 'criar'
    template_name = 'crm/oportunidade_form.html'

    def get(self, request):
        pipeline = ComercialService.obter_pipeline_padrao(request.filial_ativa)
        cliente = CarteiraService.clientes_disponiveis_na_filial(
            filial=request.filial_ativa, usuario=request.user,
        ).filter(
            pk=request.GET.get('cliente'), ativo=True,
        ).first()
        origem = request.GET.get('origem')
        origens_validas = {valor for valor, _rotulo in OportunidadeCRM.Origem.choices}
        inicial = {
            'pipeline': pipeline,
            'etapa': pipeline.etapas.filter(ativo=True).order_by('ordem').first(),
            'cliente': cliente,
            'origem': origem if origem in origens_validas else OportunidadeCRM.Origem.MANUAL,
        }
        if cliente:
            inicial['titulo'] = f'Nova oportunidade — {cliente.nome_display}'
        if cliente and origem == OportunidadeCRM.Origem.RECOMPRA:
            recompra = RecompraCliente.objects.filter(
                filial=request.filial_ativa, cliente=cliente,
            ).first()
            inicial.update({
                'titulo': f'Reativação — {cliente.nome_display}',
                'valor_estimado': recompra.valor_medio if recompra else Decimal('0'),
                'proxima_acao': 'Entrar em contato sobre a próxima compra',
            })
        return render(request, self.template_name, {
            'form': OportunidadeCRMForm(
                filial=request.filial_ativa, usuario=request.user, initial=inicial,
            ),
            'title': 'Nova oportunidade',
        })

    def post(self, request):
        form = OportunidadeCRMForm(
            request.POST, filial=request.filial_ativa, usuario=request.user,
        )
        if form.is_valid():
            dados = {
                campo: form.cleaned_data[campo]
                for campo in form.Meta.fields if campo != 'tags'
            }
            oportunidade = ComercialService.criar_oportunidade(
                filial=request.filial_ativa, usuario=request.user, dados=dados,
                tags=form.cleaned_data['tags'],
            )
            messages.success(request, 'Oportunidade criada no funil comercial.')
            return redirect('crm:oportunidade-detalhe', pk=oportunidade.pk)
        return render(request, self.template_name, {'form': form, 'title': 'Nova oportunidade'})


class OportunidadeDetailView(PermissaoRequiredMixin, View):
    permissao_modulo = 'crm'
    permissao_acao = 'ver'
    template_name = 'crm/oportunidade_detail.html'

    def get(self, request, pk):
        oportunidade = _oportunidade_no_escopo(request, pk)
        return render(request, self.template_name, {
            'oportunidade': oportunidade,
            'form': _form_edicao_oportunidade(
                instance=oportunidade, filial=request.filial_ativa, usuario=request.user,
            ),
            'atividades': oportunidade.atividades.select_related('autor')[:100],
        })

    @tenant_atomic
    def post(self, request, pk):
        if not request.user.tem_permissao('crm', 'editar'):
            messages.error(request, 'Você não tem permissão para esta ação.')
            return redirect('crm:oportunidade-detalhe', pk=pk)
        oportunidade = _oportunidade_no_escopo(request, pk)
        form = _form_edicao_oportunidade(
            request.POST, instance=oportunidade, filial=request.filial_ativa,
            usuario=request.user,
        )
        if form.is_valid():
            oportunidade = form.save()
            AtividadeCRM.objects.create(
                oportunidade=oportunidade, tipo=AtividadeCRM.Tipo.EDICAO,
                descricao='Dados da oportunidade atualizados.', autor=request.user,
            )
            messages.success(request, 'Oportunidade atualizada.')
            return redirect('crm:oportunidade-detalhe', pk=pk)
        return render(request, self.template_name, {
            'oportunidade': oportunidade, 'form': form,
            'atividades': oportunidade.atividades.select_related('autor')[:100],
        })


class OportunidadeMoverView(PermissaoRequiredMixin, View):
    permissao_modulo = 'crm'
    permissao_acao = 'editar'

    def post(self, request, pk):
        oportunidade = _oportunidade_no_escopo(request, pk)
        etapa = get_object_or_404(
            EtapaPipelineCRM.objects.select_related('pipeline'),
            pk=request.POST.get('etapa'), ativo=True,
        )
        try:
            ComercialService.mover_etapa(
                oportunidade=oportunidade, etapa=etapa, usuario=request.user,
                motivo_perda=request.POST.get('motivo_perda', ''),
            )
            messages.success(request, f'Oportunidade movida para {etapa.nome}.')
        except ValidationError as exc:
            messages.error(request, '; '.join(exc.messages))
        proxima_url = request.POST.get('next', '')
        if not url_has_allowed_host_and_scheme(
            proxima_url, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
        ):
            proxima_url = reverse('crm:funil')
        return redirect(proxima_url)


class OportunidadeNotaView(PermissaoRequiredMixin, View):
    permissao_modulo = 'crm'
    permissao_acao = 'editar'

    def post(self, request, pk):
        oportunidade = _oportunidade_no_escopo(request, pk)
        try:
            ComercialService.registrar_nota(
                oportunidade=oportunidade, texto=request.POST.get('nota'), usuario=request.user,
            )
            messages.success(request, 'Nota registrada no histórico.')
        except ValidationError as exc:
            messages.error(request, '; '.join(exc.messages))
        return redirect('crm:oportunidade-detalhe', pk=pk)


class Cliente360View(PermissaoRequiredMixin, View):
    permissao_modulo = 'crm'
    permissao_acao = 'ver'
    template_name = 'crm/cliente_360.html'

    def get(self, request, cliente_id):
        filial = request.filial_ativa
        clientes = CarteiraService.clientes_disponiveis_na_filial(
            filial=filial, usuario=request.user,
        )
        cliente = get_object_or_404(clientes, pk=cliente_id)
        oportunidades = OportunidadeCRM.objects.filter(
            filial=filial, cliente=cliente,
        ).select_related('etapa', 'responsavel', 'pipeline').prefetch_related('tags')
        pedidos = PedidoVenda.objects.filter(
            filial=filial, cliente=cliente,
        ).order_by('-data_emissao')
        totais = pedidos.exclude(
            status__in=[PedidoVenda.Status.CANCELADO, PedidoVenda.Status.DEVOLVIDO],
        ).aggregate(total=Sum('valor_total'), quantidade=Count('id'))
        recompra = RecompraCliente.objects.filter(filial=filial, cliente=cliente).first()
        return render(request, self.template_name, {
            'cliente': cliente,
            'oportunidades': oportunidades[:20],
            'pedidos': pedidos[:12],
            'total_comprado': totais['total'] or Decimal('0'),
            'quantidade_pedidos': totais['quantidade'] or 0,
            'recompra': recompra,
        })


class CarteiraClientesView(PermissaoRequiredMixin, View):
    permissao_modulo = 'crm'
    permissao_acao = 'ver'
    template_name = 'crm/carteira_clientes.html'

    def _autorizar(self, request):
        if CarteiraService.pode_supervisionar(request.user):
            return True
        messages.error(request, 'Somente gestores podem organizar as carteiras de clientes.')
        return False

    def _contexto(self, request, form=None):
        empresa = request.filial_ativa.empresa
        busca = (request.GET.get('q') or '').strip()
        queryset = Cliente.objects.for_empresa(empresa).filter(ativo=True).select_related('filial')
        if busca:
            queryset = queryset.filter(
                models.Q(razao_social__icontains=busca)
                | models.Q(nome_fantasia__icontains=busca)
                | models.Q(cpf_cnpj__icontains=busca)
            )
        atribuicoes = {
            item.grupo_cliente: item
            for item in CarteiraCliente.objects.filter(empresa=empresa).select_related('vendedor')
        }
        clientes = []
        vistos = set()
        for cliente in queryset.order_by('razao_social', 'pk'):
            if cliente.grupo_replicacao in vistos:
                continue
            vistos.add(cliente.grupo_replicacao)
            clientes.append({
                'cliente': cliente,
                'atribuicao': atribuicoes.get(cliente.grupo_replicacao),
            })
        return {
            'clientes': clientes,
            'vendedores': request.user.__class__.objects.filter(
                empresa=empresa, ativo=True,
            ).order_by('nome'),
            'busca': busca,
            'form': form or CarteiraClienteForm(empresa=empresa),
        }

    def get(self, request):
        if not self._autorizar(request):
            return redirect('crm:funil')
        return render(request, self.template_name, self._contexto(request))

    def post(self, request):
        if not self._autorizar(request):
            return redirect('crm:funil')
        form = CarteiraClienteForm(request.POST, empresa=request.filial_ativa.empresa)
        if form.is_valid():
            carteira, criada = CarteiraService.atribuir(
                cliente=form.cleaned_data['cliente'],
                vendedor=form.cleaned_data['vendedor'],
                atribuido_por=request.user,
            )
            acao = 'incluído na' if criada else 'reatribuído dentro da'
            messages.success(
                request,
                f'{carteira.cliente.nome_display} foi {acao} carteira de {carteira.vendedor.nome}.',
            )
            return redirect('crm:carteiras')
        return render(request, self.template_name, self._contexto(request, form=form))
