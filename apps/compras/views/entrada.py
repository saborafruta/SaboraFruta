"""Views de Entrada de Mercadoria."""
import logging
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.dateparse import parse_date
from django.utils import timezone
from django.views import View

from apps.cadastros.models import Fornecedor
from apps.compras.forms import (
    AdicionarItemEntradaForm, ConsultarChaveForm, EntradaNFForm, EntradaNFParcelaForm,
    ImportarXMLForm,
)
from apps.compras.models import EntradaNF, EntradaNFParcela, PedidoCompra
from apps.compras.services.compra_service import CompraService
from apps.compras.services.entrada_custo_service import EntradaCustoService
from apps.compras.services.entrada_financeiro_service import (
    gerar_contas_pagar_da_entrada, validar_geracao_contas_pagar,
)
from apps.compras.services.entrada_estorno_service import calcular_impacto_estorno_entrada, estornar_entrada
from apps.compras.services.entrada_produto_service import (
    criar_produto_e_vincular_item, desvincular_item_de_produto,
    reprocessar_vinculos_automaticos, sincronizar_vinculos_da_conferencia,
    sugerir_produtos_para_item, vincular_item_a_produto,
)
from apps.compras.services.entrada_xml_service import (
    EntradaXMLDuplicadaError,
    atualizar_equivalencias_fornecedor_xml, criar_fornecedor_por_emitente_xml,
    get_fornecedor_padrao, importar_xml_para_entrada, localizar_fornecedor,
)
from apps.core.services.exceptions import DomainError
from apps.core.services.auditoria import auditoria_para_objeto, registrar_auditoria, snapshot_modelo
from apps.core.services.permissions import PERMISSION_DENIED_MESSAGE, PermissaoRequiredMixin
from apps.estoque.models import Estoque, LoteProduto, MovimentacaoEstoque
from apps.produtos.models import Produto
from apps.produtos.services.prontidao_comercial_service import avaliar_entrada_pos_efetivacao


STATUS_KPI = {
    'rascunho': [EntradaNF.Status.RASCUNHO],
    'aguardando': [
        EntradaNF.Status.AGUARDANDO_VINCULOS,
        EntradaNF.Status.AGUARDANDO_CONFERENCIA,
    ],
    'diferencas': [EntradaNF.Status.COM_DIFERENCAS],
    'efetivadas': [EntradaNF.Status.EFETIVADA],
}


def _decimal_localizado(valor, padrao=Decimal('1')) -> Decimal:
    if valor in (None, ''):
        return padrao
    texto = str(valor).strip().replace(' ', '')
    if ',' in texto:
        texto = texto.replace('.', '').replace(',', '.')
    return Decimal(texto)


def _bool_parametros(data, nome: str, padrao: bool = False) -> bool:
    if nome not in data:
        return padrao
    return str(data.get(nome)).strip().lower() in {'1', 'true', 'on', 'sim'}


logger = logging.getLogger(__name__)


def _entrada_aberta(entrada):
    return entrada.status in {
        EntradaNF.Status.RASCUNHO,
        EntradaNF.Status.AGUARDANDO_VINCULOS,
        EntradaNF.Status.AGUARDANDO_CONFERENCIA,
        EntradaNF.Status.COM_DIFERENCAS,
        EntradaNF.Status.CONFERIDA,
    }


def _atualizar_equivalencias_fornecedor(entrada):
    return atualizar_equivalencias_fornecedor_xml(
        entrada.filial,
        entrada.fornecedor,
        entrada.emitente_cnpj_xml,
    )


def _criar_fornecedor_do_xml(entrada) -> Fornecedor:
    return criar_fornecedor_por_emitente_xml(
        entrada.filial,
        {
            'documento': entrada.emitente_cnpj_xml,
            'razao_social': entrada.emitente_razao_social_xml,
            'nome_fantasia': entrada.emitente_nome_fantasia_xml,
            'ie': entrada.emitente_ie_xml,
            'endereco': entrada.emitente_endereco_xml,
            'municipio': entrada.emitente_municipio_xml,
            'uf': entrada.emitente_uf_xml,
            'cep': entrada.emitente_cep_xml,
            'telefone': entrada.emitente_telefone_xml,
        },
        exigir_dados=True,
    )


def _atualizar_diferenca_item(item):
    return CompraService.atualizar_diferenca_item(item)


def _avaliar_diferenca_item_para_tela(item):
    tipo, descricao, bloqueante = CompraService.avaliar_diferenca_item(item)
    item.diferenca_tipo = tipo
    item.diferenca_descricao = descricao
    item.diferenca_bloqueante = bloqueante
    return item


def _quantidade_recebida_item(item):
    quantidade = item.quantidade_recebida
    if quantidade is None:
        quantidade = item.quantidade_estoque or item.quantidade
    return quantidade or Decimal('0')


def _permissoes_compras(request):
    usuario = request.user
    return {
        'pode_ver': usuario.tem_permissao('compras', 'ver'),
        'pode_criar': usuario.tem_permissao('compras', 'criar'),
        'pode_editar': usuario.tem_permissao('compras', 'editar'),
        'pode_cancelar': usuario.tem_permissao('compras', 'cancelar'),
        'pode_aprovar': usuario.tem_permissao('compras', 'aprovar'),
        'pode_exportar': usuario.tem_permissao('compras', 'exportar'),
        'pode_gerar_financeiro': usuario.tem_permissao('financeiro', 'criar'),
    }


def _auditar_entrada(request, acao, entrada, descricao='', justificativa='', antes=None, depois=None, relacionado=None, metadados=None):
    return registrar_auditoria(
        request=request,
        modulo='compras',
        acao=acao,
        objeto=entrada,
        descricao=descricao or f'NF {entrada.numero_nf}/{entrada.serie_nf}',
        justificativa=justificativa,
        antes=antes,
        depois=depois,
        relacionado=relacionado,
        metadados=metadados,
    )


def _redirect_entrada_duplicada(request, entrada: EntradaNF, origem: str = 'xml'):
    messages.warning(
        request,
        f'Esta nota ja existe nesta filial. Abrimos a NF {entrada.numero_nf}/{entrada.serie_nf} para voce revisar, continuar ou cancelar.',
    )
    return redirect(f"{reverse('compras:entrada-detail', args=[entrada.pk])}?duplicada={origem}")


class EntradaNFListView(PermissaoRequiredMixin, View):
    permissao_modulo = 'compras'
    template_name = 'compras/entrada/list.html'

    def get(self, request):
        base_qs = EntradaNF.objects.for_filial(request.filial_ativa).select_related(
            'fornecedor', 'pedido_compra', 'usuario',
        ).annotate(
            total_itens=Count('itens', distinct=True),
            sem_produto_count=Count('itens', filter=Q(itens__produto__isnull=True), distinct=True),
            divergencias_count=Count(
                'itens',
                filter=Q(itens__diferenca_tipo__gt=''),
                distinct=True,
            ),
            divergencias_bloqueantes_count=Count(
                'itens',
                filter=Q(itens__diferenca_bloqueante=True),
                distinct=True,
            ),
            lote_pendente_count=Count(
                'itens',
                filter=(
                    Q(itens__produto__controla_lote=True, itens__numero_lote='')
                    | Q(itens__produto__controla_validade=True, itens__data_validade__isnull=True)
                ),
                distinct=True,
            ),
        )
        qs = base_qs
        busca = request.GET.get('q', '').strip()
        status = request.GET.get('status', '')
        origem = request.GET.get('origem', '')
        grupo = request.GET.get('grupo', 'abertas')
        pendencia = request.GET.get('pendencia', '')
        if busca:
            qs = qs.filter(
                Q(numero_nf__icontains=busca)
                | Q(chave_acesso_nf__icontains=busca)
                | Q(fornecedor__razao_social__icontains=busca)
                | Q(fornecedor__nome_fantasia__icontains=busca)
                | Q(fornecedor__cpf_cnpj__icontains=busca)
                | Q(emitente_razao_social_xml__icontains=busca)
                | Q(emitente_cnpj_xml__icontains=busca)
                | Q(itens__descricao_xml__icontains=busca)
                | Q(itens__ean_xml__icontains=busca)
                | Q(itens__codigo_produto_fornecedor__icontains=busca)
                | Q(itens__produto__descricao__icontains=busca)
                | Q(itens__produto__codigo__icontains=busca)
                | Q(itens__produto__codigo_barras__icontains=busca)
            )
        if status:
            qs = qs.filter(status=status)
        if origem:
            qs = qs.filter(origem_entrada=origem)
        if grupo == 'abertas':
            qs = qs.exclude(status__in=[
                EntradaNF.Status.EFETIVADA,
                EntradaNF.Status.CANCELADA,
                EntradaNF.Status.ESTORNADA,
            ])
        elif grupo == 'historico':
            qs = qs.filter(status__in=[
                EntradaNF.Status.EFETIVADA,
                EntradaNF.Status.CANCELADA,
                EntradaNF.Status.ESTORNADA,
            ])
        if pendencia == 'fornecedor':
            qs = qs.filter(fornecedor_pendente=True)
        elif pendencia == 'sem_produto':
            qs = qs.filter(itens__produto__isnull=True)
        elif pendencia == 'divergencia':
            qs = qs.filter(itens__diferenca_tipo__gt='')
        elif pendencia == 'lote':
            qs = qs.filter(
                Q(itens__produto__controla_lote=True, itens__numero_lote='')
                | Q(itens__produto__controla_validade=True, itens__data_validade__isnull=True)
            )
        elif pendencia == 'custo':
            qs = qs.filter(
                itens__produto__isnull=False,
                itens__quantidade_recebida__gt=0,
                itens__custo_unitario_total__lte=0,
            )
        qs = qs.distinct()

        agregados = base_qs.values('status').annotate(total=Count('id'))
        totais_status = {item['status']: item['total'] for item in agregados}
        kpis = {
            chave: sum(totais_status.get(status_item, 0) for status_item in status_list)
            for chave, status_list in STATUS_KPI.items()
        }
        pendencias_totais = {
            'fornecedor': base_qs.filter(fornecedor_pendente=True).count(),
            'sem_produto': base_qs.filter(itens__produto__isnull=True).distinct().count(),
            'divergencia': base_qs.filter(itens__diferenca_tipo__gt='').distinct().count(),
            'lote': base_qs.filter(
                Q(itens__produto__controla_lote=True, itens__numero_lote='')
                | Q(itens__produto__controla_validade=True, itens__data_validade__isnull=True)
            ).distinct().count(),
            'custo': base_qs.filter(
                itens__produto__isnull=False,
                itens__quantidade_recebida__gt=0,
                itens__custo_unitario_total__lte=0,
            ).distinct().count(),
        }
        grupo_totais = {
            'abertas': base_qs.exclude(status__in=[
                EntradaNF.Status.EFETIVADA,
                EntradaNF.Status.CANCELADA,
                EntradaNF.Status.ESTORNADA,
            ]).count(),
            'historico': base_qs.filter(status__in=[
                EntradaNF.Status.EFETIVADA,
                EntradaNF.Status.CANCELADA,
                EntradaNF.Status.ESTORNADA,
            ]).count(),
        }

        page_obj = Paginator(qs.order_by('-data_entrada'), 25).get_page(request.GET.get('page'))
        entradas = list(page_obj.object_list)
        _preparar_entradas_para_lista(entradas)
        return render(request, self.template_name, {
            'page_obj': page_obj,
            'entradas': entradas,
            'busca': busca,
            'status': status,
            'origem': origem,
            'grupo': grupo,
            'pendencia': pendencia,
            'status_choices': EntradaNF.Status.choices,
            'origem_choices': EntradaNF.OrigemEntrada.choices,
            'kpis': kpis,
            'pendencias_totais': pendencias_totais,
            'grupo_totais': grupo_totais,
            'permissoes_compras': _permissoes_compras(request),
        })


