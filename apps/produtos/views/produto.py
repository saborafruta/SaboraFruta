"""CRUD de Produto."""
import csv
import io
import json
import logging
import re
import uuid
from decimal import Decimal
from urllib.request import urlopen

from django.contrib import messages
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Case, DecimalField, F, FilteredRelation, IntegerField, Max, OuterRef, Q, Subquery, Sum, Value, When
from django.db.models.functions import Cast, Coalesce
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.gzip import gzip_page
from PIL import Image, ImageOps
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

from apps.core.services.permissions import PermissaoRequiredMixin
from apps.core.services.search import filter_queryset_by_terms
from apps.cadastros.models import Fornecedor
from apps.core.models import Filial, LogSistema
from apps.estoque.models import Estoque, LoteProduto, MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.produtos.forms import ProdutoForm
from apps.produtos.forms.produto import LIMITE_IMAGEM_PRODUTO_BYTES, LIMITE_IMAGEM_PRODUTO_MB
from apps.produtos.models import (
    CategoriaProduto, ClasseFiscal, MarcaProduto, Produto, ProdutoFilial,
    ProdutoFornecedorEquivalencia, UnidadeMedida,
)
from apps.produtos.services.replicacao_service import ReplicacaoProdutoService


def _usuario_pode_exportar(request):
    return request.user.tem_permissao('produtos', 'exportar')


def _sincronizar_produto_sem_quebrar(request, produto):
    try:
        ReplicacaoProdutoService._vincular_produto(produto, produto.filial)
        ReplicacaoProdutoService.sincronizar_produto(produto)
        produto.refresh_from_db(fields=['id_externo', 'updated_at'])
    except Exception:
        messages.warning(
            request,
            'Produto salvo, mas nao foi possivel replicar para outras filiais agora.',
        )


def _produtos_filial_qs(request, incluir_inativos=False):
    qs = Produto.objects.annotate(
        vinculo_filial=FilteredRelation(
            'filiais_vinculo',
            condition=Q(filiais_vinculo__filial=request.filial_ativa),
        ),
    ).filter(
        vinculo_filial__id__isnull=False,
    ).annotate(
        ativo_filial=F('vinculo_filial__ativo'),
        produto_filial_id=F('vinculo_filial__id'),
    )
    if not incluir_inativos:
        qs = qs.filter(vinculo_filial__ativo=True)
    return qs.distinct()


def _produto_vinculo_filial(produto, filial, ativo_padrao=True):
    vinculo, _ = ProdutoFilial.objects.get_or_create(
        produto=produto,
        filial=filial,
        defaults={'ativo': ativo_padrao},
    )
    return vinculo


def _definir_status_produto_filial(produto, filial, ativo):
    vinculo, _ = ProdutoFilial.objects.update_or_create(
        produto=produto,
        filial=filial,
        defaults={'ativo': ativo},
    )
    return vinculo


def _filiais_replicacao_produtos(filial):
    politica_origem = ReplicacaoProdutoService._politica(filial)
    if (
        not filial
        or not getattr(filial, 'participa_replicacao', False)
        or not getattr(politica_origem, 'replicar_produtos_basicos', False)
    ):
        return filial.empresa.filiais.none() if filial else Filial.objects.none()
    return ReplicacaoProdutoService._filiais_destino(filial, 'replicar_produtos_basicos')


def _decimal_from_request(value):
    if value is None:
        return Decimal('0')
    text = str(value).strip()
    if not text:
        return Decimal('0')
    if ',' in text and '.' in text:
        text = text.replace('.', '').replace(',', '.')
    elif ',' in text:
        text = text.replace(',', '.')
    return Decimal(text)


def _format_quantidade_produto(valor, produto):
    if valor is None:
        valor = Decimal('0')
    try:
        valor = Decimal(str(valor))
    except Exception:
        return str(valor)
    usa_decimal = bool(
        getattr(produto, 'vendido_por_peso_granel', False)
        or getattr(produto, 'fracionavel', False)
        or getattr(produto, 'eh_granel', False)
    )
    casas = 3 if usa_decimal else (2 if valor != valor.to_integral_value() else 0)
    return f'{valor:,.{casas}f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def _digits_only(value):
    return ''.join(ch for ch in str(value or '') if ch.isdigit())


def _zerar_estoque_produto(produto, filial, usuario):
    estoque = Estoque.objects.filter(produto=produto, filial=filial).first()
    quantidade_atual = estoque.quantidade_atual if estoque else Decimal('0')
    if quantidade_atual == 0:
        return Decimal('0'), []
    observacao = 'Estoque zerado na inativacao do produto.'
    if produto.controla_lote:
        total_zerado = Decimal('0')
        ignorados = []
        lotes = LoteProduto.objects.filter(
            produto=produto,
            filial=filial,
            quantidade_atual__gt=0,
        ).order_by('data_validade', 'created_at')
        for lote in lotes:
            quantidade_lote = Decimal(lote.quantidade_atual or '0')
            if quantidade_lote <= 0:
                continue
            if lote.esta_vencido or lote.status == LoteProduto.Status.VENCIDO:
                tipo_operacao = MovimentacaoEstoque.TipoOperacao.BAIXA_VALIDADE
            elif lote.status == LoteProduto.Status.ATIVO:
                tipo_operacao = MovimentacaoEstoque.TipoOperacao.AJUSTE_MENOS
            else:
                ignorados.append(lote.numero_lote)
                continue
            MovimentacaoService.registrar_movimentacao(
                produto_id=produto.pk,
                filial_id=filial.pk,
                tipo_operacao=tipo_operacao,
                quantidade=quantidade_lote,
                usuario_id=usuario.pk,
                lote_id=lote.pk,
                documento_tipo=MovimentacaoEstoque.DocumentoTipo.AJUSTE_MANUAL,
                observacao=observacao,
            )
            total_zerado += quantidade_lote
        return total_zerado, ignorados
    MovimentacaoService.ajustar_manual(
        produto_id=produto.pk,
        filial_id=filial.pk,
        quantidade_nova=Decimal('0'),
        usuario_id=usuario.pk,
        justificativa=observacao,
    )
    return quantidade_atual, []


def _salvar_imagem_produto(form, produto):
    if form.cleaned_data.get('remover_imagem') and not form.cleaned_data.get('imagem_produto'):
        produto.foto_url = ''
        return True
    imagem = form.cleaned_data.get('imagem_produto')
    if not imagem:
        return False
    _gravar_imagem_produto(produto, imagem)
    return True


def _gravar_imagem_produto(produto, imagem):
    nome = getattr(imagem, 'name', '') or 'produto'
    extensao = nome.rsplit('.', 1)[-1].lower() if '.' in nome else 'jpg'
    if extensao not in {'jpg', 'jpeg', 'png', 'webp', 'gif'}:
        extensao = 'jpg'
    caminho = f'produtos/imagens/{uuid.uuid4().hex}.{extensao}'
    caminho_salvo = default_storage.save(caminho, imagem)
    produto.foto_url = caminho_salvo


def _proximo_codigo_produto():
    maior_codigo = 0
    for id_externo in Produto.objects.exclude(id_externo='').values_list('id_externo', flat=True):
        sufixo = id_externo.rsplit(':', 1)[-1]
        if sufixo.isdigit():
            maior_codigo = max(maior_codigo, int(sufixo))
    maior_pk_sem_replicacao = (
        Produto.objects.filter(id_externo='').aggregate(maior=Max('id')).get('maior') or 0
    )
    return max(maior_codigo, maior_pk_sem_replicacao) + 1


def _produto_queryset_filtrado(request, incluir_inativos_por_padrao=False):
    busca = request.GET.get('q', '').strip()
    categoria_id = request.GET.get('categoria', '')
    subcategoria_id = request.GET.get('subcategoria', '')
    marca_id = request.GET.get('marca', '')
    fornecedor_id = request.GET.get('fornecedor', '')
    status = request.GET.get('status') or ('todos' if incluir_inativos_por_padrao else 'ativo')
    somente_com_estoque = request.GET.get('com_estoque') == '1'
    ordem = request.GET.get('ordem', 'id')
    estoque_atual = Estoque.objects.filter(
        produto=OuterRef('pk'),
        filial=request.filial_ativa,
    ).values('quantidade_atual')[:1]
    qs = _produtos_filial_qs(
        request,
        incluir_inativos=incluir_inativos_por_padrao or status in {'todos', 'inativo'},
    ).select_related(
        'categoria', 'subcategoria', 'linha_producao', 'unidade_medida',
        'unidade_medida_compra', 'classe_fiscal', 'marca', 'fornecedor',
    ).annotate(
        estoque_atual_lista=Coalesce(
            Subquery(
                estoque_atual,
                output_field=DecimalField(max_digits=12, decimal_places=3),
            ),
            Value(Decimal('0')),
            output_field=DecimalField(max_digits=12, decimal_places=3),
        ),
        codigo_ordenacao=Coalesce(
            Case(
                When(codigo__regex=r'^\d+$', then=Cast('codigo', IntegerField())),
                output_field=IntegerField(),
            ),
            F('id'),
            output_field=IntegerField(),
        ),
    )
    if busca:
        busca_codigo = busca.lstrip('0')
        if busca_codigo.isdigit():
            codigo_int = int(busca_codigo)
            qs = qs.filter(
                Q(pk=codigo_int) | Q(id_externo=f'produto:{codigo_int}')
                | Q(codigo__icontains=busca) | Q(codigo_barras__icontains=busca)
                | Q(descricao__icontains=busca) | Q(ncm__icontains=busca)
            )
        else:
            qs = filter_queryset_by_terms(
                qs,
                busca,
                fields=(
                    'codigo', 'codigo_barras', 'descricao', 'ncm', 'marca__nome',
                    'fornecedor__nome_fantasia', 'fornecedor__razao_social',
                ),
            )
    if categoria_id:
        categoria = CategoriaProduto.objects.for_filial(request.filial_ativa).filter(
            pk=categoria_id,
            empresa=request.user.empresa,
        ).first()
        subcategoria = CategoriaProduto.objects.for_filial(request.filial_ativa).filter(
            pk=subcategoria_id,
            categoria_pai_id=categoria_id,
            empresa=request.user.empresa,
        ).first() if subcategoria_id else None
        if subcategoria:
            filtro_categoria = (
                Q(subcategoria_id=subcategoria_id)
                | Q(categoria_id=subcategoria_id)
                | Q(categoria_id=subcategoria.categoria_pai_id, subcategoria__isnull=True)
            )
            if subcategoria.id_externo:
                filtro_categoria |= Q(subcategoria__id_externo=subcategoria.id_externo)
            if subcategoria.categoria_pai and subcategoria.categoria_pai.id_externo:
                filtro_categoria |= Q(
                    categoria__id_externo=subcategoria.categoria_pai.id_externo,
                    subcategoria__isnull=True,
                )
            qs = qs.filter(filtro_categoria)
        else:
            filtro_categoria = (
                Q(categoria_id=categoria_id)
                | Q(categoria__categoria_pai_id=categoria_id)
            )
            if categoria and categoria.id_externo:
                filtro_categoria |= (
                    Q(categoria__id_externo=categoria.id_externo)
                    | Q(categoria__categoria_pai__id_externo=categoria.id_externo)
                )
            qs = qs.filter(filtro_categoria)
    elif subcategoria_id and (
        subcategoria := CategoriaProduto.objects.for_filial(request.filial_ativa).filter(
        pk=subcategoria_id,
        empresa=request.user.empresa,
        categoria_pai__isnull=False,
        ).first()
    ):
        filtro_categoria = (
            Q(subcategoria_id=subcategoria_id)
            | Q(categoria_id=subcategoria_id)
            | Q(categoria_id=subcategoria.categoria_pai_id, subcategoria__isnull=True)
        )
        if subcategoria.id_externo:
            filtro_categoria |= Q(subcategoria__id_externo=subcategoria.id_externo)
        if subcategoria.categoria_pai and subcategoria.categoria_pai.id_externo:
            filtro_categoria |= Q(
                categoria__id_externo=subcategoria.categoria_pai.id_externo,
                subcategoria__isnull=True,
            )
        qs = qs.filter(filtro_categoria)
    if status == 'ativo':
        qs = qs.filter(vinculo_filial__ativo=True)
    elif status == 'inativo':
        qs = qs.filter(vinculo_filial__ativo=False)
    if somente_com_estoque:
        qs = qs.filter(estoque_atual_lista__gt=0)
    if marca_id:
        marca = MarcaProduto.objects.for_filial(request.filial_ativa).filter(
            pk=marca_id,
            empresa=request.user.empresa,
        ).first()
        if marca:
            filtro_marca = Q(marca_id=marca_id)
            if marca.id_externo:
                filtro_marca |= Q(marca__id_externo=marca.id_externo)
            qs = qs.filter(filtro_marca)
    if fornecedor_id:
        fornecedor = Fornecedor.objects.for_filial(request.filial_ativa).filter(
            pk=fornecedor_id,
        ).first()
        if fornecedor:
            filtro_fornecedor = Q(fornecedor_id=fornecedor_id)
            if fornecedor.id_externo:
                filtro_fornecedor |= Q(fornecedor__id_externo=fornecedor.id_externo)
            if getattr(fornecedor, 'grupo_replicacao', None):
                filtro_fornecedor |= Q(fornecedor__grupo_replicacao=fornecedor.grupo_replicacao)
            qs = qs.filter(filtro_fornecedor)

    ordenacoes = {
        'id': ('codigo_ordenacao', 'id'),
        'id_desc': ('-codigo_ordenacao', '-id'),
        'referencia': ('codigo_ordenacao', 'codigo', 'id'),
        'referencia_desc': ('-codigo_ordenacao', '-codigo', '-id'),
        'az': ('descricao', 'id'),
        'za': ('-descricao', 'id'),
        'custo': ('preco_custo', 'id'),
        'custo_desc': ('-preco_custo', 'id'),
        'preco': ('preco_venda', 'id'),
        'preco_desc': ('-preco_venda', 'id'),
        'criado_desc': ('-created_at', 'id'),
        'criado_asc': ('created_at', 'id'),
    }
    return qs.order_by(*ordenacoes.get(ordem, ordenacoes['id']))


