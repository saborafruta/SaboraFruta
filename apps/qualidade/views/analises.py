import json

from django.contrib import messages
from django.db import IntegrityError, connection
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.core.tenant_context import tenant_atomic
from apps.core.services.permissions import requer_permissao
from apps.produtos.models import CategoriaProduto, Produto
from apps.produtos.services.replicacao_service import ReplicacaoProdutoService
from apps.qualidade.forms import ParametroQualidadeCategoriaForm, ParametroQualidadeProdutoForm
from apps.qualidade.models import ParametroQualidadeCategoria, ParametroQualidadeProduto


def _tabela_parametros_disponivel():
    return "parametros_qualidade_produtos" in connection.introspection.table_names()


def _tabela_padroes_disponivel():
    return "parametros_qualidade_categorias" in connection.introspection.table_names()


def _produtos_da_filial(request):
    filial = getattr(request, "filial_ativa", None)
    if not filial:
        return Produto.objects.none()
    return (
        Produto.objects.for_filial(filial)
        .filter(ativo=True)
        .select_related("unidade_medida")
        .order_by("descricao", "codigo")
    )


def _categorias_da_filial(request):
    filial = getattr(request, "filial_ativa", None)
    if not filial:
        return CategoriaProduto.objects.none()
    return (
        CategoriaProduto.objects.for_filial(filial)
        .filter(empresa=filial.empresa, ativo=True)
        .select_related("categoria_pai")
        .order_by("categoria_pai__nome", "nome")
    )


def _subcategorias_por_categoria(categorias):
    grupos = {}
    for categoria in categorias:
        if not categoria.categoria_pai_id:
            continue
        grupos.setdefault(str(categoria.categoria_pai_id), []).append(
            {"id": categoria.pk, "nome": categoria.nome}
        )
    return grupos


def _produto_selecionado(request, produtos):
    produto_id = request.GET.get("produto_id", "").strip()
    if not produto_id.isdigit():
        return None
    return produtos.filter(pk=int(produto_id)).first()


def _redirect_produto(produto_id=None):
    url = reverse("qualidade:analise_list")
    if produto_id:
        url = f"{url}?produto_id={produto_id}"
    return redirect(url)


def _categorias_padrao_produto(produto):
    if not produto:
        return []
    categorias = []
    if produto.subcategoria_id:
        categorias.append(produto.subcategoria_id)
    if produto.categoria_id and produto.categoria_id not in categorias:
        categorias.append(produto.categoria_id)
    return categorias


def _padroes_para_produto(request, produto):
    if not produto or not _tabela_padroes_disponivel():
        return ParametroQualidadeCategoria.objects.none()
    categorias = _categorias_padrao_produto(produto)
    if not categorias:
        return ParametroQualidadeCategoria.objects.none()
    return (
        ParametroQualidadeCategoria.objects.for_filial(request.filial_ativa)
        .filter(categoria_id__in=categorias)
        .select_related("categoria")
        .order_by("categoria__categoria_pai_id", "categoria__nome", "etapa", "nome_parametro")
    )


def _padroes_priorizados_para_produto(request, produto):
    """Retorna subcategoria antes da categoria e remove duplicidades por etapa/nome."""
    if not produto or not _tabela_padroes_disponivel():
        return []
    categorias = _categorias_padrao_produto(produto)
    selecionados = []
    vistos = set()
    for categoria_id in categorias:
        padroes = (
            ParametroQualidadeCategoria.objects.for_filial(request.filial_ativa)
            .filter(categoria_id=categoria_id, ativo=True)
            .order_by("etapa", "nome_parametro")
        )
        for padrao in padroes:
            chave = (padrao.etapa, padrao.nome_parametro.strip().lower())
            if chave in vistos:
                continue
            vistos.add(chave)
            selecionados.append(padrao)
    return selecionados