def _preparar_entradas_para_lista(entradas):
    for entrada in entradas:
        pendencias = []
        custo_critico_count = _custo_critico_lista_count(entrada)
        entrada.custo_critico_count = custo_critico_count

        if entrada.fornecedor_pendente:
            pendencias.append({
                'chave': 'fornecedor',
                'label': 'Fornecedor pendente',
                'classe': 'is-amber',
                'total': 1,
            })
        if entrada.sem_produto_count:
            pendencias.append({
                'chave': 'sem_produto',
                'label': f'{entrada.sem_produto_count} sem produto',
                'classe': 'is-red',
                'total': entrada.sem_produto_count,
            })
        if entrada.divergencias_count:
            pendencias.append({
                'chave': 'divergencia',
                'label': f'{entrada.divergencias_count} divergencia(s)',
                'classe': 'is-red' if entrada.divergencias_bloqueantes_count else 'is-amber',
                'total': entrada.divergencias_count,
            })
        if entrada.lote_pendente_count:
            pendencias.append({
                'chave': 'lote',
                'label': f'{entrada.lote_pendente_count} lote/validade',
                'classe': 'is-red',
                'total': entrada.lote_pendente_count,
            })
        if custo_critico_count:
            pendencias.append({
                'chave': 'custo',
                'label': f'{custo_critico_count} custo critico',
                'classe': 'is-red',
                'total': custo_critico_count,
            })
        if entrada.destinatario_documento_diferente:
            pendencias.append({
                'chave': 'documento',
                'label': 'Documento em alerta',
                'classe': 'is-blue',
                'total': 1,
            })

        entrada.pendencias_lista = pendencias
        entrada.tem_pendencia_bloqueante = bool(
            entrada.sem_produto_count
            or entrada.divergencias_bloqueantes_count
            or entrada.lote_pendente_count
            or custo_critico_count
        )
        entrada.grupo_operacional = (
            'Historico'
            if entrada.status in (
                EntradaNF.Status.EFETIVADA,
                EntradaNF.Status.CANCELADA,
                EntradaNF.Status.ESTORNADA,
            )
            else 'Aberta'
        )
        entrada.proxima_acao = _proxima_acao_entrada(entrada)


def _custo_critico_lista_count(entrada) -> int:
    if entrada.status in (
        EntradaNF.Status.EFETIVADA,
        EntradaNF.Status.CANCELADA,
        EntradaNF.Status.ESTORNADA,
    ):
        return 0
    return entrada.itens.filter(
        produto__isnull=False,
        quantidade_recebida__gt=0,
        custo_unitario_total__lte=0,
    ).count()


def _proxima_acao_entrada(entrada):
    if entrada.status == EntradaNF.Status.EFETIVADA:
        return {
            'label': 'Ver resultado',
            'hint': 'Movimentos, lotes e custos gravados.',
            'url': reverse_lazy('compras:entrada-detail', kwargs={'pk': entrada.pk}),
            'classe': 'btn-table-blue',
            'requer': 'ver',
        }
    if entrada.status in (EntradaNF.Status.CANCELADA, EntradaNF.Status.ESTORNADA):
        return {
            'label': 'Ver auditoria',
            'hint': 'Nota fechada sem acao operacional.',
            'url': reverse_lazy('compras:entrada-detail', kwargs={'pk': entrada.pk}),
            'classe': 'btn-table-slate',
            'requer': 'ver',
        }
    if entrada.fornecedor_pendente:
        return {
            'label': 'Resolver fornecedor',
            'hint': 'Vincule ou cadastre o fornecedor do XML.',
            'url': reverse_lazy('compras:entrada-fornecedor-pendente', kwargs={'pk': entrada.pk}),
            'classe': 'btn-table-slate',
            'requer': 'editar',
        }
    if entrada.sem_produto_count:
        return {
            'label': 'Vincular produtos',
            'hint': 'Associe itens da nota ao cadastro interno.',
            'url': reverse_lazy('compras:entrada-conferencia', kwargs={'pk': entrada.pk}),
            'classe': 'btn-table-red',
            'requer': 'editar',
        }
    if entrada.lote_pendente_count:
        return {
            'label': 'Preencher lote',
            'hint': 'Complete lote e validade obrigatorios.',
            'url': reverse_lazy('compras:entrada-conferencia', kwargs={'pk': entrada.pk}),
            'classe': 'btn-table-red',
            'requer': 'editar',
        }
    if entrada.divergencias_count:
        return {
            'label': 'Resolver divergencias',
            'hint': 'Revise quantidade fisica e justificativas.',
            'url': reverse_lazy('compras:entrada-diferencas', kwargs={'pk': entrada.pk}),
            'classe': 'btn-table-slate',
            'requer': 'editar',
        }
    if entrada.custo_critico_count:
        return {
            'label': 'Revisar custos',
            'hint': 'Corrija custo antes de efetivar.',
            'url': reverse_lazy('compras:entrada-custos', kwargs={'pk': entrada.pk}),
            'classe': 'btn-table-red',
            'requer': 'editar',
        }
    if entrada.status in (EntradaNF.Status.CONFERIDA, EntradaNF.Status.COM_DIFERENCAS):
        return {
            'label': 'Revisar finalizacao',
            'hint': 'Confira resumo final antes de efetivar.',
            'url': reverse_lazy('compras:entrada-finalizacao', kwargs={'pk': entrada.pk}),
            'classe': 'btn-table-blue',
            'requer': 'aprovar',
        }
    if entrada.status == EntradaNF.Status.RASCUNHO:
        return {
            'label': 'Continuar cadastro',
            'hint': 'Inclua ou revise itens da entrada.',
            'url': reverse_lazy('compras:entrada-detail', kwargs={'pk': entrada.pk}),
            'classe': 'btn-table-blue',
            'requer': 'editar',
        }
    return {
        'label': 'Conferir',
        'hint': 'Revise produtos, quantidade, lote e validade.',
        'url': reverse_lazy('compras:entrada-conferencia', kwargs={'pk': entrada.pk}),
        'classe': 'btn-table-blue',
        'requer': 'editar',
    }


class EntradaNFLocalizarNotaView(PermissaoRequiredMixin, View):
    permissao_modulo = 'compras'
    template_name = 'compras/entrada/localizar_nota.html'

    def get(self, request):
        return render(request, self.template_name, {
            'permissoes_compras': _permissoes_compras(request),
        })


class EntradaNFImportarXMLView(PermissaoRequiredMixin, View):
    permissao_modulo = 'compras'
    permissao_acao = 'criar'
    template_name = 'compras/entrada/importar_xml.html'

    def get(self, request):
        return render(request, self.template_name, {'form': ImportarXMLForm()})

    def post(self, request):
        form = ImportarXMLForm(request.POST, request.FILES)
        if form.is_valid():
            arquivo = form.cleaned_data['arquivo_xml']
            raw = arquivo.read()
            try:
                xml_texto = raw.decode('utf-8')
            except UnicodeDecodeError:
                xml_texto = raw.decode('latin1')
            try:
                entrada = importar_xml_para_entrada(
                    xml_texto=xml_texto,
                    filial=request.filial_ativa,
                    usuario=request.user,
                    nome_arquivo=arquivo.name,
                )
                _auditar_entrada(
                    request,
                    'criar',
                    entrada,
                    'XML importado para entrada de mercadoria',
                    metadados={'arquivo': arquivo.name, 'origem': 'xml'},
                    depois=snapshot_modelo(entrada),
                )
                messages.success(request, f'XML importado. NF {entrada.numero_nf} pronta para conferencia.')
                return redirect('compras:entrada-conferencia', pk=entrada.pk)
            except DomainError as exc:
                if isinstance(exc, EntradaXMLDuplicadaError):
                    return _redirect_entrada_duplicada(request, exc.entrada, origem='xml')
                messages.error(request, str(exc))
        return render(request, self.template_name, {'form': form})


class EntradaNFConsultarChaveView(PermissaoRequiredMixin, View):
    permissao_modulo = 'compras'
    permissao_acao = 'criar'
    template_name = 'compras/entrada/consultar_chave.html'

    def get(self, request):
        return render(request, self.template_name, {'form': ConsultarChaveForm()})

    def post(self, request):
        form = ConsultarChaveForm(request.POST)
        if form.is_valid():
            chave = form.cleaned_data['chave_acesso']
            entrada_existente = EntradaNF.objects.for_filial(request.filial_ativa).filter(chave_acesso_nf=chave).first()
            if entrada_existente:
                return _redirect_entrada_duplicada(request, entrada_existente, origem='chave')
            cnpj_emitente = chave[6:20]
            fornecedor, fornecedor_pendente = localizar_fornecedor(request.filial_ativa, cnpj_emitente)
            entrada = CompraService.criar_entrada_nf(
                filial=request.filial_ativa,
                usuario=request.user,
                fornecedor=fornecedor,
                numero_nf=chave[25:34].lstrip('0') or chave[25:34],
                serie_nf=chave[22:25].lstrip('0') or '1',
                data_emissao_nf=timezone.localdate(),
                chave_acesso_nf=chave,
                origem_entrada=EntradaNF.OrigemEntrada.CHAVE,
                fornecedor_pendente=fornecedor_pendente,
                dados_emitente_xml={'documento': cnpj_emitente},
                observacao='Criada pela chave de acesso. Consulta DF-e real ainda pendente.',
            )
            _auditar_entrada(
                request,
                'criar',
                entrada,
                'Entrada criada por chave de acesso',
                metadados={'chave': chave, 'origem': 'chave'},
                depois=snapshot_modelo(entrada),
            )
            messages.warning(
                request,
                'Chave registrada. Como a consulta SEFAZ real ainda esta em preparacao, confira ou preencha os itens manualmente.',
            )
            return redirect('compras:entrada-detail', pk=entrada.pk)
        return render(request, self.template_name, {'form': form})


class EntradaNFCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'compras'
    permissao_acao = 'criar'
    template_name = 'compras/entrada/criar.html'

    def get(self, request):
        initial = {}
        if request.GET.get('chave'):
            initial['chave_acesso_nf'] = request.GET['chave']
        pedido_selecionado = None
        if request.GET.get('pedido_compra'):
            pedido_selecionado = (
                PedidoCompra.objects
                .for_filial(request.filial_ativa)
                .filter(pk=request.GET.get('pedido_compra'))
                .first()
            )
            if pedido_selecionado:
                initial['pedido_compra'] = pedido_selecionado.pk
                initial['fornecedor'] = pedido_selecionado.fornecedor_id
        return render(request, self.template_name, {
            'form': EntradaNFForm(initial=initial, filial=request.filial_ativa),
            'title': 'Entrada manual',
            'cancel_url': reverse_lazy('compras:entrada-list'),
            'pedido_selecionado': pedido_selecionado,
        })

    def post(self, request):
        form = EntradaNFForm(request.POST, filial=request.filial_ativa)
        if form.is_valid():
            try:
                fornecedor = form.cleaned_data.get('fornecedor')
                fornecedor_pendente = False
                if not fornecedor:
                    fornecedor = get_fornecedor_padrao(request.filial_ativa)
                    fornecedor_pendente = True
                entrada = CompraService.criar_entrada_nf(
                    filial=request.filial_ativa,
                    usuario=request.user,
                    fornecedor=fornecedor,
                    numero_nf=form.cleaned_data['numero_nf'],
                    serie_nf=form.cleaned_data.get('serie_nf') or '1',
                    data_emissao_nf=form.cleaned_data['data_emissao_nf'],
                    chave_acesso_nf=form.cleaned_data.get('chave_acesso_nf', ''),
                    pedido_compra=form.cleaned_data.get('pedido_compra'),
                    observacao=form.cleaned_data.get('observacao', ''),
                    origem_entrada=EntradaNF.OrigemEntrada.MANUAL,
                    fornecedor_pendente=fornecedor_pendente,
                )
                for campo in ('tipo', 'valor_frete', 'valor_seguro', 'valor_outras_despesas'):
                    setattr(entrada, campo, form.cleaned_data.get(campo) or 0)
                entrada.save()
                _auditar_entrada(
                    request,
                    'criar',
                    entrada,
                    'Entrada manual criada',
                    metadados={'origem': 'manual'},
                    depois=snapshot_modelo(entrada),
                )
                messages.success(request, f'Entrada NF {entrada.numero_nf} criada. Adicione os itens.')
                return redirect('compras:entrada-detail', pk=entrada.pk)
            except DomainError as e:
                messages.error(request, str(e))
        return render(request, self.template_name, {
            'form': form,
            'title': 'Entrada manual',
            'cancel_url': reverse_lazy('compras:entrada-list'),
        })


class EntradaNFDetailView(PermissaoRequiredMixin, View):
    permissao_modulo = 'compras'
    template_name = 'compras/entrada/detail.html'

    def dispatch(self, request, *args, **kwargs):
        pk = kwargs.get('pk')
        if pk and not EntradaNF.objects.for_filial(request.filial_ativa).filter(pk=pk).exists():
            entrada_existe = EntradaNF.objects.select_related('filial').filter(pk=pk).first()
            if entrada_existe:
                messages.warning(
                    request,
                    (
                        'Esta entrada pertence a outra filial. Voce foi direcionado '
                        'para as entradas da filial ativa para evitar editar a nota no contexto errado.'
                    ),
                )
                return redirect(f"{reverse('compras:entrada-list')}?fora_filial=1")
        return super().dispatch(request, *args, **kwargs)

    def get_entrada(self, request, pk):
        return get_object_or_404(
            EntradaNF.objects.for_filial(request.filial_ativa)
            .select_related('fornecedor', 'pedido_compra', 'usuario', 'usuario_efetivacao', 'usuario_estorno'),
            pk=pk,
        )

    def get(self, request, pk):
        entrada = self.get_entrada(request, pk)
        itens = list(entrada.itens.select_related('produto', 'produto__unidade_medida', 'lote_gerado').all())
        for item in itens:
            item.quantidade_movimenta = _quantidade_recebida_item(item)
            item.item_recusado = (
                item.quantidade_movimenta <= 0
                and bool(item.justificativa_diferenca)
            )
            if item.produto_id:
                item.extrato_produto_url = (
                    f"{reverse('estoque:movimentacao-list')}?produto={item.produto_id}"
                )
                item.movimentacoes_nota_url = _movimentacoes_entrada_url(entrada)
        prontidao_pos_entrada = None
        if entrada.status in (EntradaNF.Status.EFETIVADA, EntradaNF.Status.ESTORNADA):
            prontidao_pos_entrada = avaliar_entrada_pos_efetivacao(entrada, itens)
        return render(request, self.template_name, {
            'entrada': entrada,
            'itens': itens,
            'resultado_efetivacao': _resultado_efetivacao_entrada(request, entrada, itens),
            'prontidao_pos_entrada': prontidao_pos_entrada,
            'auditoria_entrada': list(auditoria_para_objeto(entrada, limit=12)),
            'permissoes_compras': _permissoes_compras(request),
            'entrada_alerta_duplicada': request.GET.get('duplicada') in {'xml', 'chave'},
            'entrada_pode_cancelar': entrada.pode_cancelar,
            'entrada_pode_estornar': entrada.pode_estornar,
            'adicionar_item_form': (
                AdicionarItemEntradaForm(filial=request.filial_ativa)
                if _entrada_aberta(entrada)
                else None
            ),
        })


class EntradaNFConferenciaView(EntradaNFDetailView):
    template_name = 'compras/entrada/conferencia.html'

    def get(self, request, pk):
        entrada = self.get_entrada(request, pk)
        # Poe a nota em dia antes de mostra-la: solta item cuja equivalencia
        # foi desativada e amarra o que ja' da' para amarrar. Nunca derruba a
        # tela -- conferencia que nao abre e' pior do que conferencia
        # desatualizada, e o botao de reprocessar continua ali para forcar.
        if _entrada_aberta(entrada):
            try:
                sincronizar_vinculos_da_conferencia(entrada)
                entrada.refresh_from_db()
            except Exception:  # noqa: BLE001
                logger.exception(
                    'Falha ao sincronizar vinculos da entrada %s', entrada.pk,
                )
        itens = entrada.itens.select_related('produto', 'produto__unidade_medida').all()
        custo_por_item = {}
        custos_criticos = set()
        try:
            composicao_custo = EntradaCustoService.compor(
                entrada=entrada,
                metodo_rateio=entrada.custo_rateio_metodo,
                incluir_ipi=entrada.custo_incluir_ipi,
                incluir_icms_st=entrada.custo_incluir_icms_st,
                incluir_icms=entrada.custo_incluir_icms,
                custo_financeiro=entrada.custo_financeiro or Decimal('0'),
            )
            custo_por_item = {
                linha.item.pk: linha
                for linha in composicao_custo.get('linhas', [])
            }
            custos_criticos = {
                linha.item.pk
                for linha in composicao_custo.get('alertas_custo', [])
                if linha.alerta_custo_nivel == 'critico'
            }
        except DomainError:
            composicao_custo = None
        sugestoes_em_lote = []
        resumo_status = {
            'vinculados': 0,
            'sugeridos': 0,
            'sem_produto': 0,
            'divergencias': 0,
            'lote_pendente': 0,
            'custo_critico': 0,
        }
        itens_mobile = []
        for item in itens:
            _avaliar_diferenca_item_para_tela(item)
            item.sugestoes_produto = (
                sugerir_produtos_para_item(item, request.filial_ativa)
                if not item.produto_id
                else []
            )
            item.sugestao_principal = item.sugestoes_produto[0] if item.sugestoes_produto else None
            if item.sugestao_principal:
                sugestoes_em_lote.append(item)
            item.lote_pendente = bool(
                item.produto_id
                and _quantidade_recebida_item(item) > 0
                and (
                    (item.produto.controla_lote and not item.numero_lote)
                    or (item.produto.controla_validade and not item.data_validade)
                )
            )
            item.linha_custo_preview = custo_por_item.get(item.pk)
            item.custo_critico = item.pk in custos_criticos
            item.status_flags = []
            if item.produto_id:
                resumo_status['vinculados'] += 1
                item.status_flags.append(('Vinculado', 'is-green'))
            elif item.sugestao_principal:
                resumo_status['sugeridos'] += 1
                item.status_flags.append(('Sugerido', 'is-amber'))
            else:
                resumo_status['sem_produto'] += 1
                item.status_flags.append(('Sem produto', 'is-red'))
            if item.diferenca_tipo and item.diferenca_tipo != 'produto_sem_vinculo':
                resumo_status['divergencias'] += 1
                item.status_flags.append((
                    'Divergencia',
                    'is-red' if item.diferenca_bloqueante else 'is-amber',
                ))
            if item.lote_pendente:
                resumo_status['lote_pendente'] += 1
                item.status_flags.append(('Lote pendente', 'is-red'))
            if item.custo_critico:
                resumo_status['custo_critico'] += 1
                item.status_flags.append(('Custo critico', 'is-red'))
            if item.custo_critico or item.lote_pendente or item.diferenca_bloqueante or (not item.produto_id and not item.sugestao_principal):
                item.status_severidade = 'critico'
            elif item.diferenca_tipo or item.sugestao_principal:
                item.status_severidade = 'atencao'
            else:
                item.status_severidade = 'ok'
            item.mobile_status_keys = ['todos']
            if item.status_severidade != 'ok':
                item.mobile_status_keys.append('pendentes')
            if item.sugestao_principal:
                item.mobile_status_keys.append('sugeridos')
            if not item.produto_id and not item.sugestao_principal:
                item.mobile_status_keys.append('sem_produto')
            if item.lote_pendente:
                item.mobile_status_keys.append('lote')
            if item.custo_critico:
                item.mobile_status_keys.append('custo')
            if item.diferenca_tipo and item.diferenca_tipo != 'produto_sem_vinculo':
                item.mobile_status_keys.append('divergencia')

            if item.custo_critico:
                item.mobile_action_label = 'Revisar custo'
                item.mobile_action_hint = 'Custo composto fora da referencia.'
                item.mobile_action_url = reverse_lazy('compras:entrada-custos', kwargs={'pk': entrada.pk})
                item.mobile_priority = 10
            elif item.lote_pendente:
                item.mobile_action_label = 'Preencher lote'
                item.mobile_action_hint = 'Informe lote ou validade obrigatoria.'
                item.mobile_action_url = f'#mobile-edit-item-{item.pk}'
                item.mobile_priority = 20
            elif not item.produto_id and item.sugestao_principal:
                item.mobile_action_label = 'Confirmar sugestao'
                item.mobile_action_hint = 'Existe produto parecido para vincular.'
                item.mobile_action_url = f'#mobile-suggestions-item-{item.pk}'
                item.mobile_priority = 30
            elif not item.produto_id:
                item.mobile_action_label = 'Vincular produto'
                item.mobile_action_hint = 'Escolha produto interno ou cadastre pelo XML.'
                item.mobile_action_url = f'#mobile-edit-item-{item.pk}'
                item.mobile_priority = 40
            elif item.diferenca_tipo:
                item.mobile_action_label = 'Corrigir divergencia'
                item.mobile_action_hint = item.diferenca_descricao or 'Revise a divergencia do item.'
                item.mobile_action_url = f'#mobile-edit-item-{item.pk}'
                item.mobile_priority = 50
            else:
                item.mobile_action_label = 'Pronto'
                item.mobile_action_hint = 'Item pronto para finalizacao.'
                item.mobile_action_url = '#'
                item.mobile_priority = 90
            item.mobile_status_data = ' '.join(item.mobile_status_keys)
            itens_mobile.append(item)
        resumo_status['pendentes'] = sum(1 for item in itens_mobile if item.status_severidade != 'ok')
        itens_mobile.sort(key=lambda item: (item.mobile_priority, item.numero_item or 0, item.pk))
        status_cards = [
            {
                'chave': 'vinculados',
                'titulo': 'Vinculados',
                'valor': resumo_status['vinculados'],
                'classe': 'is-green',
                'texto': 'Ja possuem produto interno definido.',
                'acao': 'Revisar itens',
                'url': '#itens-conferencia',
            },
            {
                'chave': 'sugeridos',
                'titulo': 'Sugeridos',
                'valor': resumo_status['sugeridos'],
                'classe': 'is-amber',
                'texto': 'Ha produto parecido para confirmar.',
                'acao': 'Confirmar sugestoes',
                'url': '#sugestoes-conferencia',
            },
            {
                'chave': 'sem_produto',
                'titulo': 'Sem produto',
                'valor': resumo_status['sem_produto'],
                'classe': 'is-red',
                'texto': 'Precisa vincular ou cadastrar produto.',
                'acao': 'Resolver vinculo',
                'url': '#itens-conferencia',
            },
            {
                'chave': 'divergencias',
                'titulo': 'Com divergencia',
                'valor': resumo_status['divergencias'],
                'classe': 'is-amber',
                'texto': 'Quantidade, lote, validade ou regra pendente.',
                'acao': 'Abrir divergencias',
                'url': '#itens-conferencia',
            },
            {
                'chave': 'lote_pendente',
                'titulo': 'Lote pendente',
                'valor': resumo_status['lote_pendente'],
                'classe': 'is-red',
                'texto': 'Produto exige lote ou validade antes de efetivar.',
                'acao': 'Preencher lote',
                'url': '#itens-conferencia',
            },
            {
                'chave': 'custo_critico',
                'titulo': 'Custo critico',
                'valor': resumo_status['custo_critico'],
                'classe': 'is-red',
                'texto': 'Custo fora da referencia cadastrada.',
                'acao': 'Abrir custos',
                'url': reverse_lazy('compras:entrada-custos', kwargs={'pk': entrada.pk}),
            },
        ]
        mobile_filter_cards = [
            {'chave': 'pendentes', 'titulo': 'Pendentes', 'valor': resumo_status['pendentes']},
            {'chave': 'sugeridos', 'titulo': 'Sugeridos', 'valor': resumo_status['sugeridos']},
            {'chave': 'sem_produto', 'titulo': 'Sem produto', 'valor': resumo_status['sem_produto']},
            {'chave': 'lote', 'titulo': 'Lote', 'valor': resumo_status['lote_pendente']},
            {'chave': 'custo', 'titulo': 'Custo', 'valor': resumo_status['custo_critico']},
        ]
        produtos = Produto.objects.for_filial(request.filial_ativa).filter(ativo=True).order_by('descricao')
        return render(request, self.template_name, {
            'entrada': entrada,
            'itens': itens,
            'itens_mobile': itens_mobile,
            'produtos': produtos,
            'sugestoes_em_lote': sugestoes_em_lote,
            'resumo_status': resumo_status,
            'status_cards': status_cards,
            'mobile_filter_cards': mobile_filter_cards,
            'composicao_custo': composicao_custo,
            'permissoes_compras': _permissoes_compras(request),
        })