def _produto_fiscal_pendencias(produto):
    pendencias = []
    if not produto.ncm:
        pendencias.append('NCM')
    if not produto.cfop_venda_interna:
        pendencias.append('CFOP venda UF')
    if not produto.cfop_venda_interestadual:
        pendencias.append('CFOP venda fora UF')
    if not produto.cst_csosn:
        pendencias.append('CST/CSOSN')
    if not produto.cst_pis:
        pendencias.append('CST PIS')
    if not produto.cst_cofins:
        pendencias.append('CST COFINS')
    if produto.aliquota_ipi and produto.aliquota_ipi > 0 and not produto.cst_ipi:
        pendencias.append('CST IPI')
    if produto.cst_ipi == '99' and not produto.codigo_enquadramento_ipi:
        pendencias.append('Enquadramento IPI')
    return pendencias


def _filial_regime_fiscal_badge(filial):
    empresa = getattr(filial, 'empresa', None)
    regime = getattr(filial, 'regime_tributario', '') or getattr(empresa, 'regime_tributario', '')
    codigo = getattr(filial, 'codigo_regime_tributario', None)
    if codigo in (None, ''):
        codigo = getattr(empresa, 'codigo_regime_tributario', None)
    try:
        codigo = int(codigo)
    except (TypeError, ValueError):
        codigo = None
    if regime in {'simples_nacional', 'mei'} or codigo in {1, 2}:
        return {'label': 'Simples Nacional', 'kind': 'simples'}
    return {'label': 'Regime Normal', 'kind': 'normal'}


def _produto_fiscal_queryset(request):
    qs = _produto_queryset_filtrado(request, incluir_inativos_por_padrao=True)
    ncm = request.GET.get('ncm', '').strip()
    cfop = request.GET.get('cfop', '').strip()
    ipi = request.GET.get('ipi', '').strip()
    pis_cofins = request.GET.get('pis_cofins', '').strip()

    if ncm:
        ncm_digits = _digits_only(ncm)
        filtro = Q(ncm__icontains=ncm)
        if ncm_digits and ncm_digits != ncm:
            filtro |= Q(ncm__icontains=ncm_digits)
        qs = qs.filter(filtro)
    if cfop:
        cfop_digits = _digits_only(cfop) or cfop
        qs = qs.filter(
            Q(cfop_venda_interna__icontains=cfop_digits)
            | Q(cfop_venda_interestadual__icontains=cfop_digits)
            | Q(cfop_venda_exportacao__icontains=cfop_digits)
            | Q(cfop_compra__icontains=cfop_digits)
            | Q(cfop_devolucao__icontains=cfop_digits)
            | Q(cfop_devolucao_compra__icontains=cfop_digits)
        )
    if ipi:
        filtro = (
            Q(cst_ipi__icontains=ipi)
            | Q(codigo_enquadramento_ipi__icontains=ipi)
        )
        try:
            filtro |= Q(aliquota_ipi=_decimal_from_request(ipi))
        except Exception:
            pass
        qs = qs.filter(filtro)
    if pis_cofins:
        filtro = Q(cst_pis__icontains=pis_cofins) | Q(cst_cofins__icontains=pis_cofins)
        try:
            valor = _decimal_from_request(pis_cofins)
            filtro |= Q(classe_fiscal__aliquotas__pis=valor) | Q(classe_fiscal__aliquotas__cofins=valor)
        except Exception:
            pass
        qs = qs.filter(filtro).distinct()
    return qs


def _sim_nao(value):
    return 'Sim' if value else 'Nao'


def _produto_ativo_contextual(produto):
    return bool(getattr(produto, 'ativo_filial', produto.ativo))


def _produtos_com_estoque_total(qs, empresa):
    produtos = list(qs)
    if not empresa or not produtos:
        for produto in produtos:
            produto.estoque_total_export = Decimal('0')
        return produtos
    ids_externos = [produto.id_externo for produto in produtos if produto.id_externo]
    codigos = [produto.codigo_replicacao for produto in produtos if not produto.id_externo]
    if not ids_externos and not codigos:
        for produto in produtos:
            produto.estoque_total_export = Decimal('0')
        return produtos
    relacionados_qs = Produto.objects.filter(filial__empresa=empresa)
    filtro_relacionados = Q()
    if ids_externos:
        filtro_relacionados |= Q(id_externo__in=ids_externos)
    if codigos:
        # codigo_replicacao e uma property (nao um campo de banco): para quem
        # nao tem id_externo, ela e sempre igual ao proprio pk (ver o
        # @property no model), entao o filtro equivalente e por pk.
        filtro_relacionados |= Q(id_externo='', pk__in=codigos)
    relacionados = relacionados_qs.filter(filtro_relacionados).only('id', 'id_externo')
    produto_para_chave = {
        item.pk: item.id_externo or f'codigo:{item.codigo_replicacao}'
        for item in relacionados
    }
    totais_por_chave = {}
    for item in Estoque.objects.filter(
        produto_id__in=produto_para_chave.keys(),
        filial__empresa=empresa,
    ).values('produto_id').annotate(total=Sum('quantidade_atual')):
        chave = produto_para_chave.get(item['produto_id'])
        if not chave:
            continue
        totais_por_chave[chave] = totais_por_chave.get(chave, Decimal('0')) + (item['total'] or Decimal('0'))
    for produto in produtos:
        chave = produto.id_externo or f'codigo:{produto.codigo_replicacao}'
        produto.estoque_total_export = totais_por_chave.get(chave, Decimal('0'))
    return produtos


def _produto_csv_response(qs, filename, completo=False, empresa=None):
    response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response.write('\ufeff')
    writer = csv.writer(response, delimiter=';')
    produtos = _produtos_com_estoque_total(qs, empresa)
    if completo:
        writer.writerow([
            'ID', 'Nome', 'Descricao curta', 'Referencia', 'Codigo de barras',
            'Codigos de barras extras', 'Categoria', 'Sub categoria', 'Familia / Linha',
            'Marca / Fabricante', 'Fornecedor',
            'Tipo produto', 'Unidade de medida', 'Unidade de compra', 'Fator conversao compra',
            'Ativo', 'Permite venda sem estoque', 'Estoque atual', 'Estoque total', 'Estoque minimo',
            'Estoque maximo', 'Ponto reposicao', 'Estoque seguranca', 'Metodo saida',
            'Localizacao estoque', 'Lead time reposicao dias', 'Dias aviso vencimento',
            'Preco custo', 'Preco custo medio', 'Preco venda', 'Margem lucro',
            'Margem desejada', 'Markup', 'Preco sugerido', 'Preco minimo',
            'Preco promocional', 'Promocao inicio', 'Promocao fim', 'Moeda',
            'NCM', 'CEST', 'Origem produto',
            'CFOP venda interna', 'CFOP venda interestadual', 'CFOP venda exportacao',
            'CFOP compra', 'CFOP devolucao venda', 'CFOP devolucao compra',
            'CST / CSOSN', 'CST PIS', 'CST COFINS', 'CST IPI',
            'Enquadramento IPI', 'Aliquota IPI', 'Controla lote', 'Controla validade',
            'Condicao armazenamento', 'Temperatura minima', 'Temperatura maxima',
            'Umidade relativa', 'Vendido por peso / granel', 'Codigo balanca',
            'Tara padrao', 'Variacao peso permitida', 'Peso minimo venda',
            'Unidade pesagem', 'Fracionavel', 'Gera etiqueta balanca',
            'Peso bruto', 'Peso liquido', 'Largura', 'Altura', 'Comprimento',
            'Unidade peso', 'Unidade dimensao', 'Volume / Cubagem (m3)',
            'Tipo embalagem', 'Quantidade por embalagem', 'Empilhamento maximo',
            'Observacao interna', 'Descricao completa', 'Especificacoes tecnicas',
            'Criado em', 'Atualizado em',
        ])
        for p in produtos:
            writer.writerow([
                p.codigo_replicacao,
                p.descricao,
                p.descricao_curta,
                p.codigo,
                p.codigo_barras,
                ', '.join(p.codigos_barras_extras or []),
                p.categoria.nome if p.categoria else '',
                p.subcategoria.nome if p.subcategoria else '',
                p.linha_producao.nome if p.linha_producao else '',
                p.marca.nome if p.marca else '',
                str(p.fornecedor) if p.fornecedor else '',
                p.get_tipo_produto_display(),
                str(p.unidade_medida) if p.unidade_medida else '',
                str(p.unidade_medida_compra) if p.unidade_medida_compra else '',
                p.fator_conversao_compra,
                _sim_nao(_produto_ativo_contextual(p)),
                _sim_nao(p.permite_venda_sem_estoque),
                p.estoque_atual_lista,
                p.estoque_total_export,
                p.estoque_minimo,
                p.estoque_maximo,
                p.ponto_reposicao,
                p.estoque_seguranca,
                p.get_metodo_saida_display(),
                p.localizacao_estoque,
                p.lead_time_reposicao_dias,
                p.dias_aviso_vencimento,
                p.preco_custo,
                p.preco_custo_medio,
                p.preco_venda,
                p.margem_lucro,
                p.margem_desejada,
                p.markup,
                p.preco_sugerido,
                p.preco_minimo,
                p.preco_promocional,
                p.promocao_inicio,
                p.promocao_fim,
                p.moeda,
                p.ncm,
                p.cest,
                p.get_origem_produto_display(),
                p.cfop_venda_interna,
                p.cfop_venda_interestadual,
                p.cfop_venda_exportacao,
                p.cfop_compra,
                p.cfop_devolucao,
                p.cfop_devolucao_compra,
                p.cst_csosn,
                p.cst_pis,
                p.cst_cofins,
                p.cst_ipi,
                p.codigo_enquadramento_ipi,
                p.aliquota_ipi,
                _sim_nao(p.controla_lote),
                _sim_nao(p.controla_validade),
                p.get_condicao_armazenamento_display(),
                p.temperatura_minima,
                p.temperatura_maxima,
                p.umidade_relativa,
                _sim_nao(p.vendido_por_peso_granel),
                p.codigo_balanca,
                p.tara_padrao,
                p.variacao_peso_permitida,
                p.peso_minimo_venda,
                p.get_unidade_pesagem_display(),
                _sim_nao(p.fracionavel),
                _sim_nao(p.gera_etiqueta_balanca),
                p.peso_bruto,
                p.peso_liquido,
                p.largura,
                p.altura,
                p.profundidade,
                p.get_unidade_peso_display(),
                p.get_unidade_dimensao_display(),
                p.volume_cubagem,
                p.tipo_embalagem,
                p.quantidade_por_embalagem,
                p.empilhamento_maximo,
                p.observacao,
                p.descricao_completa,
                json.dumps(p.especificacoes_tecnicas or [], ensure_ascii=False),
                timezone.localtime(p.created_at).strftime('%d/%m/%Y %H:%M') if p.created_at else '',
                timezone.localtime(p.updated_at).strftime('%d/%m/%Y %H:%M') if p.updated_at else '',
            ])
        return response

    writer.writerow([
        'ID', 'Referencia', 'Descricao', 'Codigo de barras', 'Categoria', 'Marca', 'Fornecedor', 'Tipo',
        'Estoque', 'Estoque total', 'Custo', 'Preco venda', 'Margem', 'Ativo', 'Criado em',
    ])
    for p in produtos:
        writer.writerow([
            p.codigo_replicacao,
            p.codigo,
            p.descricao,
            p.codigo_barras,
            p.categoria.nome if p.categoria else '',
            p.marca.nome if p.marca else '',
            str(p.fornecedor) if p.fornecedor else '',
            p.get_tipo_produto_display(),
            p.estoque_atual_lista,
            p.estoque_total_export,
            p.preco_custo,
            p.preco_venda,
            p.margem_lucro,
            _sim_nao(_produto_ativo_contextual(p)),
            timezone.localtime(p.created_at).strftime('%d/%m/%Y %H:%M') if p.created_at else '',
        ])
    return response