def _render_analises(request, form=None, padrao_form=None, produto=None):
    tabela_disponivel = _tabela_parametros_disponivel()
    tabela_padroes_disponivel = _tabela_padroes_disponivel()
    produtos = _produtos_da_filial(request)
    categorias = _categorias_da_filial(request)
    produto = produto or _produto_selecionado(request, produtos)
    parametros = ParametroQualidadeProduto.objects.none()
    padroes_produto = ParametroQualidadeCategoria.objects.none()
    todos_padroes = ParametroQualidadeCategoria.objects.none()

    if tabela_disponivel and produto:
        parametros = (
            ParametroQualidadeProduto.objects.for_filial(request.filial_ativa)
            .filter(produto=produto)
            .order_by("etapa", "nome_parametro")
        )
    if tabela_padroes_disponivel:
        todos_padroes = (
            ParametroQualidadeCategoria.objects.for_filial(request.filial_ativa)
            .select_related("categoria", "categoria__categoria_pai")
            .order_by("categoria__categoria_pai__nome", "categoria__nome", "etapa", "nome_parametro")
        )
        padroes_produto = _padroes_para_produto(request, produto)

    return render(
        request,
        "qualidade/analises_list.html",
        {
            "form": form or ParametroQualidadeProdutoForm(prefix="produto"),
            "padrao_form": padrao_form or ParametroQualidadeCategoriaForm(
                categorias=categorias,
                prefix="padrao",
            ),
            "parametros": parametros,
            "padroes_produto": padroes_produto,
            "todos_padroes": todos_padroes,
            "produto": produto,
            "produtos": produtos,
            "categorias": categorias,
            "categorias_base": categorias.filter(categoria_pai__isnull=True),
            "subcategorias_por_categoria_json": json.dumps(_subcategorias_por_categoria(categorias)),
            "etapas_qualidade": ParametroQualidadeCategoria._meta.get_field("etapa").choices,
            "tipos_valor_qualidade": ParametroQualidadeCategoria.TipoValor.choices,
            "tabela_disponivel": tabela_disponivel,
            "tabela_padroes_disponivel": tabela_padroes_disponivel,
            "voltar_url": reverse("produtos:produto-list"),
        },
    )


@requer_permissao("qualidade", "ver")
def analise_list(request):
    return _render_analises(request)


@requer_permissao("qualidade", "ver")
def produto_search(request):
    termo = request.GET.get("q", "").strip()
    produtos = _produtos_da_filial(request)
    if len(termo) >= 2 or termo.isdigit():
        filtro = (
            Q(descricao__icontains=termo)
            | Q(codigo__icontains=termo)
            | Q(codigo_barras__icontains=termo)
            | Q(descricao_curta__icontains=termo)
        )
        if termo.isdigit():
            filtro |= Q(pk=int(termo))
        produtos = produtos.filter(filtro)
    else:
        produtos = produtos.none()
    resultados = []
    for produto in produtos.select_related("categoria", "subcategoria")[:20]:
        categoria = produto.subcategoria or produto.categoria
        resultados.append(
            {
                "id": produto.pk,
                "codigo": "",
                "referencia": produto.codigo or "",
                "codigo_barras": produto.codigo_barras or "",
                "descricao": produto.descricao,
                "categoria": categoria.nome if categoria else "",
                "label": f"{produto.codigo + ' - ' if produto.codigo else ''}{produto.descricao}",
            }
        )
    return JsonResponse({"produtos": resultados})


@require_POST
@requer_permissao("qualidade", "criar")
def parametro_create(request):
    if not _tabela_parametros_disponivel():
        messages.error(request, "A tabela de qualidade ainda nao esta disponivel neste deploy.")
        return _redirect_produto(request.GET.get("produto_id"))

    produto_id = request.GET.get("produto_id", "").strip()
    produto = get_object_or_404(_produtos_da_filial(request), pk=produto_id)
    form = ParametroQualidadeProdutoForm(request.POST, prefix="produto")

    if form.is_valid():
        try:
            with tenant_atomic():
                parametro = form.save(commit=False)
                parametro.filial = request.filial_ativa
                parametro.produto = produto
                parametro.save()
                ReplicacaoProdutoService.sincronizar_parametro_qualidade_produto(parametro)
            messages.success(request, f'Parametro "{parametro.nome_parametro}" adicionado.')
            return _redirect_produto(produto.pk)
        except IntegrityError:
            form.add_error(None, "Ja existe um parametro com este nome e etapa para o produto.")

    return _render_analises(request, form=form, produto=produto)


@require_POST
@requer_permissao("qualidade", "criar")
def padrao_create(request):
    if not _tabela_padroes_disponivel():
        messages.error(request, "A tabela de padroes de qualidade ainda nao esta disponivel neste deploy.")
        return _redirect_produto(request.GET.get("produto_id"))

    categorias = _categorias_da_filial(request)
    form = ParametroQualidadeCategoriaForm(
        request.POST,
        categorias=categorias,
        prefix="padrao",
    )
    produto = _produto_selecionado(request, _produtos_da_filial(request))

    if form.is_valid():
        try:
            with tenant_atomic():
                padrao = form.save(commit=False)
                padrao.filial = request.filial_ativa
                padrao.save()
                ReplicacaoProdutoService.sincronizar_parametro_qualidade_categoria(padrao)
            messages.success(request, f'Padrao "{padrao.nome_parametro}" adicionado.')
            return _redirect_produto(request.GET.get("produto_id"))
        except IntegrityError:
            form.add_error(None, "Ja existe um padrao com este nome e etapa para a categoria.")

    return _render_analises(request, padrao_form=form, produto=produto)