class EntradaNFVincularItemView(PermissaoRequiredMixin, View):
    permissao_modulo = 'compras'
    permissao_acao = 'editar'

    def post(self, request, pk, item_id):
        entrada = get_object_or_404(EntradaNF.objects.for_filial(request.filial_ativa), pk=pk)
        if not _entrada_aberta(entrada):
            messages.error(request, 'Entrada efetivada nao permite trocar produto, lote ou validade.')
            return redirect('compras:entrada-detail', pk=entrada.pk)
        item = get_object_or_404(entrada.itens.all(), pk=item_id)
        produto = get_object_or_404(
            Produto.objects.for_filial(request.filial_ativa).filter(ativo=True),
            pk=request.POST.get('produto'),
        )
        fator = _decimal_localizado(request.POST.get('fator_conversao'), item.fator_conversao or Decimal('1'))
        unidade_estoque = request.POST.get('unidade_estoque') or produto.unidade_medida.sigla
        validade = parse_date(request.POST.get('data_validade') or '')
        antes = snapshot_modelo(item)
        vincular_item_a_produto(
            entrada=entrada,
            item=item,
            produto=produto,
            fator_conversao=fator,
            unidade_estoque=unidade_estoque,
            numero_lote=request.POST.get('numero_lote', item.numero_lote),
            data_validade=validade,
        )
        CompraService._atualizar_status_conferencia(entrada)
        item.refresh_from_db()
        _auditar_entrada(
            request,
            'vincular',
            entrada,
            f'Item {item.numero_item} vinculado ao produto {produto.descricao}',
            antes=antes,
            depois=snapshot_modelo(item),
            relacionado=item,
            metadados={'produto_id': produto.pk, 'fator_conversao': str(fator)},
        )
        messages.success(request, 'Produto vinculado e equivalencia salva para proximas entradas.')
        return redirect('compras:entrada-conferencia', pk=pk)


class EntradaNFDesvincularItemView(PermissaoRequiredMixin, View):
    """
    Solta o item do produto na tela de conferencia. E' o inverso de
    `EntradaNFVincularItemView`.

    E' O UNICO JEITO DE DESFAZER UM VINCULO AUTOMATICO. O reprocessamento
    vincula por EAN e por codigo do fornecedor; quando ele acerta o produto
    errado -- EAN reaproveitado, codigo repetido entre itens -- o conferente
    nao tinha como recusar. Trocar por outro produto so' resolve se ele souber
    qual e' o certo; muitas vezes o certo e' "nenhum, ainda".

    E O MARCADOR E' O QUE FAZ ISSO DURAR. `desvincular_item_de_produto` carimba
    o item; sem o carimbo, o proximo reprocessamento -- que a propria tela de
    conferencia dispara -- reataria o item ao mesmo produto errado, e o
    conferente veria seu clique ser desfeito sozinho.

    O carimbo cede em um caso so': se o produto passar a ter o EAN da nota
    DEPOIS do desvinculo, o revinculo volta a valer. Ali a identificacao deixou
    de ser palpite.
    """
    permissao_modulo = 'compras'
    permissao_acao = 'editar'

    def post(self, request, pk, item_id):
        entrada = get_object_or_404(EntradaNF.objects.for_filial(request.filial_ativa), pk=pk)
        if not _entrada_aberta(entrada):
            messages.error(request, 'Entrada efetivada nao permite desvincular item.')
            return redirect('compras:entrada-detail', pk=entrada.pk)
        item = get_object_or_404(entrada.itens.all(), pk=item_id)
        if not item.produto_id:
            messages.info(request, 'Item ja esta sem produto vinculado.')
            return redirect('compras:entrada-conferencia', pk=pk)

        antes = snapshot_modelo(item)
        descricao_produto = item.produto.descricao
        desvincular_item_de_produto(item)
        # O item ficou sem produto: a entrada nao pode seguir dizendo que esta
        # pronta para conferir. Mesma chamada que a view de vincular faz.
        CompraService._atualizar_status_conferencia(entrada)
        item.refresh_from_db()
        _auditar_entrada(
            request,
            'desvincular',
            entrada,
            f'Item {item.numero_item} desvinculado do produto {descricao_produto}',
            antes=antes,
            depois=snapshot_modelo(item),
            relacionado=item,
        )
        messages.success(
            request,
            'Item desvinculado. Ele nao sera revinculado automaticamente.',
        )
        return redirect('compras:entrada-conferencia', pk=pk)


class EntradaNFVincularSugestoesView(PermissaoRequiredMixin, View):
    permissao_modulo = 'compras'
    permissao_acao = 'editar'

    def post(self, request, pk):
        entrada = get_object_or_404(EntradaNF.objects.for_filial(request.filial_ativa), pk=pk)
        if not _entrada_aberta(entrada):
            messages.error(request, 'Entrada fechada nao permite alterar vinculos.')
            return redirect('compras:entrada-conferencia', pk=entrada.pk)

        item_ids = request.POST.getlist('item')
        if not item_ids:
            messages.warning(request, 'Selecione ao menos uma sugestao para confirmar.')
            return redirect('compras:entrada-conferencia', pk=entrada.pk)

        vinculados = 0
        ignorados = 0
        with transaction.atomic():
            itens = (
                entrada.itens
                .filter(pk__in=item_ids, produto__isnull=True)
                .select_related('produto')
            )
            for item in itens:
                produto_id = request.POST.get(f'produto_{item.pk}')
                sugestoes = sugerir_produtos_para_item(item, request.filial_ativa)
                sugestao = next(
                    (
                        item_sugestao
                        for item_sugestao in sugestoes
                        if str(item_sugestao.produto.pk) == str(produto_id)
                    ),
                    None,
                )
                if not sugestao:
                    ignorados += 1
                    continue

                try:
                    fator = _decimal_localizado(
                        request.POST.get(f'fator_conversao_{item.pk}'),
                        item.fator_conversao or Decimal('1'),
                    )
                except (InvalidOperation, ValueError):
                    ignorados += 1
                    continue

                if fator <= 0:
                    ignorados += 1
                    continue

                unidade_estoque = (
                    request.POST.get(f'unidade_estoque_{item.pk}')
                    or sugestao.produto.unidade_medida.sigla
                )
                validade = (
                    parse_date(request.POST.get(f'data_validade_{item.pk}') or '')
                    or item.data_validade
                )
                antes = snapshot_modelo(item)
                vincular_item_a_produto(
                    entrada=entrada,
                    item=item,
                    produto=sugestao.produto,
                    fator_conversao=fator,
                    unidade_estoque=unidade_estoque.strip()[:6],
                    numero_lote=request.POST.get(f'numero_lote_{item.pk}', item.numero_lote),
                    data_validade=validade,
                )
                item.refresh_from_db()
                _auditar_entrada(
                    request,
                    'vincular',
                    entrada,
                    f'Sugestao confirmada no item {item.numero_item}',
                    antes=antes,
                    depois=snapshot_modelo(item),
                    relacionado=item,
                    metadados={'produto_id': sugestao.produto.pk, 'origem': 'sugestao'},
                )
                vinculados += 1
            CompraService._atualizar_status_conferencia(entrada)

        if vinculados:
            messages.success(request, f'{vinculados} sugestao(oes) vinculada(s).')
        if ignorados:
            messages.warning(request, f'{ignorados} sugestao(oes) foram ignorada(s) por seguranca.')
        if not vinculados and not ignorados:
            messages.warning(request, 'Nenhum item pendente foi encontrado para confirmar.')
        return redirect('compras:entrada-conferencia', pk=entrada.pk)


class EntradaNFReprocessarVinculosView(PermissaoRequiredMixin, View):
    permissao_modulo = 'compras'
    permissao_acao = 'editar'

    def post(self, request, pk):
        entrada = get_object_or_404(
            EntradaNF.objects.for_filial(request.filial_ativa).select_related('fornecedor'),
            pk=pk,
        )
        if not _entrada_aberta(entrada):
            messages.error(request, 'Entrada fechada nao permite reprocessar vinculos.')
            return redirect('compras:entrada-conferencia', pk=entrada.pk)

        resultado = reprocessar_vinculos_automaticos(entrada)
        vinculados = resultado['vinculados']
        pendentes = resultado['pendentes']
        _auditar_entrada(
            request,
            'reprocessar',
            entrada,
            'Vinculos da entrada reprocessados',
            metadados={'vinculados': vinculados, 'pendentes': pendentes},
        )
        if vinculados:
            messages.success(
                request,
                f'{vinculados} item(ns) vinculado(s) automaticamente por EAN ou equivalencia segura.',
            )
        elif pendentes:
            messages.warning(
                request,
                'Nenhum novo vinculo seguro foi encontrado. Revise as sugestoes por nome ou cadastre pelo XML.',
            )
        else:
            messages.info(request, 'Nao havia itens pendentes para reprocessar.')
        return redirect('compras:entrada-conferencia', pk=entrada.pk)