def _produto_pdf_response(qs, empresa=None):
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="produtos_filtrados.pdf"'
    doc = SimpleDocTemplate(response, pagesize=landscape(A4), rightMargin=24, leftMargin=24, topMargin=24, bottomMargin=24)
    styles = getSampleStyleSheet()
    elementos = [
        Paragraph('Produtos filtrados', styles['Title']),
        Paragraph(f'Gerado em {timezone.localtime().strftime("%d/%m/%Y %H:%M")}', styles['Normal']),
        Spacer(1, 12),
    ]
    dados = [['ID', 'Ref.', 'Produto', 'Cod. barras', 'Estoque', 'Total', 'Categoria', 'Tipo', 'Custo', 'Venda', 'Ativo']]
    for p in _produtos_com_estoque_total(qs, empresa):
        dados.append([
            str(p.codigo_replicacao),
            p.codigo or '-',
            p.descricao,
            p.codigo_barras or '-',
            f'{p.estoque_atual_lista:.3f}',
            f'{p.estoque_total_export:.3f}',
            p.categoria.nome if p.categoria else '-',
            p.get_tipo_produto_display(),
            f'{p.preco_custo:.2f}',
            f'{p.preco_venda:.2f}',
            _sim_nao(_produto_ativo_contextual(p)),
        ])
    table = Table(dados, repeatRows=1, colWidths=[28, 48, 138, 76, 48, 48, 76, 60, 46, 46, 34])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2563eb')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#d1d5db')),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8fafc')]),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    elementos.append(table)
    doc.build(elementos)
    return response


def _subcategorias_form_json(empresa, filial):
    return json.dumps({
        str(categoria_id): [
            {'id': item.id, 'nome': item.nome}
            for item in CategoriaProduto.objects.for_filial(filial).filter(
                empresa=empresa,
                ativo=True,
                categoria_pai_id=categoria_id,
            ).order_by('nome')
        ]
        for categoria_id in CategoriaProduto.objects.for_filial(filial).filter(
            empresa=empresa,
            ativo=True,
            categoria_pai__isnull=True,
        ).values_list('id', flat=True)
    })


def _produto_duplicate_initial(produto):
    campos_ignorados = {
        'id',
        'filial',
        'created_at',
        'updated_at',
        'id_externo',
        'codigo',
        'codigo_barras',
        'codigos_barras_extras',
        'preco_custo_medio',
        'margem_lucro',
        'markup',
        'preco_sugerido',
        'saida_fefo',
    }
    initial = {}
    for field in Produto._meta.fields:
        if field.name in campos_ignorados:
            continue
        value = getattr(produto, field.name)
        if getattr(field, 'remote_field', None) and value is not None:
            value = value.pk
        initial[field.name] = value
    initial.update({
        'codigo': '',
        'codigo_barras': '',
        'codigos_barras_extras': [],
        'codigo_barras_extra_1': '',
        'codigo_barras_extra_2': '',
        'codigo_barras_extra_3': '',
        'estoque_quantidade': Decimal('0'),
    })
    return initial


PRODUTO_AUDIT_FIELDS = {
    'descricao': 'Nome',
    'codigo': 'Referencia / codigo interno',
    'codigo_barras': 'Codigo de barras',
    'codigos_barras_extras': 'Codigos de barras extras',
    'categoria': 'Categoria',
    'subcategoria': 'Sub categoria',
    'linha_producao': 'Familia / linha',
    'marca': 'Marca / Fabricante',
    'fornecedor': 'Fornecedor',
    'tipo_produto': 'Tipo produto',
    'unidade_medida': 'Unidade de medida',
    'unidade_medida_compra': 'Unidade de compra',
    'fator_conversao_compra': 'Fator de conversao da compra',
    'descricao_curta': 'Descricao curta',
    'foto_url': 'Imagem',
    'observacao': 'Observacao interna',
    'ncm': 'NCM',
    'cest': 'CEST',
    'origem_produto': 'Origem produto',
    'classe_fiscal': 'Classe fiscal',
    'cfop_venda_interna': 'CFOP venda dentro do estado',
    'cfop_venda_interestadual': 'CFOP venda para outro estado',
    'cfop_venda_exportacao': 'CFOP venda para exportacao',
    'cfop_compra': 'CFOP compra / entrada',
    'cfop_devolucao': 'CFOP devolucao de venda',
    'cfop_devolucao_compra': 'CFOP devolucao de compra',
    'cst_csosn': 'CST / CSOSN',
    'codigo_beneficio_fiscal_icms': 'Beneficio fiscal ICMS',
    'modalidade_bc_icms': 'Modalidade BC ICMS',
    'aliquota_icms': 'Aliquota ICMS',
    'reducao_bc_icms': 'Reducao BC ICMS',
    'aliquota_fcp': 'Aliquota FCP',
    'modalidade_bc_icms_st': 'Modalidade BC ICMS-ST',
    'mva_icms_st': 'MVA ICMS-ST',
    'reducao_bc_icms_st': 'Reducao BC ICMS-ST',
    'aliquota_icms_st': 'Aliquota ICMS-ST',
    'aliquota_fcp_st': 'Aliquota FCP-ST',
    'cst_pis': 'CST PIS',
    'aliquota_pis': 'Aliquota PIS',
    'cst_cofins': 'CST COFINS',
    'aliquota_cofins': 'Aliquota COFINS',
    'natureza_receita_pis_cofins': 'Natureza receita PIS/COFINS',
    'cst_ipi': 'CST IPI',
    'codigo_enquadramento_ipi': 'Enquadramento IPI',
    'aliquota_ipi': 'Aliquota IPI',
    'ex_tipi': 'EX TIPI',
    'cst_cbs': 'CST CBS',
    'classificacao_tributaria_cbs': 'Classificacao tributaria CBS',
    'aliquota_cbs': 'Aliquota CBS',
    'reducao_cbs': 'Reducao CBS',
    'cst_ibs': 'CST IBS',
    'classificacao_tributaria_ibs': 'Classificacao tributaria IBS',
    'aliquota_ibs_uf': 'Aliquota IBS UF',
    'aliquota_ibs_municipal': 'Aliquota IBS municipal',
    'reducao_ibs': 'Reducao IBS',
    'cst_is': 'CST IS',
    'classificacao_tributaria_is': 'Classificacao tributaria IS',
    'aliquota_is': 'Aliquota IS',
    'preco_custo': 'Preco de custo',
    'preco_venda': 'Preco de venda',
    'preco_minimo': 'Preco minimo',
    'preco_promocional': 'Preco promocional',
    'promocao_inicio': 'Inicio da promocao',
    'promocao_fim': 'Fim da promocao',
    'estoque_minimo': 'Estoque minimo',
    'estoque_maximo': 'Estoque maximo',
    'ponto_reposicao': 'Ponto de reposicao',
    'estoque_seguranca': 'Estoque de seguranca',
    'metodo_saida': 'Metodo de saida',
    'localizacao_estoque': 'Localizacao estoque',
    'dias_aviso_vencimento': 'Dias aviso vencimento',
    'permite_venda_sem_estoque': 'Permite venda sem estoque',
    'controla_lote': 'Controla lote',
    'controla_validade': 'Controla validade',
    'condicao_armazenamento': 'Condicao de armazenamento',
    'temperatura_minima': 'Temperatura minima',
    'temperatura_maxima': 'Temperatura maxima',
    'umidade_relativa': 'Umidade relativa',
    'vendido_por_peso_granel': 'Produto vendido por peso / granel',
    'codigo_balanca': 'Codigo da balanca',
    'tara_padrao': 'Tara padrao',
    'variacao_peso_permitida': 'Variacao peso permitida',
    'peso_minimo_venda': 'Peso minimo venda',
    'unidade_pesagem': 'Unidade pesagem',
    'fracionavel': 'Permite quantidades fracionadas',
    'gera_etiqueta_balanca': 'Gera etiqueta balanca',
    'peso_bruto': 'Peso bruto',
    'peso_liquido': 'Peso liquido',
    'largura': 'Largura',
    'altura': 'Altura',
    'profundidade': 'Comprimento',
    'unidade_peso': 'Unidade peso',
    'unidade_dimensao': 'Unidade dimensao',
    'tipo_embalagem': 'Tipo de embalagem',
    'quantidade_por_embalagem': 'Quantidade por embalagem',
    'empilhamento_maximo': 'Empilhamento maximo',
    'ativo': 'Ativo',
}


def _produto_valor_auditoria(produto, field_name):
    value = getattr(produto, field_name, None)
    display_method = getattr(produto, f'get_{field_name}_display', None)
    if callable(display_method):
        return str(display_method() or '')
    if field_name in {
        'categoria', 'subcategoria', 'linha_producao', 'marca', 'fornecedor',
        'unidade_medida', 'unidade_medida_compra', 'classe_fiscal',
    }:
        return str(value) if value else ''
    if isinstance(value, list):
        return ', '.join(str(item) for item in value)
    if value is True:
        return 'Sim'
    if value is False:
        return 'Nao'
    if value is None:
        return ''
    return str(value)


def _produto_audit_snapshot(produto):
    return {
        field_name: _produto_valor_auditoria(produto, field_name)
        for field_name in PRODUTO_AUDIT_FIELDS
    }


PRODUTO_AUDIT_DECIMAL_FIELDS = {
    'fator_conversao_compra',
    'aliquota_icms',
    'reducao_bc_icms',
    'aliquota_fcp',
    'mva_icms_st',
    'reducao_bc_icms_st',
    'aliquota_icms_st',
    'aliquota_fcp_st',
    'aliquota_pis',
    'aliquota_cofins',
    'aliquota_ipi',
    'aliquota_cbs',
    'reducao_cbs',
    'aliquota_ibs_uf',
    'aliquota_ibs_municipal',
    'reducao_ibs',
    'aliquota_is',
    'preco_custo',
    'preco_custo_medio',
    'preco_venda',
    'margem_lucro',
    'margem_desejada',
    'markup',
    'preco_sugerido',
    'preco_minimo',
    'preco_promocional',
    'estoque_minimo',
    'estoque_maximo',
    'ponto_reposicao',
    'estoque_seguranca',
    'lead_time_reposicao_dias',
    'dias_aviso_vencimento',
    'peso_bruto',
    'peso_liquido',
    'largura',
    'altura',
    'profundidade',
    'quantidade_por_embalagem',
    'empilhamento_maximo',
    'tara_padrao',
    'variacao_peso_permitida',
    'peso_minimo_venda',
    'temperatura_minima',
    'temperatura_maxima',
    'umidade_relativa',
}


def _decimal_auditoria(value):
    if value in (None, '', '-'):
        return None
    texto = str(value).strip().replace('R$', '').replace('%', '').replace(' ', '')
    if ',' in texto and '.' in texto:
        texto = texto.replace('.', '').replace(',', '.')
    elif ',' in texto:
        texto = texto.replace(',', '.')
    try:
        return Decimal(texto)
    except Exception:
        return None


def _format_decimal_auditoria(value):
    decimal_value = _decimal_auditoria(value)
    if decimal_value is None:
        return value or '-'
    texto = format(decimal_value, 'f')
    if '.' in texto:
        texto = texto.rstrip('0').rstrip('.')
    return texto or '0'


def _produto_audit_changes(antes, depois):
    changes = []
    for field_name, label in PRODUTO_AUDIT_FIELDS.items():
        valor_antes = antes.get(field_name)
        valor_depois = depois.get(field_name)
        if field_name in PRODUTO_AUDIT_DECIMAL_FIELDS:
            decimal_antes = _decimal_auditoria(valor_antes)
            decimal_depois = _decimal_auditoria(valor_depois)
            if decimal_antes is not None and decimal_depois is not None and decimal_antes == decimal_depois:
                continue
        if valor_antes == valor_depois:
            continue
        changes.append({
            'campo': label,
            'antes': _format_decimal_auditoria(valor_antes) if field_name in PRODUTO_AUDIT_DECIMAL_FIELDS else (valor_antes or '-'),
            'depois': _format_decimal_auditoria(valor_depois) if field_name in PRODUTO_AUDIT_DECIMAL_FIELDS else (valor_depois or '-'),
        })
    return changes




def _registrar_produto_log(request, produto, evento, detalhes='', changes=None, acao=None):
    try:
        LogSistema.objects.create(
            filial=getattr(request, 'filial_ativa', None),
            usuario=request.user,
            modulo='produtos',
            acao=acao or LogSistema.Acao.EDITAR,
            tabela_afetada=Produto._meta.db_table,
            registro_id=produto.pk,
            dados_novos={'evento': evento, 'detalhes': detalhes, 'changes': changes or []},
            ip_acesso=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
        )
    except Exception:
        pass


def _produto_log_detail(changes, detalhes):
    if not changes:
        return detalhes
    nomes = [change.get('campo') for change in changes if change.get('campo')]
    if not nomes:
        return detalhes
    if len(nomes) == 1:
        return f'{nomes[0]} alterado.'
    return f'{len(nomes)} campos alterados: {", ".join(nomes)}.'