@require_POST
@requer_permissao("qualidade", "editar")
def padrao_toggle(request, pk):
    if not _tabela_padroes_disponivel():
        messages.error(request, "A tabela de padroes de qualidade ainda nao esta disponivel neste deploy.")
        return _redirect_produto(request.GET.get("produto_id"))

    padrao = get_object_or_404(
        ParametroQualidadeCategoria.objects.for_filial(request.filial_ativa),
        pk=pk,
    )
    padrao.ativo = not padrao.ativo
    padrao.save(update_fields=["ativo", "updated_at"])
    ReplicacaoProdutoService.sincronizar_parametro_qualidade_categoria(padrao)
    status = "ativado" if padrao.ativo else "inativado"
    messages.success(request, f'Padrao "{padrao.nome_parametro}" {status}.')
    return _redirect_produto(request.GET.get("produto_id"))


@require_POST
@requer_permissao("qualidade", "editar")
def padrao_update(request, pk):
    if not _tabela_padroes_disponivel():
        messages.error(request, "A tabela de padroes de qualidade ainda nao esta disponivel neste deploy.")
        return _redirect_produto(request.GET.get("produto_id"))

    padrao = get_object_or_404(
        ParametroQualidadeCategoria.objects.for_filial(request.filial_ativa),
        pk=pk,
    )
    form = ParametroQualidadeCategoriaForm(
        request.POST,
        instance=padrao,
        categorias=_categorias_da_filial(request),
    )
    if form.is_valid():
        try:
            with tenant_atomic():
                padrao = form.save()
                ReplicacaoProdutoService.sincronizar_parametro_qualidade_categoria(padrao)
            messages.success(request, f'Padrao "{padrao.nome_parametro}" atualizado.')
            return _redirect_produto(request.GET.get("produto_id"))
        except IntegrityError:
            form.add_error(None, "Ja existe um padrao com este nome e etapa para a categoria.")

    produto = _produto_selecionado(request, _produtos_da_filial(request))
    return _render_analises(request, padrao_form=form, produto=produto)


@require_POST
@requer_permissao("qualidade", "criar")
def aplicar_padroes_produto(request):
    if not _tabela_parametros_disponivel() or not _tabela_padroes_disponivel():
        messages.error(request, "A estrutura de qualidade ainda nao esta disponivel neste deploy.")
        return _redirect_produto(request.GET.get("produto_id"))

    produto_id = request.GET.get("produto_id", "").strip()
    produto = get_object_or_404(_produtos_da_filial(request), pk=produto_id)
    padroes = _padroes_priorizados_para_produto(request, produto)
    criados = 0

    with tenant_atomic():
        for padrao in padroes:
            dados = {
                "tipo_valor": padrao.tipo_valor,
                "unidade_medida": padrao.unidade_medida,
                "valor_minimo": padrao.valor_minimo,
                "valor_maximo": padrao.valor_maximo,
                "valor_ideal": padrao.valor_ideal,
                "valor_texto_ideal": padrao.valor_texto_ideal,
                "opcoes": padrao.opcoes,
                "obrigatorio": padrao.obrigatorio,
                "ativo": padrao.ativo,
            }
            parametro, created = ParametroQualidadeProduto.objects.get_or_create(
                filial=request.filial_ativa,
                produto=produto,
                etapa=padrao.etapa,
                nome_parametro=padrao.nome_parametro,
                defaults=dados,
            )
            if created:
                criados += 1
                ReplicacaoProdutoService.sincronizar_parametro_qualidade_produto(parametro)

    if criados:
        messages.success(request, f"{criados} parametro(s) aplicado(s) ao produto.")
    else:
        messages.info(request, "Nenhum parametro novo aplicado; o produto ja tinha estes padroes.")
    return _redirect_produto(produto.pk)


@require_POST
@requer_permissao("qualidade", "editar")
def parametro_toggle(request, pk):
    if not _tabela_parametros_disponivel():
        messages.error(request, "A tabela de qualidade ainda nao esta disponivel neste deploy.")
        return _redirect_produto()

    parametro = get_object_or_404(
        ParametroQualidadeProduto.objects.for_filial(request.filial_ativa),
        pk=pk,
    )
    parametro.ativo = not parametro.ativo
    parametro.save(update_fields=["ativo", "updated_at"])
    ReplicacaoProdutoService.sincronizar_parametro_qualidade_produto(parametro)
    status = "ativado" if parametro.ativo else "inativado"
    messages.success(request, f'Parametro "{parametro.nome_parametro}" {status}.')
    return _redirect_produto(parametro.produto_id)