class EntradaNFCriarProdutoItemView(PermissaoRequiredMixin, View):
    permissao_modulo = 'compras'
    permissao_acao = 'editar'

    def post(self, request, pk, item_id):
        entrada = get_object_or_404(EntradaNF.objects.for_filial(request.filial_ativa), pk=pk)
        if not _entrada_aberta(entrada):
            messages.error(request, 'Entrada efetivada nao permite cadastrar ou trocar produto pelo XML.')
            return redirect('compras:entrada-detail', pk=entrada.pk)
        item = get_object_or_404(entrada.itens.all(), pk=item_id)
        try:
            antes = snapshot_modelo(item)
            produto = criar_produto_e_vincular_item(entrada, item)
            CompraService._atualizar_status_conferencia(entrada)
            item.refresh_from_db()
            _auditar_entrada(
                request,
                'criar',
                entrada,
                f'Produto cadastrado pelo XML e vinculado ao item {item.numero_item}',
                antes=antes,
                depois=snapshot_modelo(item),
                relacionado=produto,
                metadados={'produto_id': produto.pk, 'item_id': item.pk},
            )
            messages.success(request, f'Produto "{produto.descricao}" cadastrado e vinculado ao item.')
        except Exception as exc:
            messages.error(request, f'Nao foi possivel cadastrar o produto: {exc}')
        return redirect('compras:entrada-conferencia', pk=pk)


class EntradaNFFornecedorPendenteView(EntradaNFDetailView):
    permissao_acao = 'editar'
    template_name = 'compras/entrada/fornecedor_pendente.html'

    def get(self, request, pk):
        entrada = self.get_entrada(request, pk)
        fornecedores = Fornecedor.objects.for_filial(request.filial_ativa).filter(ativo=True).order_by('razao_social')
        return render(request, self.template_name, {
            'entrada': entrada,
            'fornecedores': fornecedores,
        })

    def post(self, request, pk):
        entrada = self.get_entrada(request, pk)
        acao = request.POST.get('acao', 'vincular')
        if acao == 'criar_xml':
            try:
                antes = snapshot_modelo(entrada)
                with transaction.atomic():
                    fornecedor = _criar_fornecedor_do_xml(entrada)
                    entrada.fornecedor = fornecedor
                    entrada.fornecedor_pendente = False
                    entrada.save(update_fields=['fornecedor', 'fornecedor_pendente', 'updated_at'])
                    atualizadas = _atualizar_equivalencias_fornecedor(entrada)
                mensagem = 'Fornecedor criado a partir do XML e vinculado a entrada.'
                if atualizadas:
                    mensagem += f' {atualizadas} equivalencia(s) pendente(s) foram atualizadas.'
                entrada.refresh_from_db()
                _auditar_entrada(
                    request,
                    'vincular',
                    entrada,
                    'Fornecedor criado pelo XML e vinculado a entrada',
                    antes=antes,
                    depois=snapshot_modelo(entrada),
                    relacionado=fornecedor,
                    metadados={'fornecedor_id': fornecedor.pk, 'equivalencias_atualizadas': atualizadas},
                )
                messages.success(request, mensagem)
            except DomainError as exc:
                messages.error(request, str(exc))
            return redirect('compras:entrada-detail', pk=entrada.pk)

        fornecedor_id = request.POST.get('fornecedor')
        if fornecedor_id:
            fornecedor = get_object_or_404(
                Fornecedor.objects.for_filial(request.filial_ativa).filter(ativo=True),
                pk=fornecedor_id,
            )
            antes = snapshot_modelo(entrada)
            with transaction.atomic():
                entrada.fornecedor = fornecedor
                entrada.fornecedor_pendente = False
                entrada.save(update_fields=['fornecedor', 'fornecedor_pendente', 'updated_at'])
                atualizadas = _atualizar_equivalencias_fornecedor(entrada)
            mensagem = 'Fornecedor vinculado a entrada.'
            if atualizadas:
                mensagem += f' {atualizadas} equivalencia(s) pendente(s) foram atualizadas.'
            entrada.refresh_from_db()
            _auditar_entrada(
                request,
                'vincular',
                entrada,
                'Fornecedor vinculado a entrada',
                antes=antes,
                depois=snapshot_modelo(entrada),
                relacionado=fornecedor,
                metadados={'fornecedor_id': fornecedor.pk, 'equivalencias_atualizadas': atualizadas},
            )
            messages.success(request, mensagem)
        return redirect('compras:entrada-detail', pk=entrada.pk)


class EntradaNFDiferencasView(EntradaNFDetailView):
    template_name = 'compras/entrada/diferencas.html'

    def get_context(self, entrada, usuario=None):
        pode_editar = (
            usuario.tem_permissao('compras', 'editar')
            if usuario and usuario.is_authenticated
            else False
        )
        todos_itens = list(
            entrada.itens
            .select_related('produto')
            .order_by('numero_item', 'pk')
        )
        itens = []
        for item in todos_itens:
            _avaliar_diferenca_item_para_tela(item)
            if item.diferenca_tipo or item.diferenca_bloqueante or not item.produto_id:
                itens.append(item)
        return {
            'entrada': entrada,
            'itens': itens,
            'total_itens': len(todos_itens),
            'total_diferencas': len(itens),
            'total_bloqueantes': sum(1 for item in itens if item.diferenca_bloqueante or not item.produto_id),
            'total_alertas': sum(1 for item in itens if item.diferenca_tipo and not item.diferenca_bloqueante),
            'pode_editar_diferencas': _entrada_aberta(entrada) and pode_editar,
        }

    def get(self, request, pk):
        entrada = self.get_entrada(request, pk)
        contexto = self.get_context(entrada, request.user)
        contexto['permissoes_compras'] = _permissoes_compras(request)
        return render(request, self.template_name, contexto)

    def post(self, request, pk):
        entrada = self.get_entrada(request, pk)
        if not request.user.tem_permissao('compras', 'editar'):
            messages.error(request, PERMISSION_DENIED_MESSAGE)
            return redirect('compras:entrada-diferencas', pk=entrada.pk)
        if not _entrada_aberta(entrada):
            messages.error(request, 'Entrada fechada nao permite alterar diferencas.')
            return redirect('compras:entrada-diferencas', pk=entrada.pk)

        item = get_object_or_404(
            entrada.itens.select_related('produto'),
            pk=request.POST.get('item_id'),
        )
        antes = snapshot_modelo(item)
        try:
            item.quantidade_recebida = _decimal_localizado(
                request.POST.get('quantidade_recebida'),
                item.quantidade_recebida or item.quantidade_estoque,
            )
        except (InvalidOperation, ValueError):
            messages.error(request, 'Quantidade recebida invalida.')
            return redirect('compras:entrada-diferencas', pk=entrada.pk)

        item.numero_lote = (request.POST.get('numero_lote') or '').strip()
        item.data_validade = parse_date(request.POST.get('data_validade') or '')
        item.justificativa_diferenca = (request.POST.get('justificativa_diferenca') or '').strip()
        _atualizar_diferenca_item(item)
        CompraService._atualizar_status_conferencia(entrada)
        item.refresh_from_db()
        _auditar_entrada(
            request,
            'editar',
            entrada,
            f'Divergencia/conferencia alterada no item {item.numero_item}',
            justificativa=item.justificativa_diferenca,
            antes=antes,
            depois=snapshot_modelo(item),
            relacionado=item,
            metadados={'diferenca_tipo': item.diferenca_tipo, 'diferenca_bloqueante': item.diferenca_bloqueante},
        )

        if item.diferenca_bloqueante:
            messages.warning(request, 'Diferenca salva, mas ainda bloqueia a finalizacao.')
        elif item.diferenca_tipo:
            messages.success(request, 'Diferenca justificada. A entrada segue como alerta operacional.')
        else:
            messages.success(request, 'Diferenca resolvida.')
        return redirect('compras:entrada-diferencas', pk=entrada.pk)



class EntradaNFFinanceiroView(EntradaNFDetailView):
    template_name = 'compras/entrada/financeiro.html'

    def get_context(self, entrada, usuario=None):
        parcelas = list(entrada.parcelas_financeiras.all())
        total_parcelas = sum((parcela.valor for parcela in parcelas), Decimal('0'))
        diferenca_total = entrada.valor_total - total_parcelas
        pendentes_geracao = [
            parcela for parcela in parcelas
            if parcela.status == EntradaNFParcela.Status.PENDENTE and not parcela.conta_pagar_id
        ]
        bloqueios_geracao = validar_geracao_contas_pagar(entrada)
        pode_criar_contas = (
            usuario.tem_permissao('financeiro', 'criar')
            if usuario and usuario.is_authenticated
            else False
        )
        if not pode_criar_contas:
            bloqueios_geracao.append('Usuario sem permissao para criar contas a pagar.')
        return {
            'entrada': entrada,
            'parcelas': parcelas,
            'form': EntradaNFParcelaForm(),
            'total_parcelas': total_parcelas,
            'diferenca_total': diferenca_total,
            'pode_editar_financeiro': _entrada_aberta(entrada) and pode_criar_contas,
            'parcelas_pendentes_geracao': pendentes_geracao,
            'contas_geradas_count': sum(1 for parcela in parcelas if parcela.conta_pagar_id),
            'bloqueios_geracao': bloqueios_geracao,
            'pode_gerar_contas': bool(pendentes_geracao) and not bloqueios_geracao,
        }

    def get(self, request, pk):
        entrada = self.get_entrada(request, pk)
        contexto = self.get_context(entrada, request.user)
        contexto['permissoes_compras'] = _permissoes_compras(request)
        return render(request, self.template_name, contexto)

    def post(self, request, pk):
        entrada = self.get_entrada(request, pk)
        if not request.user.tem_permissao('financeiro', 'criar'):
            messages.error(request, PERMISSION_DENIED_MESSAGE)
            return redirect('compras:entrada-financeiro', pk=entrada.pk)
        if not _entrada_aberta(entrada):
            messages.error(request, 'Entrada fechada nao permite alterar parcelas.')
            return redirect('compras:entrada-financeiro', pk=entrada.pk)

        form = EntradaNFParcelaForm(request.POST)
        if form.is_valid():
            parcela = form.save(commit=False)
            parcela.entrada = entrada
            parcela.origem = EntradaNFParcela.Origem.MANUAL
            parcela.status = EntradaNFParcela.Status.PENDENTE
            parcela.fornecedor_pendente = entrada.fornecedor_pendente
            parcela.emitente_documento_xml = entrada.emitente_cnpj_xml
            parcela.emitente_nome_xml = entrada.emitente_razao_social_xml
            if not parcela.numero:
                proximo = entrada.parcelas_financeiras.count() + 1
                parcela.numero = str(proximo).zfill(3)
            parcela.save()
            registrar_auditoria(
                request=request,
                modulo='financeiro',
                acao='criar',
                objeto=parcela,
                descricao=f'Parcela financeira criada para NF {entrada.numero_nf}/{entrada.serie_nf}',
                relacionado=entrada,
                depois=snapshot_modelo(parcela),
                metadados={'entrada_id': entrada.pk, 'valor': str(parcela.valor)},
            )
            messages.success(request, 'Parcela adicionada para revisao financeira.')
            return redirect('compras:entrada-financeiro', pk=entrada.pk)

        contexto = self.get_context(entrada, request.user)
        contexto['form'] = form
        messages.error(request, 'Verifique os dados da parcela.')
        return render(request, self.template_name, contexto)