def _produto_log_entries(produto, usuario_padrao=None, limit=10, offset=0):
    entries = []
    has_created_log = False
    source_limit = None if limit is None else offset + limit
    fallback_usuario = getattr(usuario_padrao, 'nome', '') or 'Sistema'
    log_qs = LogSistema.objects.filter(
        tabela_afetada=Produto._meta.db_table,
        registro_id=produto.pk,
    ).select_related('usuario').order_by('-data_hora')
    if source_limit:
        log_qs = log_qs[:max(source_limit, 40)]
    for log in log_qs:
        dados = log.dados_novos or {}
        evento = dados.get('evento')
        changes = dados.get('changes') or []
        if evento:
            acao = evento
        elif log.acao == LogSistema.Acao.CRIAR:
            acao = 'Produto criado'
            has_created_log = True
        elif log.acao == LogSistema.Acao.EXCLUIR:
            acao = 'Produto excluido'
        else:
            if not changes:
                continue
            acao = 'Produto editado'
        if log.acao == LogSistema.Acao.CRIAR:
            has_created_log = True
        if log.usuario:
            fallback_usuario = log.usuario.nome
        detalhe = dados.get('detalhes') or ('Alteracoes no cadastro' if changes else 'Cadastro do produto')
        entries.append({
            'data': log.data_hora,
            'usuario': log.usuario.nome if log.usuario else 'Sistema',
            'acao': acao,
            'quantidade': f'{len(changes)} campos' if changes else '',
            'detalhes': _produto_log_detail(changes, detalhe),
            'changes': changes,
            'kind': 'created' if log.acao == LogSistema.Acao.CRIAR else 'edit',
        })

    mov_qs = MovimentacaoEstoque.objects.filter(
        produto=produto,
    ).select_related('usuario').order_by('-data_movimentacao')
    if source_limit:
        mov_qs = mov_qs[:max(source_limit, 30)]
    for mov in mov_qs:
        estoque_changes = [
            {
                'campo': 'Estoque',
                'antes': str(mov.quantidade_anterior),
                'depois': str(mov.quantidade_posterior),
            }
        ]
        entries.append({
            'data': mov.data_movimentacao,
            'usuario': mov.usuario.nome if mov.usuario else 'Sistema',
            'acao': mov.get_tipo_operacao_display(),
            'quantidade': mov.quantidade,
            'detalhes': (
                f'Estoque alterado de {mov.quantidade_anterior} para {mov.quantidade_posterior}.'
                if mov.quantidade_anterior != mov.quantidade_posterior
                else (mov.observacao or 'Movimentacao de estoque')
            ),
            'changes': estoque_changes,
            'kind': 'stock',
        })

    if produto.created_at and not has_created_log:
        entries.append({
            'data': produto.created_at,
            'usuario': fallback_usuario,
            'acao': 'Produto criado',
            'quantidade': '',
            'detalhes': 'Registro inicial',
            'changes': [],
            'kind': 'created',
        })
    entries = sorted(entries, key=lambda item: item['data'], reverse=True)
    return entries[offset:offset + limit] if limit is not None else entries


def _produto_log_total_count(produto):
    has_created_log = LogSistema.objects.filter(
        tabela_afetada=Produto._meta.db_table,
        registro_id=produto.pk,
        acao=LogSistema.Acao.CRIAR,
    ).exists()
    total = LogSistema.objects.filter(
        tabela_afetada=Produto._meta.db_table,
        registro_id=produto.pk,
    ).count()
    total += MovimentacaoEstoque.objects.filter(produto=produto).count()
    if produto.created_at and not has_created_log:
        total += 1
    return total


def _produto_log_context(produto, usuario_padrao=None):
    logs = _produto_log_entries(produto, usuario_padrao=usuario_padrao, limit=10)
    total = _produto_log_total_count(produto)
    return {
        'produto_logs': logs,
        'produto_log_usuarios': sorted({item['usuario'] for item in logs if item.get('usuario')}),
        'produto_log_campos': sorted({
            change['campo']
            for item in logs
            for change in item.get('changes', [])
            if change.get('campo')
        }),
        'produto_log_total': total,
        'produto_log_next_offset': len(logs),
        'produto_log_has_more': total > len(logs),
    }


def _produto_log_export_rows(produto, usuario_padrao=None):
    rows = []
    for item in _produto_log_entries(produto, usuario_padrao=usuario_padrao, limit=None):
        data = timezone.localtime(item['data']).strftime('%d/%m/%Y %H:%M') if item.get('data') else ''
        base = {
            'data': data,
            'usuario': item.get('usuario') or '',
            'acao': item.get('acao') or '',
            'quantidade': item.get('quantidade') or '',
            'detalhes': item.get('detalhes') or '',
        }
        changes = item.get('changes') or []
        if not changes:
            rows.append({**base, 'campo': '', 'antes': '', 'depois': ''})
            continue
        for change in changes:
            rows.append({
                **base,
                'campo': change.get('campo') or '',
                'antes': change.get('antes') or '',
                'depois': change.get('depois') or '',
            })
    return rows


def _produto_log_csv_response(produto, request):
    response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
    response['Content-Disposition'] = f'attachment; filename="log_produto_{produto.pk}.csv"'
    response.write('\ufeff')
    writer = csv.writer(response, delimiter=';')
    writer.writerow(['Data e hora', 'Usuario', 'Acao', 'Quantidade', 'Detalhe', 'Campo', 'Antes', 'Depois'])
    for row in _produto_log_export_rows(produto, usuario_padrao=request.user):
        writer.writerow([
            row['data'], row['usuario'], row['acao'], row['quantidade'],
            row['detalhes'], row['campo'], row['antes'], row['depois'],
        ])
    return response


def _produto_log_pdf_response(produto, request):
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="log_produto_{produto.pk}.pdf"'
    doc = SimpleDocTemplate(response, pagesize=landscape(A4), rightMargin=20, leftMargin=20, topMargin=20, bottomMargin=20)
    styles = getSampleStyleSheet()
    elementos = [
        Paragraph(f'Log do produto #{produto.pk} - {produto.descricao}', styles['Title']),
        Paragraph(f'Gerado em {timezone.localtime().strftime("%d/%m/%Y %H:%M")}', styles['Normal']),
        Spacer(1, 10),
    ]
    dados = [['Data/hora', 'Usuario', 'Acao', 'Qtd.', 'Detalhe', 'Campo', 'Antes', 'Depois']]
    for row in _produto_log_export_rows(produto, usuario_padrao=request.user):
        dados.append([
            row['data'],
            row['usuario'] or '-',
            row['acao'] or '-',
            str(row['quantidade'] or '-'),
            row['detalhes'] or '-',
            row['campo'] or '-',
            row['antes'] or '-',
            row['depois'] or '-',
        ])
    table = Table(dados, repeatRows=1, colWidths=[62, 78, 72, 42, 130, 84, 130, 130])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2563eb')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#d1d5db')),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 7),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8fafc')]),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    elementos.append(table)
    doc.build(elementos)
    return response


PRODUTO_STEP_FIELDS = {
    1: {
        'descricao', 'categoria', 'subcategoria', 'marca', 'fornecedor',
        'tipo_produto', 'linha_producao',
        'unidade_medida', 'unidade_medida_compra', 'fator_conversao_compra',
        'codigo', 'codigo_barras', 'codigo_barras_extra_1', 'codigo_barras_extra_2',
        'codigo_barras_extra_3', 'descricao_curta', 'observacao', 'ativo',
    },
    2: {
        'condicao_armazenamento', 'temperatura_minima', 'temperatura_maxima',
        'umidade_relativa', 'controla_lote', 'controla_validade',
        'especificacoes_tecnicas', 'descricao_completa',
    },
    3: {
        'ncm', 'cest', 'origem_produto', 'classe_fiscal', 'cfop_venda_interna',
        'cfop_venda_interestadual', 'cfop_venda_exportacao', 'cfop_compra',
        'cfop_devolucao', 'cfop_devolucao_compra', 'cst_csosn', 'cst_pis',
        'cst_cofins', 'cst_ipi', 'codigo_enquadramento_ipi', 'aliquota_ipi',
        'codigo_beneficio_fiscal_icms', 'modalidade_bc_icms', 'aliquota_icms',
        'reducao_bc_icms', 'aliquota_fcp', 'modalidade_bc_icms_st',
        'mva_icms_st', 'reducao_bc_icms_st', 'aliquota_icms_st',
        'aliquota_fcp_st', 'aliquota_pis', 'aliquota_cofins',
        'natureza_receita_pis_cofins', 'ex_tipi', 'cst_cbs',
        'classificacao_tributaria_cbs', 'aliquota_cbs', 'reducao_cbs',
        'cst_ibs', 'classificacao_tributaria_ibs', 'aliquota_ibs_uf',
        'aliquota_ibs_municipal', 'reducao_ibs', 'cst_is',
        'classificacao_tributaria_is', 'aliquota_is',
        'informacoes_complementares_fiscais', 'beneficios_fiscais_observacoes',
    },
    4: {
        'preco_venda', 'preco_custo', 'moeda', 'margem_desejada', 'preco_minimo',
        'preco_promocional', 'promocao_inicio', 'promocao_fim',
    },
    5: {
        'estoque_quantidade', 'estoque_minimo', 'estoque_maximo', 'ponto_reposicao',
        'estoque_seguranca', 'lead_time_reposicao_dias', 'localizacao_estoque',
        'metodo_saida', 'dias_aviso_vencimento', 'permite_venda_sem_estoque',
    },
    6: {
        'vendido_por_peso_granel', 'codigo_balanca', 'tara_padrao',
        'variacao_peso_permitida', 'peso_minimo_venda', 'unidade_pesagem',
        'fracionavel', 'gera_etiqueta_balanca',
    },
    7: {
        'peso_bruto', 'peso_liquido', 'unidade_peso', 'largura', 'altura',
        'profundidade', 'unidade_dimensao', 'tipo_embalagem',
        'quantidade_por_embalagem', 'empilhamento_maximo',
    },
}


def _produto_form_feedback(form):
    error_fields = []
    error_steps = set()
    if not form.errors:
        return [], '[]'

    field_to_step = {
        field_name: step
        for step, field_names in PRODUTO_STEP_FIELDS.items()
        for field_name in field_names
    }
    for field_name in form.errors:
        if field_name == '__all__':
            for error in form.non_field_errors():
                error_fields.append(str(error))
            continue
        field = form.fields.get(field_name)
        label = field.label if field else field_name
        error_fields.append(label)
        if field_name in field_to_step:
            error_steps.add(field_to_step[field_name])

    return error_fields, json.dumps(sorted(error_steps))


@method_decorator(gzip_page, name='dispatch')
class ProdutoListView(PermissaoRequiredMixin, View):
    permissao_modulo = 'produtos'
    permissao_acao = 'ver'
    template_name = 'produtos/produto/list.html'

    def get(self, request):
        qs = _produto_queryset_filtrado(request, incluir_inativos_por_padrao=True)
        busca = request.GET.get('q', '').strip()
        categoria_id = request.GET.get('categoria', '')
        subcategoria_id = request.GET.get('subcategoria', '')
        marca_id = request.GET.get('marca', '')
        fornecedor_id = request.GET.get('fornecedor', '')
        status = request.GET.get('status') or 'todos'
        somente_com_estoque = request.GET.get('com_estoque') == '1'
        ordem = request.GET.get('ordem', 'id')
        ver_todos = request.GET.get('ver') == 'todos'
        if ver_todos:
            produtos_completos = list(qs)
            page_obj = Paginator(
                produtos_completos,
                max(len(produtos_completos), 1),
            ).get_page(1)
        else:
            page_obj = Paginator(qs, 50).get_page(request.GET.get('page'))
        produtos_pagina = list(page_obj.object_list)
        produto_ids = [produto.pk for produto in produtos_pagina]
        filiais_para_inativar = {}
        filiais_para_ativar = {}
        filiais_replicacao = _filiais_replicacao_produtos(request.filial_ativa)
        if produto_ids:
            for vinculo in ProdutoFilial.objects.filter(
                produto_id__in=produto_ids,
                filial__in=filiais_replicacao,
                ativo=True,
            ).select_related('filial').order_by('filial__nome_fantasia', 'filial__razao_social'):
                filiais_para_inativar.setdefault(vinculo.produto_id, []).append({
                    'id': vinculo.filial_id,
                    'nome': vinculo.filial.nome_fantasia or vinculo.filial.razao_social,
                })
            for vinculo in ProdutoFilial.objects.filter(
                produto_id__in=produto_ids,
                filial__in=filiais_replicacao,
                ativo=False,
            ).select_related('filial').order_by('filial__nome_fantasia', 'filial__razao_social'):
                filiais_para_ativar.setdefault(vinculo.produto_id, []).append({
                    'id': vinculo.filial_id,
                    'nome': vinculo.filial.nome_fantasia or vinculo.filial.razao_social,
                })
        for produto in produtos_pagina:
            produto.ativo_filial = bool(getattr(produto, 'ativo_filial', produto.ativo))
            produto.filiais_inativacao_json = json.dumps(
                filiais_para_inativar.get(produto.pk, []),
                ensure_ascii=False,
            )
            produto.filiais_ativacao_json = json.dumps(
                filiais_para_ativar.get(produto.pk, []),
                ensure_ascii=False,
            )
        multi_filial = request.user.empresa.filiais.filter(ativo=True).count() > 1
        if ver_todos and request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return render(request, self.template_name, {
                'ver_todos': True,
                'page_obj': page_obj,
                'page_querystring': '',
                'sort_urls': {},
                'produtos': produtos_pagina,
                'multi_filial': multi_filial,
                'busca': busca,
                'categoria_id': categoria_id,
                'subcategoria_id': subcategoria_id,
                'marca_id': marca_id,
                'fornecedor_id': fornecedor_id,
                'status': status,
                'somente_com_estoque': somente_com_estoque,
                'ordem': ordem,
                'categorias': (),
                'subcategorias': (),
                'subcategorias_por_categoria_json': '{}',
                'marcas': (),
                'fornecedores': (),
                'pode_exportar': False,
                'pode_criar': False,
                'pode_editar': request.user.tem_permissao('produtos', 'editar'),
                'pode_excluir': request.user.tem_permissao('produtos', 'excluir'),
                'inline_categorias_json': '[]',
                'inline_subcategorias_json': '[]',
                'inline_tipos_produto_json': '[]',
                'inline_unidades_json': '[]',
            })
        query_params = request.GET.copy()
        query_params.pop('page', None)
        sort_urls = {}
        for key, value in {
            'id': 'id_desc' if ordem == 'id' else 'id',
            'referencia': 'referencia_desc' if ordem == 'referencia' else 'referencia',
            'nome': 'za' if ordem == 'az' else 'az',
            'custo': 'custo_desc' if ordem == 'custo' else 'custo',
            'preco': 'preco_desc' if ordem == 'preco' else 'preco',
            'criado': 'criado_asc' if ordem == 'criado_desc' else 'criado_desc',
        }.items():
            params = request.GET.copy()
            params.pop('page', None)
            params['ordem'] = value
            sort_urls[key] = params.urlencode()

        return render(request, self.template_name, {
            'ver_todos': ver_todos,
            'page_obj': page_obj,
            'page_querystring': query_params.urlencode(),
            'sort_urls': sort_urls,
            'produtos': produtos_pagina,
            'multi_filial': multi_filial,
            'busca': busca,
            'categoria_id': categoria_id,
            'subcategoria_id': subcategoria_id,
            'marca_id': marca_id,
            'fornecedor_id': fornecedor_id,
            'status': status,
            'somente_com_estoque': somente_com_estoque,
            'ordem': ordem,
            'categorias': CategoriaProduto.objects.for_filial(request.filial_ativa).filter(
                empresa=request.user.empresa,
                ativo=True, categoria_pai__isnull=True,
            ).order_by('nome'),
            'subcategorias': CategoriaProduto.objects.for_filial(request.filial_ativa).filter(
                empresa=request.user.empresa,
                ativo=True,
                categoria_pai_id=categoria_id,
            ).order_by('nome') if categoria_id else CategoriaProduto.objects.for_filial(request.filial_ativa).filter(
                empresa=request.user.empresa,
                ativo=True,
                categoria_pai__isnull=False,
            ).order_by('categoria_pai__nome', 'nome'),
            'subcategorias_por_categoria_json': json.dumps({
                '': [
                    {'id': item.id, 'nome': item.nome}
                    for item in CategoriaProduto.objects.for_filial(request.filial_ativa).filter(
                        empresa=request.user.empresa,
                        ativo=True,
                        categoria_pai__isnull=False,
                    ).order_by('categoria_pai__nome', 'nome')
                ],
                **{
                    str(categoria_id): [
                        {'id': item.id, 'nome': item.nome}
                        for item in CategoriaProduto.objects.for_filial(request.filial_ativa).filter(
                            empresa=request.user.empresa,
                            ativo=True,
                            categoria_pai_id=categoria_id,
                        ).order_by('nome')
                    ]
                    for categoria_id in CategoriaProduto.objects.for_filial(request.filial_ativa).filter(
                        empresa=request.user.empresa,
                        ativo=True,
                        categoria_pai__isnull=True,
                    ).values_list('id', flat=True)
                },
            }),
            'marcas': MarcaProduto.objects.for_filial(request.filial_ativa).filter(
                empresa=request.user.empresa,
                ativo=True,
            ).order_by('nome'),
            'fornecedores': Fornecedor.objects.for_filial(request.filial_ativa).filter(
                ativo=True,
            ).order_by('nome_fantasia', 'razao_social'),
            'pode_exportar': _usuario_pode_exportar(request),
            'pode_criar': request.user.tem_permissao('produtos', 'criar'),
            'pode_editar': request.user.tem_permissao('produtos', 'editar'),
            'pode_excluir': request.user.tem_permissao('produtos', 'excluir'),
            'inline_categorias_json': json.dumps([
                {'id': item.id, 'nome': item.nome}
                for item in CategoriaProduto.objects.for_filial(request.filial_ativa).filter(
                    empresa=request.user.empresa, ativo=True, categoria_pai__isnull=True,
                ).order_by('nome')
            ]),
            'inline_subcategorias_json': json.dumps([
                {'id': item.id, 'nome': item.nome, 'categoria_id': item.categoria_pai_id}
                for item in CategoriaProduto.objects.for_filial(request.filial_ativa).filter(
                    empresa=request.user.empresa, ativo=True, categoria_pai__isnull=False,
                ).order_by('categoria_pai__nome', 'nome')
            ]),
            'inline_tipos_produto_json': json.dumps([
                {'id': value, 'nome': label}
                for value, label in Produto.TipoProduto.choices
            ]),
            'inline_unidades_json': json.dumps([
                {'id': item.id, 'nome': str(item)}
                for item in UnidadeMedida.objects.for_filial(request.filial_ativa).filter(
                    empresa=request.user.empresa, ativo=True,
                ).order_by('sigla')
            ]),
        })


class ProdutoFiscalListView(PermissaoRequiredMixin, View):
    permissao_modulo = 'produtos'
    permissao_acao = 'ver'
    template_name = 'produtos/produto/fiscal_list.html'

    def get(self, request):
        qs = _produto_fiscal_queryset(request)
        busca = request.GET.get('q', '').strip()
        categoria_id = request.GET.get('categoria', '')
        fornecedor_id = request.GET.get('fornecedor', '')
        status = request.GET.get('status') or 'todos'
        ordem = request.GET.get('ordem', 'id')
        ncm = request.GET.get('ncm', '').strip()
        cfop = request.GET.get('cfop', '').strip()
        ipi = request.GET.get('ipi', '').strip()
        pis_cofins = request.GET.get('pis_cofins', '').strip()
        page_obj = Paginator(qs, 50).get_page(request.GET.get('page'))
        produtos_pagina = list(page_obj.object_list)
        for produto in produtos_pagina:
            produto.ativo_filial = bool(getattr(produto, 'ativo_filial', produto.ativo))
            produto.fiscal_pendencias = _produto_fiscal_pendencias(produto)
            produto.fiscal_ok = not produto.fiscal_pendencias

        query_params = request.GET.copy()
        query_params.pop('page', None)
        sort_urls = {}
        for key, value in {
            'id': 'id_desc' if ordem == 'id' else 'id',
            'nome': 'za' if ordem == 'az' else 'az',
            'referencia': 'referencia_desc' if ordem == 'referencia' else 'referencia',
        }.items():
            params = request.GET.copy()
            params.pop('page', None)
            params['ordem'] = value
            sort_urls[key] = params.urlencode()

        return render(request, self.template_name, {
            'page_obj': page_obj,
            'page_querystring': query_params.urlencode(),
            'sort_urls': sort_urls,
            'produtos': produtos_pagina,
            'busca': busca,
            'categoria_id': categoria_id,
            'fornecedor_id': fornecedor_id,
            'status': status,
            'ordem': ordem,
            'ncm': ncm,
            'cfop': cfop,
            'ipi': ipi,
            'pis_cofins': pis_cofins,
            'regime_fiscal_badge': _filial_regime_fiscal_badge(request.filial_ativa),
            'categorias': CategoriaProduto.objects.for_filial(request.filial_ativa).filter(
                empresa=request.user.empresa,
                ativo=True, categoria_pai__isnull=True,
            ).order_by('nome'),
            'fornecedores': Fornecedor.objects.for_filial(request.filial_ativa).filter(
                ativo=True,
            ).order_by('nome_fantasia', 'razao_social'),
            'pode_editar': request.user.tem_permissao('produtos', 'editar'),
            'inline_categorias_json': json.dumps([
                {'id': item.id, 'nome': item.nome}
                for item in CategoriaProduto.objects.for_filial(request.filial_ativa).filter(
                    empresa=request.user.empresa, ativo=True, categoria_pai__isnull=True,
                ).order_by('nome')
            ]),
            'inline_subcategorias_json': json.dumps([
                {'id': item.id, 'nome': item.nome, 'categoria_id': item.categoria_pai_id}
                for item in CategoriaProduto.objects.for_filial(request.filial_ativa).filter(
                    empresa=request.user.empresa, ativo=True, categoria_pai__isnull=False,
                ).order_by('categoria_pai__nome', 'nome')
            ]),
            'inline_unidades_json': json.dumps([
                {'id': item.id, 'nome': str(item)}
                for item in UnidadeMedida.objects.for_filial(request.filial_ativa).filter(
                    empresa=request.user.empresa, ativo=True,
                ).order_by('sigla')
            ]),
            'inline_origens_produto_json': json.dumps([
                {'id': value, 'nome': label}
                for value, label in Produto.OrigemProduto.choices
            ]),
        })


class ProdutoCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'produtos'
    permissao_acao = 'criar'
    template_name = 'produtos/produto/form.html'

    def ajustar_estoque(self, request, produto, quantidade_nova):
        if quantidade_nova is None:
            return
        quantidade_nova = Decimal(quantidade_nova)
        if quantidade_nova == 0:
            return
        if produto.controla_lote:
            messages.warning(
                request,
                'Estoque inicial de produto com lote deve ser lancado em Estoque > Lotes.',
            )
            return
        MovimentacaoService.ajustar_manual(
            produto_id=produto.pk,
            filial_id=request.filial_ativa.pk,
            quantidade_nova=quantidade_nova,
            usuario_id=request.user.pk,
            justificativa='Ajuste informado no cadastro do produto.',
        )

    def get_context(self, form, produto=None, request=None):
        error_fields, error_steps_json = _produto_form_feedback(form)
        imagem_preview_url = ''
        if produto and produto.foto_url:
            imagem_preview_url = reverse('produtos:produto-image-file', kwargs={'pk': produto.pk})
        elif form.initial.get('foto_url'):
            imagem_preview_url = form.initial.get('foto_url')
        return {
            'form': form,
            'produto': produto,
            'proximo_id': produto.codigo_replicacao if produto else _proximo_codigo_produto(),
            'estoque_atual': 0,
            'title': 'Novo Produto',
            'cancel_url': reverse_lazy('produtos:produto-list'),
            'error_fields': error_fields,
            'error_steps_json': error_steps_json,
            'imagem_preview_url': imagem_preview_url,
            'subcategorias_form_json': _subcategorias_form_json(request.user.empresa, request.filial_ativa) if request else '{}',
        }

    def get(self, request):
        form = ProdutoForm(empresa=request.user.empresa, filial=request.filial_ativa, estoque_atual=0)
        return render(request, self.template_name, self.get_context(form, request=request))

    def post(self, request):
        form = ProdutoForm(
            request.POST, request.FILES,
            empresa=request.user.empresa,
            filial=request.filial_ativa,
            estoque_atual=0,
        )
        if form.is_valid():
            with transaction.atomic():
                ativo_filial = form.cleaned_data.get('ativo', True)
                produto = form.save(commit=False)
                produto.filial = request.filial_ativa
                produto.ativo = True
                _salvar_imagem_produto(form, produto)
                produto.calcular_margem()
                produto.save()
                _definir_status_produto_filial(produto, request.filial_ativa, ativo_filial)
                _registrar_produto_log(
                    request,
                    produto,
                    'Produto criado',
                    'Produto cadastrado no sistema.',
                    changes=[
                        {'campo': 'Nome', 'antes': '-', 'depois': produto.descricao},
                        {'campo': 'Referencia / codigo interno', 'antes': '-', 'depois': produto.codigo or '-'},
                    ],
                    acao=LogSistema.Acao.CRIAR,
                )
                self.ajustar_estoque(request, produto, form.cleaned_data.get('estoque_quantidade'))
            _sincronizar_produto_sem_quebrar(request, produto)
            messages.success(request, f'Produto "{produto}" criado.')
            return redirect('produtos:produto-update', pk=produto.pk)
        return render(request, self.template_name, self.get_context(form, request=request))