class EntradaNFGerarContasPagarView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'criar'

    def post(self, request, pk):
        entrada = get_object_or_404(
            EntradaNF.objects.for_filial(request.filial_ativa),
            pk=pk,
        )
        try:
            resultado = gerar_contas_pagar_da_entrada(entrada, request.user)
            registrar_auditoria(
                request=request,
                modulo='financeiro',
                acao='criar',
                objeto=entrada,
                descricao=f'Contas a pagar geradas para NF {entrada.numero_nf}/{entrada.serie_nf}',
                metadados={
                    'criadas': resultado.criadas,
                    'existentes': resultado.existentes,
                    'ignoradas': resultado.ignoradas,
                },
            )
            if resultado.criadas:
                messages.success(request, f'{resultado.criadas} conta(s) a pagar gerada(s).')
            if resultado.existentes:
                messages.info(request, f'{resultado.existentes} conta(s) ja existiam e foram vinculada(s).')
            if resultado.ignoradas:
                messages.warning(request, f'{resultado.ignoradas} parcela(s) ja estavam geradas.')
        except DomainError as exc:
            messages.error(request, str(exc))
        return redirect('compras:entrada-financeiro', pk=entrada.pk)


class EntradaNFCustosView(EntradaNFDetailView):
    permissao_acao = 'editar'
    template_name = 'compras/entrada/custos.html'

    def _parametros(self, entrada, data):
        custo_financeiro = _decimal_localizado(
            data.get('custo_financeiro'),
            entrada.custo_financeiro or Decimal('0'),
        )
        return {
            'metodo_rateio': data.get('metodo_rateio') or entrada.custo_rateio_metodo,
            'incluir_ipi': _bool_parametros(data, 'incluir_ipi', entrada.custo_incluir_ipi),
            'incluir_icms_st': _bool_parametros(data, 'incluir_icms_st', entrada.custo_incluir_icms_st),
            'incluir_icms': _bool_parametros(data, 'incluir_icms', entrada.custo_incluir_icms),
            'custo_financeiro': custo_financeiro,
        }

    def get(self, request, pk):
        entrada = self.get_entrada(request, pk)
        try:
            params = self._parametros(entrada, request.GET)
            composicao = EntradaCustoService.compor(entrada, **params)
        except (DomainError, InvalidOperation, ValueError) as exc:
            messages.error(request, f'Nao foi possivel calcular o custo: {exc}')
            params = {
                'metodo_rateio': entrada.custo_rateio_metodo,
                'incluir_ipi': entrada.custo_incluir_ipi,
                'incluir_icms_st': entrada.custo_incluir_icms_st,
                'incluir_icms': entrada.custo_incluir_icms,
                'custo_financeiro': entrada.custo_financeiro or Decimal('0'),
            }
            composicao = {
                'linhas': [],
                'resumo': {
                    'valor_mercadoria': Decimal('0'),
                    'frete': Decimal('0'),
                    'seguro': Decimal('0'),
                    'outras_despesas': Decimal('0'),
                    'desconto': Decimal('0'),
                    'ipi': Decimal('0'),
                    'icms_st': Decimal('0'),
                    'icms_nao_recuperavel': Decimal('0'),
                    'custo_financeiro': Decimal('0'),
                    'custo_total': Decimal('0'),
                    'alertas_custo': 0,
                    'alertas_custo_criticos': 0,
                },
                'alertas_custo': [],
                'metodo_efetivo': params['metodo_rateio'],
                'aviso_rateio': '',
                **params,
            }

        return render(request, self.template_name, {
            'entrada': entrada,
            'composicao': composicao,
            'resumo_executivo_custo': _resumo_executivo_custo(entrada, composicao),
            'alertas_custo_especificos': _alertas_custo_especificos(entrada, composicao),
            'metodos_rateio': EntradaNF.MetodoRateioCusto.choices,
            'pode_aplicar_custo': _entrada_aberta(entrada),
            'permissoes_compras': _permissoes_compras(request),
        })

    def post(self, request, pk):
        entrada = self.get_entrada(request, pk)
        if not _entrada_aberta(entrada):
            messages.error(request, 'Entrada fechada nao permite alterar composicao de custo.')
            return redirect('compras:entrada-custos', pk=entrada.pk)
        try:
            antes = snapshot_modelo(entrada)
            if request.POST.get('acao') == 'salvar_componentes':
                campos = [
                    'valor_frete',
                    'valor_seguro',
                    'valor_outras_despesas',
                    'valor_desconto',
                    'valor_ipi',
                    'valor_icms_st',
                    'valor_icms',
                ]
                for campo in campos:
                    valor = _decimal_localizado(request.POST.get(campo), getattr(entrada, campo) or Decimal('0'))
                    if valor < 0:
                        raise DomainError('Valores de custo nao podem ser negativos.')
                    setattr(entrada, campo, valor)
                entrada.valor_total = (
                    entrada.valor_produtos
                    + entrada.valor_frete
                    + entrada.valor_seguro
                    + entrada.valor_outras_despesas
                    + entrada.valor_ipi
                    + entrada.valor_icms_st
                    - entrada.valor_desconto
                )
                entrada.save(update_fields=[
                    'valor_frete',
                    'valor_seguro',
                    'valor_outras_despesas',
                    'valor_desconto',
                    'valor_ipi',
                    'valor_icms_st',
                    'valor_icms',
                    'valor_total',
                    'updated_at',
                ])
                EntradaCustoService.aplicar_configurada(entrada)
                entrada.refresh_from_db()
                _auditar_entrada(
                    request,
                    'editar',
                    entrada,
                    'Componentes de custo da entrada alterados',
                    justificativa=request.POST.get('justificativa') or 'Revisao de componentes de custo',
                    antes=antes,
                    depois=snapshot_modelo(entrada),
                    metadados={'campos': campos},
                )
                messages.success(request, 'Componentes de custo atualizados e custo composto recalculado.')
                return redirect('compras:entrada-custos', pk=entrada.pk)

            params = self._parametros(entrada, request.POST)
            composicao = EntradaCustoService.compor(
                entrada,
                **params,
                salvar=True,
                salvar_configuracao=True,
            )
            entrada.refresh_from_db()
            _auditar_entrada(
                request,
                'editar',
                entrada,
                'Composicao de custo aplicada aos itens',
                justificativa=request.POST.get('justificativa') or 'Revisao de composicao de custo',
                antes=antes,
                depois=snapshot_modelo(entrada),
                metadados={
                    'metodo_rateio': params['metodo_rateio'],
                    'metodo_efetivo': composicao.get('metodo_efetivo'),
                    'custo_total': str((composicao.get('resumo') or {}).get('custo_total') or '0'),
                },
            )
            messages.success(request, 'Composicao de custo aplicada aos itens da entrada.')
        except (DomainError, InvalidOperation, ValueError) as exc:
            messages.error(request, f'Nao foi possivel aplicar o custo: {exc}')
        return redirect('compras:entrada-custos', pk=entrada.pk)