class ProdutoDuplicarView(ProdutoCreateView):
    permissao_modulo = 'produtos'
    permissao_acao = 'criar'

    def get_produto_origem(self, request, pk):
        return get_object_or_404(
            _produtos_filial_qs(request, incluir_inativos=True), pk=pk,
        )

    def get_context(self, form, produto=None, request=None, produto_origem=None):
        context = super().get_context(form, produto=produto, request=request)
        context.update({
            'duplicando': True,
            'produto_origem': produto_origem,
            'title': f'Duplicar produto - {produto_origem}' if produto_origem else 'Duplicar produto',
            'submit_text': 'Criar produto duplicado',
        })
        return context

    def get(self, request, pk):
        produto_origem = self.get_produto_origem(request, pk)
        form = ProdutoForm(
            empresa=request.user.empresa,
            filial=request.filial_ativa,
            estoque_atual=0,
            initial=_produto_duplicate_initial(produto_origem),
        )
        return render(
            request,
            self.template_name,
            self.get_context(form, request=request, produto_origem=produto_origem),
        )

    def post(self, request, pk):
        produto_origem = self.get_produto_origem(request, pk)
        form = ProdutoForm(
            request.POST,
            request.FILES,
            empresa=request.user.empresa,
            filial=request.filial_ativa,
            estoque_atual=0,
        )
        if form.is_valid():
            with transaction.atomic():
                ativo_filial = form.cleaned_data.get('ativo', True)
                produto = form.save(commit=False)
                produto.filial = request.filial_ativa
                produto.ativo = True
                _salvar_imagem_produto(form, produto)
                produto.calcular_margem()
                produto.save()
                _definir_status_produto_filial(produto, request.filial_ativa, ativo_filial)
                _registrar_produto_log(
                    request,
                    produto,
                    'Produto duplicado',
                    f'Criado a partir de {produto_origem}.',
                    changes=[
                        {'campo': 'Produto de origem', 'antes': '-', 'depois': str(produto_origem)},
                        {'campo': 'Nome', 'antes': '-', 'depois': produto.descricao},
                        {'campo': 'Referencia / codigo interno', 'antes': '-', 'depois': produto.codigo or '-'},
                        {'campo': 'Codigo de barras', 'antes': '-', 'depois': produto.codigo_barras or '-'},
                    ],
                    acao=LogSistema.Acao.CRIAR,
                )
                self.ajustar_estoque(request, produto, form.cleaned_data.get('estoque_quantidade'))
            _sincronizar_produto_sem_quebrar(request, produto)
            messages.success(request, f'Produto "{produto}" criado a partir da duplicacao.')
            return redirect('produtos:produto-update', pk=produto.pk)
        return render(
            request,
            self.template_name,
            self.get_context(form, request=request, produto_origem=produto_origem),
        )


def _vinculos_fornecedor(produto):
    """
    Os produtos de nota que ja' foram associados a este produto interno.

    NUNCA DERRUBA A TELA. O cadastro do produto e' a tela mais usada do modulo;
    ela nao pode deixar de abrir porque a consulta dos vinculos falhou. Em
    banco com schema parcial -- migration a meio caminho -- era exatamente isso
    que acontecia. Sem vinculos a tela mostra o estado vazio, que ja' existe no
    template.
    """
    if not produto or not produto.pk:
        return []
    try:
        return list(
            ProdutoFornecedorEquivalencia.objects
            .select_related('fornecedor')
            .filter(produto=produto, ativo=True)
            .order_by('fornecedor__razao_social', 'codigo_fornecedor')
        )
    except Exception:  # noqa: BLE001 -- a tela abre de qualquer jeito
        logging.getLogger(__name__).exception(
            'Falha ao carregar vinculos de fornecedor do produto %s', produto.pk,
        )
        return []


def _campo_do_erro_de_banco(erro):
    """
    Traduz `null value in column "aliquota_cbs"` no rotulo que o usuario ve.

    O ERP ganhou campos fiscais novos (CBS, IBS, IS) com NOT NULL. Formulario
    salvo de tela antiga, ou banco a frente do codigo, batia num IntegrityError
    cru: quinhentos na cara de quem so' queria corrigir a descricao, sem dizer
    QUAL campo faltou. O nome da coluna esta na mensagem do proprio banco.
    """
    texto = str(erro)
    achado = re.search(r'column "(\w+)"', texto) or re.search(r"column '(\w+)'", texto)
    if not achado:
        return None, None
    coluna = achado.group(1)
    return coluna, PRODUTO_AUDIT_FIELDS.get(coluna)


class ProdutoUpdateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'produtos'
    permissao_acao = 'editar'
    template_name = 'produtos/produto/form.html'

    def get_estoque_atual(self, request, produto):
        estoque = Estoque.objects.filter(
            produto=produto, filial=request.filial_ativa,
        ).first()
        return estoque.quantidade_atual if estoque else Decimal('0')

    def ajustar_estoque(self, request, produto, quantidade_nova, quantidade_atual):
        if quantidade_nova is None:
            return
        quantidade_nova = Decimal(quantidade_nova)
        if quantidade_nova == quantidade_atual:
            return
        if produto.controla_lote:
            messages.warning(
                request,
                'Estoque de produto com lote deve ser ajustado pelo modulo de lotes.',
            )
            return
        MovimentacaoService.ajustar_manual(
            produto_id=produto.pk,
            filial_id=request.filial_ativa.pk,
            quantidade_nova=quantidade_nova,
            usuario_id=request.user.pk,
            justificativa='Ajuste informado na edicao do produto.',
        )

    def get_context(self, request, form, produto):
        estoque_atual = self.get_estoque_atual(request, produto)
        error_fields, error_steps_json = _produto_form_feedback(form)
        context = {
            'form': form,
            'produto': produto,
            'proximo_id': produto.codigo_replicacao,
            'estoque_atual': estoque_atual,
            'title': f'Editar - {produto}',
            'cancel_url': reverse_lazy('produtos:produto-list'),
            'error_fields': error_fields,
            'error_steps_json': error_steps_json,
            'imagem_preview_url': (
                reverse('produtos:produto-image-file', kwargs={'pk': produto.pk})
                if produto.foto_url else ''
            ),
            'subcategorias_form_json': _subcategorias_form_json(request.user.empresa, request.filial_ativa),
            # O template ja' desenhava as duas coisas ha' tempo -- a tabela de
            # vinculos e o modo popup. Faltava so' quem enchesse o contexto.
            'vinculos_fornecedor': _vinculos_fornecedor(produto),
            'popup_mode': self._popup(request),
        }
        context.update(_produto_log_context(produto, usuario_padrao=request.user))
        return context

    @staticmethod
    def _popup(request):
        """
        A conferencia de entrada abre esta tela em `?popup=1`.

        Ali o usuario esta' no meio de conferir uma nota e so' quer corrigir o
        cadastro do produto da linha. Sair da conferencia para isso perderia o
        lugar dele na nota; por isso a tela abre sem menu nem cabecalho, e ao
        salvar avisa a janela de origem em vez de redirecionar.
        """
        return str(request.GET.get('popup', '')) == '1'

    def get(self, request, pk):
        produto = get_object_or_404(
            _produtos_filial_qs(request, incluir_inativos=True), pk=pk,
        )
        vinculo = _produto_vinculo_filial(produto, request.filial_ativa)
        form = ProdutoForm(
            instance=produto,
            empresa=request.user.empresa,
            filial=request.filial_ativa,
            estoque_atual=self.get_estoque_atual(request, produto),
        )
        form.initial['ativo'] = vinculo.ativo
        form.fields['ativo'].initial = vinculo.ativo
        return render(request, self.template_name, self.get_context(request, form, produto))

    def post(self, request, pk):
        produto = get_object_or_404(
            _produtos_filial_qs(request, incluir_inativos=True), pk=pk,
        )
        vinculo = _produto_vinculo_filial(produto, request.filial_ativa)
        status_filial_antes = vinculo.ativo
        snapshot_antes = _produto_audit_snapshot(produto)
        estoque_atual = self.get_estoque_atual(request, produto)
        form = ProdutoForm(
            request.POST,
            request.FILES,
            instance=produto,
            empresa=request.user.empresa,
            filial=request.filial_ativa,
            estoque_atual=estoque_atual,
        )
        if form.is_valid():
            try:
                with transaction.atomic():
                    ativo_filial = form.cleaned_data.get('ativo', True)
                    produto = form.save(commit=False)
                    produto.ativo = True
                    _salvar_imagem_produto(form, produto)
                    produto.calcular_margem()
                    produto.save()
                    _definir_status_produto_filial(produto, request.filial_ativa, ativo_filial)
                    snapshot_depois = _produto_audit_snapshot(produto)
                    changes = _produto_audit_changes(snapshot_antes, snapshot_depois)
                    if status_filial_antes != ativo_filial:
                        changes.append({
                            'campo': 'Ativo nesta filial',
                            'antes': _sim_nao(status_filial_antes),
                            'depois': _sim_nao(ativo_filial),
                        })
                    if changes:
                        _registrar_produto_log(
                            request,
                            produto,
                            'Produto editado',
                            f'{len(changes)} campo(s) alterado(s).',
                            changes=changes,
                        )
            except IntegrityError as erro:
                # NAO E' QUINHENTOS, E' UM CAMPO FALTANDO. O banco diz qual
                # coluna recusou; devolver isso como erro de formulario e' a
                # diferenca entre "tente de novo" e "preencha a Aliquota CBS".
                return self._erro_ao_salvar(request, form, produto, erro)
            except Exception as erro:  # noqa: BLE001
                return self._erro_ao_salvar(request, form, produto, erro)

            # FORA DA TRANSACAO DE PROPOSITO. O ajuste de estoque fala com o
            # modulo de movimentacao, que pode recusar por inventario aberto ou
            # lote travado. Dentro do `atomic` essa recusa desfazia A EDICAO
            # INTEIRA: quem so' queria corrigir a descricao perdia o trabalho
            # por causa de um campo que nem preencheu.
            try:
                self.ajustar_estoque(
                    request,
                    produto,
                    form.cleaned_data.get('estoque_quantidade'),
                    estoque_atual,
                )
            except Exception:  # noqa: BLE001
                logging.getLogger(__name__).exception(
                    'Falha ao ajustar estoque do produto %s', produto.pk,
                )
                messages.warning(
                    request,
                    'Produto salvo, mas o estoque nao pode ser ajustado agora.',
                )
            _sincronizar_produto_sem_quebrar(request, produto)
            if self._popup(request):
                return render(
                    request,
                    'produtos/produto/popup_salvo.html',
                    {'produto': produto},
                )
            messages.success(request, 'Produto atualizado.')
            return redirect('produtos:produto-list')
        return render(request, self.template_name, self.get_context(request, form, produto))

    def _erro_ao_salvar(self, request, form, produto, erro):
        """Devolve o formulario com o motivo, em vez de estourar em 500."""
        logging.getLogger(__name__).exception(
            'Falha ao salvar o produto %s', getattr(produto, 'pk', None),
        )
        coluna, rotulo = _campo_do_erro_de_banco(erro)
        if rotulo:
            mensagem = f'{rotulo}: obrigatorio para este produto. Informe 0,00 se nao se aplica.'
            if coluna in form.fields:
                form.add_error(coluna, mensagem)
            else:
                form.add_error(None, mensagem)
        elif coluna:
            form.add_error(
                None,
                f'O campo "{coluna}" e obrigatorio. Informe 0,00 se nao se aplica.',
            )
        else:
            form.add_error(None, 'Nao foi possivel salvar o produto. Tente novamente.')
        return render(
            request, self.template_name, self.get_context(request, form, produto),
        )


class ProdutoDeleteView(PermissaoRequiredMixin, View):
    permissao_modulo = 'produtos'
    permissao_acao = 'excluir'

    def post(self, request, pk):
        produto = get_object_or_404(
            _produtos_filial_qs(request, incluir_inativos=True), pk=pk,
        )
        _definir_status_produto_filial(produto, request.filial_ativa, False)
        _registrar_produto_log(request, produto, 'Produto inativado', 'Inativacao pela tela de produtos')
        messages.success(request, f'Produto "{produto}" desativado.')
        return redirect('produtos:produto-list')


class ProdutoToggleAtivoView(PermissaoRequiredMixin, View):
    permissao_modulo = 'produtos'
    permissao_acao = 'editar'

    def post(self, request, pk):
        produto = get_object_or_404(
            _produtos_filial_qs(request, incluir_inativos=True), pk=pk,
        )
        vinculo = _produto_vinculo_filial(produto, request.filial_ativa)
        estava_ativo = vinculo.ativo
        resposta_json = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        avisos = []

        def notificar(nivel, texto):
            avisos.append(texto)
            if not resposta_json:
                getattr(messages, nivel)(request, texto)

        zerar_estoque = request.POST.get('zerar_estoque') == '1'
        filiais_inativar_ids = [
            int(filial_id)
            for filial_id in request.POST.getlist('filiais_inativar')
            if str(filial_id).isdigit()
        ]
        filiais_ativar_ids = [
            int(filial_id)
            for filial_id in request.POST.getlist('filiais_ativar')
            if str(filial_id).isdigit()
        ]
        novo_status = not estava_ativo
        with transaction.atomic():
            if not produto.ativo:
                produto.ativo = True
                produto.save(update_fields=['ativo', 'updated_at'])
            vinculo.ativo = novo_status
            vinculo.save(update_fields=['ativo', 'updated_at'])
            filiais_inativadas = []
            filiais_ativadas = []
            filiais_replicacao = _filiais_replicacao_produtos(request.filial_ativa)
            if estava_ativo and not novo_status and filiais_inativar_ids:
                filiais_qs = filiais_replicacao.filter(
                    pk__in=filiais_inativar_ids,
                )
                vinculos_filiais = ProdutoFilial.objects.filter(
                    produto=produto,
                    filial__in=filiais_qs,
                    ativo=True,
                ).select_related('filial')
                filiais_inativadas = [
                    item.filial.nome_fantasia or item.filial.razao_social
                    for item in vinculos_filiais
                ]
                vinculos_filiais.update(ativo=False)
            if not estava_ativo and novo_status and filiais_ativar_ids:
                filiais_qs = filiais_replicacao.filter(
                    pk__in=filiais_ativar_ids,
                )
                vinculos_filiais = ProdutoFilial.objects.filter(
                    produto=produto,
                    filial__in=filiais_qs,
                    ativo=False,
                ).select_related('filial')
                filiais_ativadas = [
                    item.filial.nome_fantasia or item.filial.razao_social
                    for item in vinculos_filiais
                ]
                vinculos_filiais.update(ativo=True)
            if estava_ativo and not novo_status and zerar_estoque:
                try:
                    quantidade_zerada, lotes_ignorados = _zerar_estoque_produto(
                        produto,
                        request.filial_ativa,
                        request.user,
                    )
                    if quantidade_zerada:
                        notificar(
                            'success',
                            f'Estoque zerado: {_format_quantidade_produto(quantidade_zerada, produto)}.',
                        )
                    if lotes_ignorados:
                        notificar(
                            'warning',
                            'Alguns lotes nao foram zerados por estarem bloqueados ou em quarentena: '
                            + ', '.join(lotes_ignorados),
                        )
                except Exception as exc:
                    notificar('warning', f'Produto inativado, mas o estoque nao foi zerado: {exc}')
        status = 'ativado' if novo_status else 'desativado'
        _registrar_produto_log(
            request,
            produto,
            'Produto ativado' if novo_status else 'Produto inativado',
            f'Produto {status} pela listagem.',
        )
        if filiais_inativadas:
            notificar(
                'info',
                'Tambem inativado em: ' + ', '.join(filiais_inativadas) + '.',
            )
        if filiais_ativadas:
            notificar(
                'info',
                'Tambem ativado em: ' + ', '.join(filiais_ativadas) + '.',
            )
        mensagem_status = f'Produto "{produto}" {status}.'
        notificar('success', mensagem_status)
        if resposta_json:
            estoque_atual = Estoque.objects.filter(
                produto=produto,
                filial=request.filial_ativa,
            ).values_list('quantidade_atual', flat=True).first() or Decimal('0')
            return JsonResponse({
                'ok': True,
                'active': novo_status,
                'message': mensagem_status,
                'notices': avisos,
                'current_stock': str(estoque_atual),
                'current_stock_display': _format_quantidade_produto(estoque_atual, produto),
            })
        return redirect(request.META.get('HTTP_REFERER', 'produtos:produto-list'))


class ProdutoInlineEditView(PermissaoRequiredMixin, View):
    permissao_modulo = 'produtos'
    permissao_acao = 'editar'

    CAMPOS_TEXTO = {
        'descricao', 'codigo', 'codigo_barras',
        'ncm', 'cest',
        'cfop_venda_interna', 'cfop_venda_interestadual', 'cfop_venda_exportacao',
        'cfop_compra', 'cfop_devolucao', 'cfop_devolucao_compra',
        'cst_csosn', 'cst_pis', 'cst_cofins', 'cst_ipi', 'codigo_enquadramento_ipi',
    }
    CAMPOS_DECIMAIS = {'preco_custo', 'preco_venda'}
    CAMPOS_DECIMAIS_PERCENTUAIS = {'aliquota_ipi', 'aliquota_pis', 'aliquota_cofins'}
    CAMPOS_ESTOQUE = {'estoque_atual'}
    CAMPOS_FK = {'categoria', 'subcategoria', 'unidade_medida', 'classe_fiscal'}
    CAMPOS_ESCOLHA = {'tipo_produto', 'origem_produto'}

    def post(self, request, pk):
        produto = get_object_or_404(
            _produtos_filial_qs(request, incluir_inativos=True), pk=pk,
        )
        field = request.POST.get('field', '').strip()
        value = request.POST.get('value', '').strip()
        campos_permitidos = (
            self.CAMPOS_TEXTO
            | self.CAMPOS_DECIMAIS
            | self.CAMPOS_DECIMAIS_PERCENTUAIS
            | self.CAMPOS_ESTOQUE
            | self.CAMPOS_FK
            | self.CAMPOS_ESCOLHA
        )
        if field not in campos_permitidos:
            return JsonResponse({'ok': False, 'error': 'Campo nao permitido.'}, status=400)
        if field in self.CAMPOS_ESTOQUE:
            return self._atualizar_estoque(request, produto, value)

        snapshot_antes = _produto_audit_snapshot(produto)
        try:
            if field in self.CAMPOS_TEXTO:
                if field == 'descricao' and not value:
                    return JsonResponse({'ok': False, 'error': 'Nome do produto e obrigatorio.'}, status=400)
                if field == 'codigo_barras':
                    value = ''.join(ch for ch in value if ch.isdigit())[:14]
                elif field == 'ncm':
                    value = ''.join(ch for ch in value if ch.isdigit())[:8]
                elif field == 'cest':
                    value = ''.join(ch for ch in value if ch.isdigit())[:7]
                elif field.startswith('cfop_'):
                    value = ''.join(ch for ch in value if ch.isdigit())[:5]
                elif field in {'cst_pis', 'cst_cofins', 'cst_ipi'}:
                    value = value[:2]
                elif field == 'cst_csosn':
                    value = value[:3]
                elif field == 'codigo_enquadramento_ipi':
                    value = value[:3]
                setattr(produto, field, value)
            elif field in self.CAMPOS_DECIMAIS or field in self.CAMPOS_DECIMAIS_PERCENTUAIS:
                setattr(produto, field, _decimal_from_request(value))
            elif field == 'categoria':
                categoria = CategoriaProduto.objects.for_filial(request.filial_ativa).filter(
                    empresa=request.user.empresa, ativo=True, categoria_pai__isnull=True, pk=value,
                ).first() if value else None
                produto.categoria = categoria
                if produto.subcategoria and (
                    not categoria or produto.subcategoria.categoria_pai_id != categoria.pk
                ):
                    produto.subcategoria = None
            elif field == 'subcategoria':
                subcategoria = CategoriaProduto.objects.for_filial(request.filial_ativa).filter(
                    empresa=request.user.empresa, ativo=True, categoria_pai__isnull=False, pk=value,
                ).first() if value else None
                if subcategoria and produto.categoria_id and subcategoria.categoria_pai_id != produto.categoria_id:
                    return JsonResponse({'ok': False, 'error': 'Sub categoria nao pertence a categoria atual.'}, status=400)
                produto.subcategoria = subcategoria
            elif field == 'unidade_medida':
                unidade = UnidadeMedida.objects.for_filial(request.filial_ativa).filter(
                    empresa=request.user.empresa, ativo=True, pk=value,
                ).first()
                if not unidade:
                    return JsonResponse({'ok': False, 'error': 'Unidade obrigatoria.'}, status=400)
                produto.unidade_medida = unidade
            elif field == 'classe_fiscal':
                classe = ClasseFiscal.objects.for_filial(request.filial_ativa).filter(
                    empresa=request.user.empresa, ativo=True, pk=value,
                ).first() if value else None
                produto.classe_fiscal = classe
            elif field == 'tipo_produto':
                if value not in dict(Produto.TipoProduto.choices):
                    return JsonResponse({'ok': False, 'error': 'Tipo de produto invalido.'}, status=400)
                produto.tipo_produto = value
            elif field == 'origem_produto':
                origens = {str(value): value for value, _ in Produto.OrigemProduto.choices}
                if value not in origens:
                    return JsonResponse({'ok': False, 'error': 'Origem do produto invalida.'}, status=400)
                produto.origem_produto = origens[value]
        except Exception:
            return JsonResponse({'ok': False, 'error': 'Valor invalido.'}, status=400)

        produto.calcular_margem()
        produto.save()
        changes = _produto_audit_changes(snapshot_antes, _produto_audit_snapshot(produto))
        if changes:
            _registrar_produto_log(
                request,
                produto,
                'Produto editado',
                f'Edicao rapida na lista: {", ".join(change["campo"] for change in changes)}.',
                changes=changes,
            )
        _sincronizar_produto_sem_quebrar(request, produto)
        return JsonResponse({
            'ok': True,
            'display': self._display(produto, field),
            'margem': f'{produto.margem_atual:.2f}'.replace('.', ','),
            'markup': f'{produto.markup_atual:.2f}'.replace('.', ','),
            'preco_custo': f'R$ {produto.preco_custo:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.'),
            'preco_venda': f'R$ {produto.preco_atual:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.'),
        })

    def _atualizar_estoque(self, request, produto, value):
        estoque = Estoque.objects.filter(produto=produto, filial=request.filial_ativa).first()
        quantidade_atual = estoque.quantidade_atual if estoque else Decimal('0')
        try:
            quantidade_nova = _decimal_from_request(value)
        except Exception:
            return JsonResponse({'ok': False, 'error': 'Quantidade de estoque invalida.'}, status=400)
        if quantidade_nova == quantidade_atual:
            return JsonResponse({
                'ok': True,
                'display': _format_quantidade_produto(quantidade_atual, produto),
                'margem': f'{produto.margem_atual:.2f}'.replace('.', ','),
                'markup': f'{produto.markup_atual:.2f}'.replace('.', ','),
                'preco_custo': f'R$ {produto.preco_custo:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.'),
                'preco_venda': f'R$ {produto.preco_atual:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.'),
            })
        try:
            MovimentacaoService.ajustar_manual(
                produto_id=produto.pk,
                filial_id=request.filial_ativa.pk,
                quantidade_nova=quantidade_nova,
                usuario_id=request.user.pk,
                justificativa='Edicao rapida de estoque na lista de produtos.',
            )
        except Exception as exc:
            return JsonResponse({'ok': False, 'error': str(exc) or 'Nao foi possivel ajustar o estoque.'}, status=400)
        _registrar_produto_log(
            request,
            produto,
            'Ajuste de estoque',
            'Edicao rapida de estoque na lista de produtos.',
            changes=[{
                'campo': 'Estoque',
                'antes': _format_quantidade_produto(quantidade_atual, produto),
                'depois': _format_quantidade_produto(quantidade_nova, produto),
            }],
        )
        return JsonResponse({
            'ok': True,
            'display': _format_quantidade_produto(quantidade_nova, produto),
            'margem': f'{produto.margem_atual:.2f}'.replace('.', ','),
            'markup': f'{produto.markup_atual:.2f}'.replace('.', ','),
            'preco_custo': f'R$ {produto.preco_custo:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.'),
            'preco_venda': f'R$ {produto.preco_atual:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.'),
        })

    def _display(self, produto, field):
        if field == 'categoria':
            return produto.categoria.nome if produto.categoria else '-'
        if field == 'subcategoria':
            return produto.subcategoria.nome if produto.subcategoria else '-'
        if field == 'unidade_medida':
            return str(produto.unidade_medida)
        if field == 'classe_fiscal':
            return produto.classe_fiscal.codigo if produto.classe_fiscal else '-'
        if field == 'tipo_produto':
            return produto.get_tipo_produto_display()
        if field == 'origem_produto':
            return produto.get_origem_produto_display()
        if field in self.CAMPOS_DECIMAIS_PERCENTUAIS:
            valor = getattr(produto, field)
            return f'{valor:,.2f}%'.replace(',', 'X').replace('.', ',').replace('X', '.')
        if field in self.CAMPOS_DECIMAIS:
            valor = getattr(produto, field)
            return f'R$ {valor:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
        return getattr(produto, field) or '-'


class ProdutoImagemUpdateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'produtos'
    permissao_acao = 'editar'

    def post(self, request, pk):
        produto = get_object_or_404(
            _produtos_filial_qs(request, incluir_inativos=True), pk=pk,
        )
        imagem = request.FILES.get('imagem_produto')
        remover = request.POST.get('remover_imagem') == '1'
        destino = request.META.get('HTTP_REFERER', 'produtos:produto-list')
        if remover:
            produto.foto_url = ''
            produto.save(update_fields=['foto_url', 'updated_at'])
            _sincronizar_produto_sem_quebrar(request, produto)
            _registrar_produto_log(
                request,
                produto,
                'Produto editado',
                'Imagem do produto removida.',
                changes=[{'campo': 'Imagem do produto', 'antes': 'Com imagem', 'depois': 'Sem imagem'}],
            )
            messages.success(request, 'Imagem do produto removida.')
            return redirect(destino)
        if not imagem:
            messages.warning(request, 'Selecione uma imagem para atualizar o produto.')
            return redirect(destino)
        if getattr(imagem, 'size', 0) > LIMITE_IMAGEM_PRODUTO_BYTES:
            messages.error(request, f'A imagem deve ter ate {LIMITE_IMAGEM_PRODUTO_MB} MB.')
            return redirect(destino)
        content_type = getattr(imagem, 'content_type', '')
        if content_type and content_type not in {'image/jpeg', 'image/png', 'image/webp', 'image/gif'}:
            messages.error(request, 'Use uma imagem PNG, JPG, WEBP ou GIF.')
            return redirect(destino)
        antes = 'Com imagem' if produto.foto_url else 'Sem imagem'
        _gravar_imagem_produto(produto, imagem)
        produto.save(update_fields=['foto_url', 'updated_at'])
        _sincronizar_produto_sem_quebrar(request, produto)
        _registrar_produto_log(
            request,
            produto,
            'Produto editado',
            'Imagem do produto atualizada.',
            changes=[{'campo': 'Imagem do produto', 'antes': antes, 'depois': 'Com imagem'}],
        )
        messages.success(request, 'Imagem do produto atualizada.')
        return redirect(destino)