class EntradaNFFinalizacaoView(EntradaNFDetailView):
    template_name = 'compras/entrada/finalizacao.html'

    def get(self, request, pk):
        entrada = self.get_entrada(request, pk)
        itens = list(
            entrada.itens
            .select_related('produto', 'produto__unidade_medida', 'lote_gerado')
            .order_by('numero_item', 'pk')
        )
        for item in itens:
            _avaliar_diferenca_item_para_tela(item)
            item.quantidade_movimenta = _quantidade_recebida_item(item)
            item.item_recusado = item.produto_id and item.quantidade_movimenta <= 0
        hoje = timezone.localdate()
        bloqueios = []
        avisos = []
        informacoes = []
        itens_problematicos = []
        alertas_custo = []
        alertas_custo_criticos = []
        resumo_final = {
            'total_itens': len(itens),
            'vinculados': 0,
            'sem_produto': 0,
            'movimentam': 0,
            'recusados': 0,
            'divergencias': 0,
            'lotes_pendentes': 0,
            'validades_pendentes': 0,
            'validades_vencidas': 0,
            'custo_critico': 0,
            'componentes_custo': Decimal('0'),
            'custo_total': Decimal('0'),
        }
        if not itens:
            bloqueios.append('Entrada sem itens.')
        sem_produto = [item for item in itens if not item.produto_id]
        resumo_final['sem_produto'] = len(sem_produto)
        resumo_final['vinculados'] = len(itens) - len(sem_produto)
        resumo_final['movimentam'] = sum(
            1 for item in itens
            if item.produto_id and not item.item_recusado and item.quantidade_movimenta > 0
        )
        resumo_final['recusados'] = sum(1 for item in itens if item.item_recusado)
        if sem_produto:
            bloqueios.append(f'{len(sem_produto)} item(ns) sem produto interno vinculado.')
        diferencas_bloqueantes = [item for item in itens if item.diferenca_bloqueante]
        resumo_final['divergencias'] = sum(1 for item in itens if item.diferenca_tipo)
        if diferencas_bloqueantes:
            bloqueios.append(f'{len(diferencas_bloqueantes)} diferenca(s) bloqueante(s) pendente(s).')
        lotes_pendentes = [
            item for item in itens
            if item.produto_id and _quantidade_recebida_item(item) > 0
            and item.produto.controla_lote and not item.numero_lote
        ]
        resumo_final['lotes_pendentes'] = len(lotes_pendentes)
        if lotes_pendentes:
            bloqueios.append(f'{len(lotes_pendentes)} item(ns) com lote obrigatorio pendente.')
        validades_pendentes = [
            item for item in itens
            if item.produto_id and _quantidade_recebida_item(item) > 0
            and item.produto.controla_validade and not item.data_validade
        ]
        resumo_final['validades_pendentes'] = len(validades_pendentes)
        if validades_pendentes:
            bloqueios.append(f'{len(validades_pendentes)} item(ns) com validade obrigatoria pendente.')
        validades_vencidas = [
            item for item in itens
            if item.produto_id and _quantidade_recebida_item(item) > 0
            and item.produto.controla_validade
            and item.data_validade and item.data_validade < hoje
        ]
        resumo_final['validades_vencidas'] = len(validades_vencidas)
        if validades_vencidas:
            bloqueios.append(f'{len(validades_vencidas)} item(ns) com validade vencida.')
        validades_proximas = [
            item for item in itens
            if item.produto_id and _quantidade_recebida_item(item) > 0
            and item.produto.controla_validade
            and item.data_validade and item.data_validade >= hoje
            and item.produto.dias_aviso_vencimento is not None
            and (item.data_validade - hoje).days <= item.produto.dias_aviso_vencimento
        ]
        if validades_proximas:
            avisos.append(f'{len(validades_proximas)} item(ns) com validade proxima do vencimento.')
        if entrada.fornecedor_pendente:
            avisos.append('Fornecedor ainda pendente. Pode continuar, mas fica marcado para revisao.')
        if entrada.destinatario_documento_diferente:
            avisos.append('Documento destinatario diferente da filial. E apenas alerta operacional.')
        try:
            composicao_custo = EntradaCustoService.compor(
                entrada=entrada,
                metodo_rateio=entrada.custo_rateio_metodo,
                incluir_ipi=entrada.custo_incluir_ipi,
                incluir_icms_st=entrada.custo_incluir_icms_st,
                incluir_icms=entrada.custo_incluir_icms,
                custo_financeiro=entrada.custo_financeiro or Decimal('0'),
            )
            custo_por_item = {
                linha.item.pk: linha.custo_unitario
                for linha in composicao_custo['linhas']
            }
            for item in itens:
                item.custo_unitario_preview = custo_por_item.get(item.pk, item.custo_unitario_total)
                if item.item_recusado:
                    item.custo_unitario_preview = Decimal('0')
            alertas_custo = composicao_custo.get('alertas_custo', [])
            if alertas_custo:
                alertas_custo_criticos = [
                    linha for linha in alertas_custo
                    if linha.alerta_custo_nivel == 'critico'
                ]
                resumo_final['custo_critico'] = len(alertas_custo_criticos)
                avisos.append(
                    f'{len(alertas_custo)} item(ns) com custo fora da referencia '
                    f'({len(alertas_custo_criticos)} critico(s)). Revise Custos antes de finalizar.'
                )
            resumo_final['componentes_custo'] = (
                (composicao_custo['resumo']['frete'] or Decimal('0'))
                + (composicao_custo['resumo']['seguro'] or Decimal('0'))
                + (composicao_custo['resumo']['outras_despesas'] or Decimal('0'))
                - (composicao_custo['resumo']['desconto'] or Decimal('0'))
                + (composicao_custo['resumo']['ipi'] or Decimal('0'))
                + (composicao_custo['resumo']['icms_st'] or Decimal('0'))
                + (composicao_custo['resumo']['icms_nao_recuperavel'] or Decimal('0'))
                + (composicao_custo['resumo']['custo_financeiro'] or Decimal('0'))
            )
            resumo_final['custo_total'] = composicao_custo['resumo']['custo_total']
            resumo_executivo_custo = _resumo_executivo_custo(entrada, composicao_custo)
            alertas_custo_especificos = _alertas_custo_especificos(entrada, composicao_custo)
            resumo_final.update({
                'custo_mercadorias': resumo_executivo_custo['custo_produtos'],
                'custo_acrescimos': resumo_executivo_custo['acrescimos'],
                'custo_descontos': resumo_executivo_custo['descontos'],
                'custo_final': resumo_executivo_custo['custo_final'],
                'custo_diferenca_nota': resumo_executivo_custo['diferenca_total_nota'],
                'impostos_no_custo': resumo_executivo_custo['impostos_nao_recuperaveis'],
                'impostos_fora_custo': resumo_executivo_custo['impostos_recuperaveis'],
            })
            for alerta in alertas_custo_especificos:
                avisos.append(alerta['texto'])
            if any([
                entrada.valor_frete,
                entrada.valor_seguro,
                entrada.valor_outras_despesas,
                entrada.valor_desconto,
                entrada.valor_ipi,
                entrada.valor_icms_st,
                entrada.custo_financeiro,
            ]):
                avisos.append('A entrada tem componentes fiscais/financeiros que alteram o custo. Revise a tela Custos antes de finalizar.')
        except DomainError as exc:
            composicao_custo = None
            resumo_executivo_custo = None
            alertas_custo_especificos = []
            bloqueios.append(f'Composicao de custo invalida: {exc}')
        total_parcelas = sum(
            (parcela.valor for parcela in entrada.parcelas_financeiras.all()),
            Decimal('0'),
        )
        if not total_parcelas:
            avisos.append('Nenhuma parcela financeira informada. Finaliza estoque, mas o contas a pagar fica para revisao manual.')
        elif total_parcelas != entrada.valor_total:
            avisos.append('Total das parcelas financeiras diferente do total da nota. Revise antes de gerar contas a pagar.')
        else:
            informacoes.append('Total financeiro bate com o total da nota.')

        for item in itens:
            problemas = []
            proximas_acoes = []
            prioridade = 90
            if not item.produto_id:
                problemas.append('Sem produto interno')
                proximas_acoes.append('Vincular produto')
                prioridade = min(prioridade, 10)
            if getattr(item, 'diferenca_tipo', ''):
                problemas.append(item.diferenca_descricao or 'Divergencia de conferencia')
                proximas_acoes.append('Resolver divergencia')
                prioridade = min(prioridade, 20 if item.diferenca_bloqueante else 50)
            if item in lotes_pendentes:
                problemas.append('Lote obrigatorio pendente')
                proximas_acoes.append('Preencher lote')
                prioridade = min(prioridade, 30)
            if item in validades_pendentes:
                problemas.append('Validade obrigatoria pendente')
                proximas_acoes.append('Preencher validade')
                prioridade = min(prioridade, 35)
            if item in validades_vencidas:
                problemas.append('Validade vencida')
                proximas_acoes.append('Corrigir validade')
                prioridade = min(prioridade, 25)
            custo_critico = any(linha.item.pk == item.pk for linha in alertas_custo_criticos)
            if custo_critico:
                problemas.append('Custo critico')
                proximas_acoes.append('Revisar custo')
                prioridade = min(prioridade, 45)
            item.finalizacao_problemas = problemas
            item.finalizacao_proxima_acao = ' / '.join(dict.fromkeys(proximas_acoes)) or 'Revisado'
            item.finalizacao_prioridade = prioridade
            if problemas:
                itens_problematicos.append(item)
        itens_problematicos.sort(key=lambda item: (item.finalizacao_prioridade, item.numero_item or 0, item.pk))

        if bloqueios:
            painel_finalizacao = {
                'nivel': 'red',
                'titulo': 'Bloqueado para efetivar',
                'descricao': 'Resolva as pendencias obrigatorias antes de criar movimentos, lotes e custo medio.',
                'acao': 'Resolver pendencias',
            }
        elif alertas_custo_criticos or avisos:
            painel_finalizacao = {
                'nivel': 'amber',
                'titulo': 'Exige atencao e confirmacao',
                'descricao': 'A entrada pode seguir, mas ha alertas que precisam de aceite explicito.',
                'acao': 'Confirmar e efetivar',
            }
        else:
            painel_finalizacao = {
                'nivel': 'green',
                'titulo': 'Pronto para efetivar',
                'descricao': 'Todos os pontos obrigatorios estao revisados para movimentar estoque.',
                'acao': 'Efetivar entrada',
            }
        informacoes.append(f'{resumo_final["movimentam"]} item(ns) vao movimentar estoque nesta filial.')

        return render(request, self.template_name, {
            'entrada': entrada,
            'itens': itens,
            'itens_problematicos': itens_problematicos,
            'bloqueios': bloqueios,
            'avisos': avisos,
            'informacoes': informacoes,
            'painel_finalizacao': painel_finalizacao,
            'total_parcelas': total_parcelas,
            'composicao_custo': composicao_custo,
            'resumo_executivo_custo': resumo_executivo_custo,
            'alertas_custo_especificos': alertas_custo_especificos,
            'alertas_custo': alertas_custo,
            'alertas_custo_criticos': alertas_custo_criticos,
            'confirmacao_custo_critico_obrigatoria': bool(alertas_custo_criticos),
            'confirmacao_custo_composto_obrigatoria': _entrada_exige_confirmacao_custo_composto(entrada),
            'resumo_final': resumo_final,
            'pode_finalizar_visualmente': entrada.pode_efetivar and not bloqueios,
            'pode_efetivar_entrada': request.user.tem_permissao('compras', 'aprovar'),
            'permissoes_compras': _permissoes_compras(request),
        })


class AdicionarItemEntradaView(PermissaoRequiredMixin, View):
    permissao_modulo = 'compras'
    permissao_acao = 'editar'

    def post(self, request, pk):
        entrada = get_object_or_404(EntradaNF.objects.for_filial(request.filial_ativa), pk=pk)
        if not _entrada_aberta(entrada):
            messages.error(request, 'Entrada efetivada nao permite adicionar itens.')
            return redirect('compras:entrada-detail', pk=entrada.pk)
        form = AdicionarItemEntradaForm(request.POST, filial=request.filial_ativa)
        if form.is_valid():
            try:
                CompraService.adicionar_item_entrada(
                    entrada=entrada,
                    produto=form.cleaned_data['produto'],
                    quantidade=form.cleaned_data['quantidade'],
                    valor_unitario=form.cleaned_data['valor_unitario'],
                    valor_ipi=form.cleaned_data.get('valor_ipi') or 0,
                    valor_icms=form.cleaned_data.get('valor_icms') or 0,
                    numero_lote=form.cleaned_data.get('numero_lote', ''),
                    data_fabricacao=form.cleaned_data.get('data_fabricacao'),
                    data_validade=form.cleaned_data.get('data_validade'),
                    ean_xml=form.cleaned_data.get('ean_xml', ''),
                    codigo_produto_fornecedor=form.cleaned_data.get('codigo_produto_fornecedor', ''),
                    descricao_xml=form.cleaned_data.get('descricao_xml', ''),
                    unidade_xml=form.cleaned_data.get('unidade_xml', ''),
                    fator_conversao=form.cleaned_data.get('fator_conversao') or Decimal('1'),
                    quantidade_recebida=form.cleaned_data.get('quantidade_recebida'),
                )
                messages.success(request, 'Item adicionado.')
            except DomainError as e:
                messages.error(request, str(e))
        else:
            for erro in form.non_field_errors():
                messages.error(request, erro)
            messages.error(request, 'Verifique os dados do item.')
        return redirect('compras:entrada-detail', pk=pk)


class RemoverItemEntradaView(PermissaoRequiredMixin, View):
    permissao_modulo = 'compras'
    permissao_acao = 'editar'

    def post(self, request, pk, item_id):
        entrada = get_object_or_404(EntradaNF.objects.for_filial(request.filial_ativa), pk=pk)
        item = get_object_or_404(entrada.itens.select_related('produto'), pk=item_id)
        try:
            antes = snapshot_modelo(entrada)
            item_snapshot = CompraService.remover_item_entrada(item)
            entrada.refresh_from_db()
            _auditar_entrada(
                request,
                'remover_item',
                entrada,
                (
                    f"Item {item_snapshot.get('numero_item')} removido da NF "
                    f"{entrada.numero_nf}/{entrada.serie_nf}"
                ),
                justificativa=request.POST.get('motivo', 'Remocao manual de item da entrada.'),
                antes=antes,
                depois=snapshot_modelo(entrada),
                metadados={'item_removido': item_snapshot},
            )
            messages.success(request, 'Item removido da entrada e registrado na auditoria.')
        except DomainError as e:
            messages.error(request, str(e))
        return redirect('compras:entrada-detail', pk=entrada.pk)


class EfetivarEntradaView(PermissaoRequiredMixin, View):
    permissao_modulo = 'compras'
    permissao_acao = 'aprovar'

    def post(self, request, pk):
        entrada = get_object_or_404(EntradaNF.objects.for_filial(request.filial_ativa), pk=pk)
        if request.POST.get('confirmar_resumo_final') != '1':
            messages.error(
                request,
                'Confirme a revisao final da entrada antes de efetivar.',
            )
            return redirect('compras:entrada-finalizacao', pk=pk)
        if _entrada_exige_confirmacao_custo_composto(entrada) and request.POST.get('confirmar_custo_composto') != '1':
            messages.error(
                request,
                'Confirme a revisao dos componentes de custo antes de efetivar a entrada.',
            )
            return redirect('compras:entrada-finalizacao', pk=pk)
        try:
            antes = snapshot_modelo(entrada)
            CompraService.efetivar_entrada(
                entrada,
                request.user,
                confirmar_custo_critico=request.POST.get('confirmar_custo_critico') == '1',
            )
            entrada.refresh_from_db()
            resultado = _resultado_efetivacao_entrada(request, entrada)
            _auditar_entrada(
                request,
                'efetivar',
                entrada,
                f'Entrada NF {entrada.numero_nf}/{entrada.serie_nf} efetivada',
                antes=antes,
                depois=snapshot_modelo(entrada),
                metadados={
                    'produtos_movimentados': resultado['produtos_movimentados'] if resultado else 0,
                    'quantidade_total': str(resultado['quantidade_total']) if resultado else '0',
                    'custo_total': str(resultado['custo_total']) if resultado else '0',
                },
            )
            messages.success(
                request,
                (
                    f"Entrada efetivada: {resultado['produtos_movimentados']} produto(s), "
                    f"{resultado['quantidade_total']} unidade(s), "
                    f"R$ {resultado['custo_total_formatado']} custo total"
                ),
            )
        except DomainError as e:
            messages.error(request, str(e))
        return redirect('compras:entrada-detail', pk=pk)