class ProdutoImagemView(View):
    """Entrega variantes leves da foto pelo mesmo dominio da aplicacao."""

    variantes = {
        'thumb': (320, 76),
        'zoom': (1400, 86),
    }

    @classmethod
    def _nome_derivada(cls, original, variante):
        base = original.rsplit('.', 1)[0]
        limite, _ = cls.variantes[variante]
        return f'{base}__{limite}px.jpg'

    @classmethod
    def _converter(cls, arquivo, variante):
        limite, qualidade = cls.variantes[variante]
        with Image.open(arquivo) as original:
            imagem = ImageOps.exif_transpose(original)
            imagem.thumbnail((limite, limite), Image.Resampling.LANCZOS)
            if imagem.mode in {'RGBA', 'LA'} or (
                imagem.mode == 'P' and 'transparency' in imagem.info
            ):
                rgba = imagem.convert('RGBA')
                fundo = Image.new('RGB', rgba.size, 'white')
                fundo.paste(rgba, mask=rgba.getchannel('A'))
                imagem = fundo
            elif imagem.mode != 'RGB':
                imagem = imagem.convert('RGB')
            saida = io.BytesIO()
            imagem.save(saida, format='JPEG', quality=qualidade, optimize=True)
            saida.seek(0)
            return saida

    @classmethod
    def _arquivo_otimizado(cls, produto, variante):
        nome_original = produto.foto_storage_name
        if nome_original:
            nome_derivada = cls._nome_derivada(nome_original, variante)
            if not default_storage.exists(nome_derivada):
                with default_storage.open(nome_original, 'rb') as original:
                    convertido = cls._converter(original, variante)
                nome_derivada = default_storage.save(
                    nome_derivada,
                    ContentFile(convertido.getvalue()),
                )
            return default_storage.open(nome_derivada, 'rb')

        destino = produto.foto_url_resolvida
        if not destino:
            raise Http404('Produto sem imagem cadastrada.')
        with urlopen(destino, timeout=20) as resposta:
            return cls._converter(io.BytesIO(resposta.read()), variante)

    def get(self, request, pk):
        produto = get_object_or_404(Produto.objects.all(), pk=pk)
        variante = 'thumb' if request.GET.get('v') == 'thumb' else 'zoom'
        arquivo = self._arquivo_otimizado(produto, variante)
        response = FileResponse(arquivo, content_type='image/jpeg')
        response['Cache-Control'] = 'private, no-store, max-age=0'
        response['Pragma'] = 'no-cache'
        response['Content-Disposition'] = 'inline; filename="produto.jpg"'
        return response


class ProdutoLogExportCsvView(PermissaoRequiredMixin, View):
    permissao_modulo = 'produtos'
    permissao_acao = 'ver'

    def get(self, request, pk):
        if not _usuario_pode_exportar(request):
            messages.error(request, 'Apenas administradores podem exportar produtos.')
            return redirect('produtos:produto-update', pk=pk)
        produto = get_object_or_404(
            _produtos_filial_qs(request, incluir_inativos=True), pk=pk,
        )
        return _produto_log_csv_response(produto, request)


class ProdutoLogExportPdfView(PermissaoRequiredMixin, View):
    permissao_modulo = 'produtos'
    permissao_acao = 'ver'

    def get(self, request, pk):
        if not _usuario_pode_exportar(request):
            messages.error(request, 'Apenas administradores podem exportar produtos.')
            return redirect('produtos:produto-update', pk=pk)
        produto = get_object_or_404(
            _produtos_filial_qs(request, incluir_inativos=True), pk=pk,
        )
        return _produto_log_pdf_response(produto, request)


class ProdutoLogItemsView(PermissaoRequiredMixin, View):
    permissao_modulo = 'produtos'
    permissao_acao = 'ver'

    def get(self, request, pk):
        produto = get_object_or_404(
            _produtos_filial_qs(request, incluir_inativos=True), pk=pk,
        )
        offset = max(int(request.GET.get('offset', 0) or 0), 0)
        limit = min(max(int(request.GET.get('limit', 50) or 50), 1), 50)
        logs = _produto_log_entries(
            produto,
            usuario_padrao=request.user,
            limit=limit,
            offset=offset,
        )
        total = _produto_log_total_count(produto)
        return JsonResponse({
            'html': ''.join(
                render_to_string(
                    'produtos/produto/partials/_log_item.html',
                    {'item': item},
                    request=request,
                )
                for item in logs
            ),
            'next_offset': offset + len(logs) if logs else total,
            'total': total,
        })


class ProdutoExportCsvView(PermissaoRequiredMixin, View):
    permissao_modulo = 'produtos'
    permissao_acao = 'ver'

    def get(self, request):
        if not _usuario_pode_exportar(request):
            messages.error(request, 'Apenas administradores podem exportar produtos.')
            return redirect('produtos:produto-list')
        return _produto_csv_response(
            _produto_queryset_filtrado(request),
            'produtos_filtrados.csv',
            empresa=request.user.empresa,
        )


class ProdutoExportTodosCsvView(PermissaoRequiredMixin, View):
    permissao_modulo = 'produtos'
    permissao_acao = 'ver'

    def get(self, request):
        if not _usuario_pode_exportar(request):
            messages.error(request, 'Apenas administradores podem exportar produtos.')
            return redirect('produtos:produto-list')
        return _produto_csv_response(
            _produto_queryset_filtrado(request, incluir_inativos_por_padrao=True),
            'produtos_todos.csv',
            completo=True,
            empresa=request.user.empresa,
        )


class ProdutoExportPdfView(PermissaoRequiredMixin, View):
    permissao_modulo = 'produtos'
    permissao_acao = 'ver'

    def get(self, request):
        if not _usuario_pode_exportar(request):
            messages.error(request, 'Apenas administradores podem exportar produtos.')
            return redirect('produtos:produto-list')
        return _produto_pdf_response(_produto_queryset_filtrado(request), request.user.empresa)


# Entradas em que o vínculo do item ainda pode ser desfeito. É O MESMO
# CONJUNTO que estava escrito à mão dentro do método — trazido para cá com
# nome, para a intenção ficar dita uma vez.
#
# São os valores literais de `EntradaNF.Status`, e não o enum: `produtos` não
# importa `compras` em tempo de módulo (o próprio método faz o import tarde,
# de propósito), e criar essa dependência por causa de três strings seria
# pagar caro por pouco.
#
# Entrada EFETIVADA fica de fora de propósito: o item já virou estoque e
# movimentação, e soltá-lo deixaria um lançamento sem produto.
ENTRADAS_ABERTAS = (
    'rascunho', 'aguardando_conferencia', 'aguardando_vinculos',
)


class ProdutoFornecedorVinculoDeleteView(PermissaoRequiredMixin, View):
    """Desativa (soft-delete) um vínculo de fornecedor de um produto."""
    permissao_modulo = 'produtos'
    permissao_acao = 'editar'

    def post(self, request, pk, vinculo_pk):
        from apps.produtos.models import ProdutoFornecedorEquivalencia
        # `filial__empresa`, e não `empresa`: `Produto` não tem esse campo — é
        # `FilialScopedModel`, e a empresa vem pela filial. E a empresa sai do
        # USUÁRIO: `request.empresa` não é preenchido por middleware nenhum.
        #
        # Com os dois errados, esta linha levantava `FieldError` ANTES de
        # qualquer outra coisa: o botão "remover vínculo" do formulário do
        # produto devolvia 500, sempre.
        produto = get_object_or_404(
            Produto, pk=pk, filial__empresa=request.user.empresa,
        )
        vinculo = get_object_or_404(ProdutoFornecedorEquivalencia, pk=vinculo_pk, produto=produto)

        vinculo.ativo = False
        vinculo.save(update_fields=['ativo', 'updated_at'])

        # Libera itens de entrada abertos que ainda estão vinculados a este
        # produto via este vínculo.
        #
        # O FORNECEDOR ESTÁ NA ENTRADA, não no item. Este filtro pedia
        # `ItemEntradaNF.fornecedor_cnpj`, campo que não existe: levantava
        # `FieldError` em toda execução, e o `except: pass` engolia. Resultado:
        # desativar o vínculo nunca liberava item nenhum, e ninguém ficava
        # sabendo.
        #
        # O PAR CNPJ↔CNPJ É EXATO porque as duas pontas vêm da mesma origem:
        # `vinculo.fornecedor_cnpj_xml` é gravado A PARTIR de
        # `entrada.emitente_cnpj_xml`. Não há formato a normalizar. É o mesmo
        # pareamento que `entrada_produto_service` já faz no sentido inverso,
        # inclusive somando a chave estrangeira quando ela existe.
        self._liberar_itens_de_entrada(produto, vinculo)

        LogSistema.objects.create(
            filial=request.filial_ativa,
            usuario=request.user,
            modulo='produtos',
            acao='editar',
            registro_id=produto.pk,
            dados_novos={'evento': 'Vinculo de fornecedor removido', 'vinculo_pk': vinculo.pk},
        )

        messages.success(request, 'Vínculo de fornecedor removido.')
        return redirect('produtos:produto-update', pk=produto.pk)

    @staticmethod
    def _liberar_itens_de_entrada(produto, vinculo) -> int:
        """
        Solta o produto dos itens de entrada AINDA ABERTOS deste fornecedor.

        SÓ ENTRADA ABERTA. Item de entrada efetivada já virou estoque e
        movimentação: soltá-lo deixaria um lançamento sem produto, e o custo
        daquela compra pararia de bater com o que entrou.

        SEM IDENTIFICAR O FORNECEDOR, NÃO MEXE. Vínculo sem CNPJ e sem chave
        estrangeira não diz de quem é; um filtro só por produto soltaria itens
        de TODOS os fornecedores, que é o oposto de "via este vínculo".

        UM A UM, PELO SERVIÇO, e não num `.update()` de queryset. Desvincular
        não é só zerar o produto: `desvincular_item_de_produto` carimba o item
        com o marcador que IMPEDE O REVÍNCULO AUTOMÁTICO, limpa as marcas de
        divergência e recalcula a conferência. Sem o carimbo, o próximo
        reprocessamento reataria o item ao mesmo produto — e a desativação do
        vínculo seria desfeita sozinha.

        São itens de entradas abertas de um produto e um fornecedor: um punhado
        de linhas. A consulta única sairia mais barata e faria a coisa errada.

        NÃO DERRUBA A DESATIVAÇÃO. O vínculo já foi desativado quando isto
        roda; um erro aqui não pode desfazer aquilo. Mas VAI PARA O LOG -- foi
        um `except: pass` mudo que escondeu o defeito anterior por tempo
        indeterminado.
        """
        import logging

        from django.db.models import Q

        from apps.compras.models import ItemEntradaNF
        from apps.compras.services.compra_service import CompraService
        from apps.compras.services.entrada_produto_service import (
            desvincular_item_de_produto,
        )

        por_fornecedor = Q()
        if vinculo.fornecedor_cnpj_xml:
            por_fornecedor |= Q(
                entrada__emitente_cnpj_xml=vinculo.fornecedor_cnpj_xml,
            )
        if vinculo.fornecedor_id:
            por_fornecedor |= Q(entrada__fornecedor_id=vinculo.fornecedor_id)
        if not por_fornecedor:
            return 0

        try:
            itens = list(
                ItemEntradaNF.objects.filter(
                    por_fornecedor,
                    produto=produto,
                    entrada__status__in=ENTRADAS_ABERTAS,
                ).select_related('entrada')
            )
            for item in itens:
                desvincular_item_de_produto(item)
            # A entrada nao pode seguir dizendo que esta pronta para conferir
            # com item sem produto. `desvincular_item_de_produto` e' de escopo
            # de ITEM e nao recalcula isso; quem tem a visao da entrada e' este
            # laco. Uma vez por entrada, e nao por item: sao N gravacoes.
            for entrada in {item.entrada for item in itens}:
                CompraService._atualizar_status_conferencia(entrada)
            return len(itens)
        except Exception:  # noqa: BLE001 — não derruba a desativação
            logging.getLogger(__name__).exception(
                'Falha ao liberar itens de entrada do vínculo %s', vinculo.pk,
            )
            return 0