class EstornarEntradaView(PermissaoRequiredMixin, View):
    permissao_modulo = 'compras'
    permissao_acao = 'cancelar'
    template_name = 'compras/entrada/estorno.html'

    def get(self, request, pk):
        entrada = get_object_or_404(
            EntradaNF.objects.for_filial(request.filial_ativa).select_related('fornecedor'),
            pk=pk,
        )
        impacto = calcular_impacto_estorno_entrada(entrada)
        return render(request, self.template_name, {
            'entrada': entrada,
            'impacto': impacto,
        })

    def post(self, request, pk):
        entrada = get_object_or_404(
            EntradaNF.objects.for_filial(request.filial_ativa).select_related('fornecedor'),
            pk=pk,
        )
        motivo = request.POST.get('motivo', '').strip()
        if not motivo:
            messages.error(request, 'Informe a justificativa para estornar a entrada.')
            return redirect('compras:entrada-estorno', pk=entrada.pk)
        try:
            antes = snapshot_modelo(entrada)
            entrada, movimentos = estornar_entrada(entrada, request.user, motivo)
            _auditar_entrada(
                request,
                'cancelar',
                entrada,
                f'Entrada NF {entrada.numero_nf}/{entrada.serie_nf} estornada',
                justificativa=motivo,
                antes=antes,
                depois=snapshot_modelo(entrada),
                metadados={
                    'movimentos_estorno': [mov.pk for mov in movimentos],
                    'quantidade_movimentos': len(movimentos),
                },
            )
            messages.success(request, f'Entrada estornada com {len(movimentos)} movimento(s) reverso(s).')
            return redirect('compras:entrada-detail', pk=entrada.pk)
        except DomainError as exc:
            messages.error(request, str(exc))
            return redirect('compras:entrada-estorno', pk=entrada.pk)


def _movimentacoes_entrada_url(entrada: EntradaNF) -> str:
    return (
        f"{reverse('estoque:movimentacao-list')}"
        f"?documento_tipo={MovimentacaoEstoque.DocumentoTipo.NFE}"
        f"&documento_id={entrada.pk}"
    )


def _resultado_efetivacao_entrada(request, entrada: EntradaNF, itens=None) -> dict | None:
    if entrada.status not in (EntradaNF.Status.EFETIVADA, EntradaNF.Status.ESTORNADA):
        return None

    itens = list(itens) if itens is not None else list(
        entrada.itens.select_related('produto', 'produto__unidade_medida', 'lote_gerado')
    )
    movimentos = list(
        MovimentacaoEstoque.objects.for_filial(entrada.filial)
        .filter(
            documento_tipo=MovimentacaoEstoque.DocumentoTipo.NFE,
            documento_id=entrada.pk,
        )
        .select_related('produto', 'lote', 'usuario')
        .order_by('produto__descricao', 'pk')
    )
    movimentos_estorno = list(
        MovimentacaoEstoque.objects.for_filial(entrada.filial)
        .filter(
            documento_tipo=MovimentacaoEstoque.DocumentoTipo.ESTORNO_ENTRADA,
            documento_id=entrada.pk,
        )
        .select_related('produto', 'lote', 'usuario')
        .order_by('produto__descricao', 'pk')
    )
    lotes_ids = [item.lote_gerado_id for item in itens if item.lote_gerado_id]
    lotes = list(
        LoteProduto.objects.for_filial(entrada.filial)
        .filter(pk__in=lotes_ids)
        .select_related('produto')
        .order_by('produto__descricao', 'numero_lote')
    )
    estoques = {
        estoque.produto_id: estoque
        for estoque in Estoque.objects.filter(
            filial=entrada.filial,
            produto_id__in=[item.produto_id for item in itens if item.produto_id],
        )
    }

    itens_movimentados = []
    itens_recusados = []
    custo_total = Decimal('0')
    quantidade_total = Decimal('0')
    for item in itens:
        item.quantidade_movimenta = _quantidade_recebida_item(item)
        item.item_recusado = (
            item.produto_id
            and item.quantidade_movimenta <= 0
            and bool(item.justificativa_diferenca)
        )
        item.custo_total_efetivado = (
            (item.custo_unitario_total or Decimal('0')) * item.quantidade_movimenta
            if item.quantidade_movimenta > 0
            else Decimal('0')
        )
        item.estoque_custo_medio = (
            estoques[item.produto_id].custo_medio
            if item.produto_id in estoques
            else None
        )
        if item.produto_id:
            item.extrato_produto_url = (
                f"{reverse('estoque:movimentacao-list')}?produto={item.produto_id}"
            )
            item.movimentacoes_nota_url = _movimentacoes_entrada_url(entrada)
        if item.item_recusado:
            itens_recusados.append(item)
        elif item.produto_id and item.quantidade_movimenta > 0:
            itens_movimentados.append(item)
            quantidade_total += item.quantidade_movimenta
            custo_total += item.custo_total_efetivado

    custo_total_formatado = f'{custo_total:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
    return {
        'movimentacoes_url': _movimentacoes_entrada_url(entrada),
        'movimentacoes': movimentos,
        'movimentacoes_estorno': movimentos_estorno,
        'movimentos_count': len(movimentos),
        'movimentos_estorno_count': len(movimentos_estorno),
        'lotes': lotes,
        'lotes_count': len(lotes),
        'itens_movimentados': itens_movimentados,
        'itens_recusados': itens_recusados,
        'recusados_count': len(itens_recusados),
        'produtos_movimentados': len({item.produto_id for item in itens_movimentados}),
        'quantidade_total': quantidade_total.normalize() if quantidade_total else Decimal('0'),
        'custo_total': custo_total,
        'custo_total_formatado': custo_total_formatado,
        'estornada': entrada.status == EntradaNF.Status.ESTORNADA,
    }


def _entrada_exige_confirmacao_custo_composto(entrada: EntradaNF) -> bool:
    componentes = [
        entrada.valor_frete,
        entrada.valor_seguro,
        entrada.valor_outras_despesas,
        entrada.valor_desconto,
        entrada.valor_ipi,
        entrada.valor_icms_st,
        entrada.valor_icms,
        entrada.custo_financeiro,
    ]
    return any(Decimal(str(valor or '0')) != 0 for valor in componentes)


def _resumo_executivo_custo(entrada: EntradaNF, composicao: dict) -> dict:
    resumo = composicao.get('resumo') or {}
    zero = Decimal('0')
    custo_produtos = Decimal(str(resumo.get('valor_mercadoria') or zero))
    despesas_custo = (
        Decimal(str(resumo.get('frete') or zero))
        + Decimal(str(resumo.get('seguro') or zero))
        + Decimal(str(resumo.get('outras_despesas') or zero))
        + Decimal(str(resumo.get('custo_financeiro') or zero))
    )
    impostos_nao_recuperaveis = (
        Decimal(str(resumo.get('ipi') or zero))
        + Decimal(str(resumo.get('icms_st') or zero))
        + Decimal(str(resumo.get('icms_nao_recuperavel') or zero))
    )
    impostos_recuperaveis = (
        (Decimal(str(entrada.valor_ipi or zero)) if not composicao.get('incluir_ipi') else zero)
        + (Decimal(str(entrada.valor_icms_st or zero)) if not composicao.get('incluir_icms_st') else zero)
        + (Decimal(str(entrada.valor_icms or zero)) if not composicao.get('incluir_icms') else zero)
    )
    descontos = Decimal(str(resumo.get('desconto') or zero))
    acrescimos = despesas_custo + impostos_nao_recuperaveis
    custo_final = Decimal(str(resumo.get('custo_total') or zero))
    total_nota = Decimal(str(entrada.valor_total or zero))
    return {
        'custo_produtos': custo_produtos,
        'despesas_custo': despesas_custo,
        'impostos_nao_recuperaveis': impostos_nao_recuperaveis,
        'impostos_recuperaveis': impostos_recuperaveis,
        'descontos': descontos,
        'acrescimos': acrescimos,
        'custo_final': custo_final,
        'total_nota': total_nota,
        'diferenca_total_nota': custo_final - total_nota,
    }


def _alertas_custo_especificos(entrada: EntradaNF, composicao: dict) -> list[dict]:
    alertas = []
    zero = Decimal('0')
    if Decimal(str(entrada.valor_icms or zero)) > 0 and composicao.get('incluir_icms'):
        alertas.append({
            'nivel': 'amber',
            'titulo': 'ICMS como custo',
            'texto': 'ICMS marcado como custo, confirme se e nao recuperavel.',
        })
    if Decimal(str(entrada.valor_icms_st or zero)) > 0 and not composicao.get('incluir_icms_st'):
        alertas.append({
            'nivel': 'red',
            'titulo': 'ST fora do custo',
            'texto': 'ST sem inclusao no custo.',
        })
    if Decimal(str(entrada.valor_frete or zero)) > 0 and not entrada.custo_composto_em:
        alertas.append({
            'nivel': 'amber',
            'titulo': 'Frete pendente',
            'texto': 'Frete informado mas nao revisado.',
        })
    if (
        composicao.get('metodo_rateio') == EntradaNF.MetodoRateioCusto.PESO
        and composicao.get('metodo_efetivo') != EntradaNF.MetodoRateioCusto.PESO
    ):
        alertas.append({
            'nivel': 'amber',
            'titulo': 'Rateio por peso indisponivel',
            'texto': 'Produto sem peso usando fallback de rateio.',
        })
    return alertas


class CancelarEntradaView(PermissaoRequiredMixin, View):
    permissao_modulo = 'compras'
    permissao_acao = 'cancelar'

    def post(self, request, pk):
        entrada = get_object_or_404(EntradaNF.objects.for_filial(request.filial_ativa), pk=pk)
        motivo = request.POST.get('motivo', '').strip()
        if not motivo:
            messages.error(request, 'Informe a justificativa para cancelar a entrada.')
            return redirect('compras:entrada-detail', pk=pk)
        try:
            antes = snapshot_modelo(entrada)
            CompraService.cancelar_entrada(entrada, request.user, motivo)
            entrada.refresh_from_db()
            _auditar_entrada(
                request,
                'cancelar',
                entrada,
                f'Entrada NF {entrada.numero_nf}/{entrada.serie_nf} cancelada',
                justificativa=motivo,
                antes=antes,
                depois=snapshot_modelo(entrada),
            )
            messages.success(request, 'Entrada cancelada.')
        except DomainError as e:
            messages.error(request, str(e))
        return redirect('compras:entrada-detail', pk=pk)
