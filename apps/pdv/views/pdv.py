import datetime
import json
from decimal import Decimal

from django.db import transaction
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import render
from django.core.paginator import Paginator
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from apps.core.services.exceptions import DadosInvalidosError, EstoqueInsuficienteError
from apps.core.services.permissions import requer_permissao
from apps.financeiro.models import FormaPagamento, TaxaParcelamento
from apps.financeiro.constants.enums import TipoFormaPagamento
from apps.fiscal.integrations.focusnfe.exceptions import FocusNFeNetworkError, FocusNFeServerError
from apps.pdv.models import (
    Caixa, ItemVendaPDV, MovimentacaoCaixa, PagamentoVendaPDV, SessaoPDV, VendaPDV,
)
from apps.pdv.services.produto_vendavel_service import ProdutoVendavelService
from apps.pdv.services.venda_pdv_service import VendaPDVService
from apps.pdv.services.cancelamento_fiscal_service import (
    cancelar_venda_e_documento,
    obter_documento_fiscal,
)
from apps.pdv.services.fiscal_readiness_service import verificar_prontidao_fiscal
from apps.produtos.models import LinhaProducao, Produto


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _sessao_aberta(request):
    return SessaoPDV.objects.for_filial(request.filial_ativa).filter(
        usuario=request.user, status="aberto"
    ).first()


def _proximo_numero_venda(filial):
    ultimo_num = (
        VendaPDV.objects.filter(filial=filial)
        .order_by("-numero_venda")
        .values_list("numero_venda", flat=True)
        .first()
    )
    return (ultimo_num or 0) + 1


def _informacoes_adicionais_request(request):
    try:
        body = json.loads(request.body or b'{}')
    except (json.JSONDecodeError, TypeError):
        body = {}
    texto = str(body.get("informacoes_adicionais") or "").strip()
    if len(texto) > 5000:
        raise DadosInvalidosError("As informacoes adicionais podem ter no maximo 5.000 caracteres.")
    return texto


# ---------------------------------------------------------------------------
# Tela principal
# ---------------------------------------------------------------------------

@requer_permissao('pdv', 'ver')
def pdv_home(request):
    caixas = list(
        Caixa.objects.for_filial(request.filial_ativa)
        .filter(ativo=True)
        .values('id', 'numero', 'descricao')
    )
    linhas = list(
        LinhaProducao.objects.filter(ativo=True)
        .values('id', 'nome', 'icone', 'cor_identificacao')
    )
    return render(request, "pdv/home.html", {
        "title": "PDV",
        "caixas_json": json.dumps(caixas),
        "linhas_json": json.dumps(linhas),
    })


# ---------------------------------------------------------------------------
# Lista de vendas
# ---------------------------------------------------------------------------

@requer_permissao('pdv', 'ver')
def vendas_list(request):
    qs = VendaPDV.objects.for_filial(request.filial_ativa).select_related(
        "cliente", "sessao_pdv", "usuario"
    ).order_by("-data_venda")
    paginator = Paginator(qs, 25)
    page = paginator.get_page(request.GET.get("page", 1))
    return render(request, "pdv/vendas_list.html", {"title": "Vendas PDV", "page": page})


# ---------------------------------------------------------------------------
# API — Busca de produtos e clientes (existentes, mantidos)
# ---------------------------------------------------------------------------

@requer_permissao('pdv', 'ver')
def buscar_produto(request):
    from apps.produtos.services.preco_service import PrecoService
    from django.utils import timezone as tz

    q = request.GET.get("q", "").strip()
    linha_id = request.GET.get("linha")
    filial = request.filial_ativa
    qs = Produto.objects.for_filial(filial).filter(ativo=True)
    if q:
        filtro = Q(descricao__icontains=q) | Q(codigo__icontains=q) | Q(codigo_barras__icontains=q)
        if q.isdigit():
            filtro |= Q(pk=int(q))
        qs = qs.filter(filtro)
    if linha_id:
        qs = qs.filter(linha_producao_id=linha_id)
    produtos = list(qs.select_related("linha_producao")[:20])
    data = []
    hoje = tz.localdate()
    for p in produtos:
        contrato = ProdutoVendavelService.consultar(
            produto=p,
            filial=filial,
            quantidade=Decimal("1"),
        )
        # Coleta TODOS os preços candidatos para permitir escolha do vendedor
        todos_precos = _todos_precos_produto(p, filial, hoje)

        data.append({
            "id": p.id, "descricao": p.descricao_pdv or p.descricao,
            "codigo_barras": p.codigo_barras,
            "preco": float(contrato["preco_aplicado"]),
            "preco_base": float(p.preco_venda or 0),
            "preco_origem": contrato["preco_origem"],
            "preco_origem_tipo": contrato["preco_origem_tipo"],
            "preco_origem_detalhe": contrato["preco_origem_detalhe"],
            "estoque_disponivel": float(contrato["saldo_disponivel"]),
            "custo_atual": float(contrato["custo_atual"]),
            "margem_percentual": float(contrato["margem_percentual"]),
            "status_comercial": contrato["status_comercial"],
            "status_comercial_label": contrato["status_comercial_label"],
            "lote_obrigatorio": contrato["lote_obrigatorio"],
            "promocoes_aplicaveis": contrato["promocoes_aplicaveis"],
            "bloqueios": contrato["bloqueios"],
            "alertas": contrato["alertas"],
            "pode_vender": contrato["pode_vender"],
            "permite_venda_sem_estoque": p.permite_venda_sem_estoque,
            "linha": p.linha_producao.nome if p.linha_producao else None,
            "icone": p.linha_producao.icone if p.linha_producao else None,
            "cor": p.linha_producao.cor_identificacao if p.linha_producao else None,
            # Lista completa de preços: se len > 1, PDV mostra seletor ao vendedor
            "todos_precos": todos_precos,
        })
    return JsonResponse({"produtos": data})


def _todos_precos_produto(produto, filial, hoje=None):
    """Retorna lista de todos os preços candidatos vigentes para o produto."""
    from apps.produtos.services.preco_service import PrecoService
    from django.utils import timezone as tz
    hoje = hoje or tz.localdate()

    candidatos = []

    # 1) Preço normal de venda
    preco_normal = float(produto.preco_venda or 0)
    if preco_normal > 0:
        candidatos.append({
            'preco': preco_normal,
            'tipo': 'normal',
            'origem': 'Preço de venda',
            'detalhe': 'Preço padrão cadastrado no produto.',
        })

    # 2) Promoção individual
    try:
        preco_promo = PrecoService.preco_promocional_vigente(produto, filial=filial, data=hoje)
        if preco_promo is not None:
            candidatos.append({
                'preco': float(preco_promo),
                'tipo': 'promocional',
                'origem': 'Promoção individual',
                'detalhe': 'Promoção ativa neste produto.',
            })
    except Exception:
        pass

    # 3) Descontos por categoria
    try:
        for c in PrecoService.precos_categoria_vigentes_detalhados(produto, filial=filial, data=hoje):
            candidatos.append({
                'preco': float(c['preco']),
                'tipo': c.get('tipo', 'categoria'),
                'origem': c.get('origem', 'Desconto por categoria'),
                'detalhe': c.get('detalhe', ''),
            })
    except Exception:
        pass

    # 4) Combos por quantidade
    try:
        for c in PrecoService.precos_combo_quantidade_vigentes_detalhados(produto, filial=filial, data=hoje):
            candidatos.append({
                'preco': float(c['preco']),
                'tipo': c.get('tipo', 'combo'),
                'origem': c.get('origem', 'Combo por quantidade'),
                'detalhe': c.get('detalhe', ''),
            })
    except Exception:
        pass

    # Remove duplicatas (mesmo preço) e ordena do menor ao maior
    vistos = set()
    unicos = []
    for item in candidatos:
        chave = round(item['preco'], 4)
        if chave not in vistos and item['preco'] > 0:
            vistos.add(chave)
            unicos.append(item)
    unicos.sort(key=lambda x: x['preco'])

    # Marca o menor como recomendado
    if unicos:
        menor = unicos[0]['preco']
        for item in unicos:
            item['melhor'] = abs(item['preco'] - menor) < 0.0001

    return unicos


@requer_permissao('pdv', 'ver')
def buscar_cliente(request):
    from apps.cadastros.models import Cliente
    from django.db.models import Q as DQ

    q = request.GET.get("q", "").strip()
    filial = request.filial_ativa

    def _serializar(c):
        endereco_entrega = {
            "rua": c.endereco or "",
            "numero": c.numero or "",
            "bairro": c.bairro or "",
            "complemento": c.complemento or "",
            "cidade": c.cidade or "",
            "uf": c.uf or "",
            "cep": c.cep or "",
        }
        try:
            endereco_extra = c.enderecos.filter(ativo=True).order_by("-padrao", "id").first()
            if endereco_extra:
                endereco_entrega = {
                    "rua": endereco_extra.endereco or endereco_entrega["rua"],
                    "numero": endereco_extra.numero or endereco_entrega["numero"],
                    "bairro": endereco_extra.bairro or endereco_entrega["bairro"],
                    "complemento": endereco_extra.complemento or endereco_entrega["complemento"],
                    "cidade": endereco_extra.cidade or endereco_entrega["cidade"],
                    "uf": endereco_extra.uf or endereco_entrega["uf"],
                    "cep": endereco_extra.cep or endereco_entrega["cep"],
                }
        except Exception:
            pass

        return {
            "id": c.id,
            "razao_social": c.razao_social,
            "nome_fantasia": c.nome_fantasia or "",
            "cpf_cnpj": c.cpf_cnpj or "",
            "celular": c.celular or "",
            "telefone": c.telefone or "",
            "endereco_entrega": endereco_entrega,
            "tem_endereco": bool(endereco_entrega.get("rua") and endereco_entrega.get("bairro")),
            "linhas_interesse": getattr(c, 'linhas_interesse', ''),
            "saldo_devedor": float(c.saldo_devedor or 0),
        }

    def _aplicar_busca(qs, q):
        if len(q) >= 2:
            return qs.filter(
                DQ(razao_social__icontains=q)
                | DQ(nome_fantasia__icontains=q)
                | DQ(cpf_cnpj__icontains=q)
                | DQ(celular__icontains=q)
                | DQ(telefone__icontains=q)
            )
        return qs

    base_qs = Cliente.objects.filter(ativo=True)

    # ── Tentativa 1: escopo da empresa via FK direta ──────────────────────────
    empresa_id = getattr(filial, 'empresa_id', None) if filial else None
    if empresa_id:
        qs = _aplicar_busca(
            base_qs.filter(filial__empresa_id=empresa_id).distinct(),
            q
        ).order_by('razao_social')[:30]
        resultados = list(qs)
        if resultados:
            return JsonResponse({"clientes": [_serializar(c) for c in resultados]})

    # ── Tentativa 2: escopo da filial via FK direta ───────────────────────────
    if filial:
        qs = _aplicar_busca(
            base_qs.filter(filial=filial).distinct(),
            q
        ).order_by('razao_social')[:30]
        resultados = list(qs)
        if resultados:
            return JsonResponse({"clientes": [_serializar(c) for c in resultados]})

    # ── Tentativa 3: ClienteFilial para qualquer filial da empresa ────────────
    if empresa_id:
        qs = _aplicar_busca(
            base_qs.filter(
                filiais_vinculo__filial__empresa_id=empresa_id,
                filiais_vinculo__ativo=True,
            ).distinct(),
            q
        ).order_by('razao_social')[:30]
        resultados = list(qs)
        if resultados:
            return JsonResponse({"clientes": [_serializar(c) for c in resultados]})

    # ── Fallback final: TODOS os clientes ativos do sistema ──────────────────
    # (sem filtro de filial — garante que clientes sempre apareçam)
    qs = _aplicar_busca(base_qs, q).order_by('razao_social')[:30]
    return JsonResponse({"clientes": [_serializar(c) for c in qs]})


@requer_permissao('pdv', 'ver')
def api_clientes_debug(request):
    """Diagnóstico: mostra informações da filial e contagem de clientes para depuração."""
    from apps.cadastros.models import Cliente, ClienteFilial
    filial = request.filial_ativa
    empresa_id = getattr(filial, 'empresa_id', None) if filial else None
    total_clientes = Cliente.objects.filter(ativo=True).count()
    clientes_filial_fk = Cliente.objects.filter(filial=filial, ativo=True).count() if filial else 0
    clientes_empresa = Cliente.objects.filter(filial__empresa_id=empresa_id, ativo=True).count() if empresa_id else 0
    clientes_vinculo = ClienteFilial.objects.filter(filial=filial, ativo=True).count() if filial else 0
    primeiros = list(Cliente.objects.filter(ativo=True).order_by('id').values('id', 'razao_social', 'filial_id')[:5])
    return JsonResponse({
        "filial_id": filial.pk if filial else None,
        "filial_nome": str(filial) if filial else None,
        "empresa_id": empresa_id,
        "total_clientes_sistema": total_clientes,
        "clientes_mesma_filial_fk": clientes_filial_fk,
        "clientes_mesma_empresa": clientes_empresa,
        "vinculos_clientefilial_filial": clientes_vinculo,
        "primeiros_clientes": primeiros,
    })


# ---------------------------------------------------------------------------
# API — Estado inicial do PDV
# ---------------------------------------------------------------------------

def _serializa_produto(p, filial):
    contrato = ProdutoVendavelService.consultar(
        produto=p,
        filial=filial,
        quantidade=Decimal("1"),
    )
    return {
        "id": p.id,
        "descricao": p.descricao_pdv or p.descricao,
        "codigo_barras": p.codigo_barras,
        "preco": float(contrato["preco_aplicado"]),
        "preco_base": float(p.preco_venda or 0),
        "preco_origem": contrato["preco_origem"],
        "preco_origem_tipo": contrato["preco_origem_tipo"],
        "preco_origem_detalhe": contrato["preco_origem_detalhe"],
        "estoque_disponivel": float(contrato["saldo_disponivel"]),
        "custo_atual": float(contrato["custo_atual"]),
        "margem_percentual": float(contrato["margem_percentual"]),
        "status_comercial": contrato["status_comercial"],
        "status_comercial_label": contrato["status_comercial_label"],
        "lote_obrigatorio": contrato["lote_obrigatorio"],
        "promocoes_aplicaveis": contrato["promocoes_aplicaveis"],
        "bloqueios": contrato["bloqueios"],
        "alertas": contrato["alertas"],
        "pode_vender": contrato["pode_vender"],
        "linha": p.linha_producao.nome if p.linha_producao else None,
        "icone": p.linha_producao.icone if p.linha_producao else None,
        "cor": p.linha_producao.cor_identificacao if p.linha_producao else None,
    }


@requer_permissao('pdv', 'ver')
@require_GET
def api_estado(request):
    sessao = _sessao_aberta(request)

    try:
        formas = list(
            FormaPagamento.objects.filter(
                empresa=request.filial_ativa.empresa, ativo=True
            ).values('id', 'descricao', 'tipo', 'requer_tef')
        )
    except Exception:
        formas = []

    # Top 10 — mais vendidos por quantidade em vendas finalizadas
    top_produtos = []
    try:
        ranking = (
            ItemVendaPDV.objects
            .filter(venda_pdv__filial=request.filial_ativa,
                    venda_pdv__status="finalizada")
            .values('produto_id')
            .annotate(qtd=Sum('quantidade'))
            .order_by('-qtd')[:10]
        )
        ids_ordenados = [r['produto_id'] for r in ranking]
        if ids_ordenados:
            produtos = {
                p.id: p for p in Produto.objects.filter(id__in=ids_ordenados)
                .select_related('linha_producao')
            }
            for pid in ids_ordenados:
                p = produtos.get(pid)
                if p and p.ativo:
                    top_produtos.append(_serializa_produto(p, request.filial_ativa))

        # Sem histórico de vendas: mostra produtos cadastrados
        if not top_produtos:
            fallback = (
                Produto.objects.for_filial(request.filial_ativa)
                .filter(ativo=True)
                .select_related('linha_producao')
                .order_by('descricao')[:10]
            )
            top_produtos = [_serializa_produto(p, request.filial_ativa) for p in fallback]
    except Exception:
        top_produtos = []

    return JsonResponse({
        "sessao": {
            "id": sessao.id,
            "caixa_id": sessao.caixa_id,
            "data_abertura": sessao.data_abertura.isoformat(),
        } if sessao else None,
        "formas_pagamento": formas,
        "top_produtos": top_produtos,
    })


# ---------------------------------------------------------------------------
# API — Abertura de caixa
# ---------------------------------------------------------------------------

@requer_permissao('pdv', 'ver')
@require_POST
def api_caixa_abrir(request):
    try:
        body = json.loads(request.body)
        caixa_id = int(body.get("caixa_id", 0))
        valor_abertura = Decimal(str(body.get("valor_abertura", "0")))
    except (ValueError, KeyError):
        return JsonResponse({"erro": "Dados inválidos."}, status=400)

    if _sessao_aberta(request):
        return JsonResponse({"erro": "Já existe uma sessão aberta para este usuário."}, status=400)

    try:
        caixa = Caixa.objects.for_filial(request.filial_ativa).get(id=caixa_id, ativo=True)
    except Caixa.DoesNotExist:
        return JsonResponse({"erro": "Caixa não encontrado."}, status=404)

    sessao = SessaoPDV.objects.create(
        filial=request.filial_ativa,
        caixa=caixa,
        usuario=request.user,
        valor_abertura=valor_abertura,
        data_abertura=timezone.now(),
        status="aberto",
    )
    return JsonResponse({
        "ok": True,
        "sessao_id": sessao.id,
        "caixa": {"id": caixa.id, "numero": caixa.numero, "descricao": caixa.descricao},
    })


# ---------------------------------------------------------------------------
# API — Criar caixa
# ---------------------------------------------------------------------------

@requer_permissao('pdv', 'ver')
@require_POST
def api_caixa_criar(request):
    """Cria um novo Caixa para a filial e o retorna pronto para seleção."""
    try:
        body = json.loads(request.body)
        descricao = str(body.get("descricao", "")).strip()[:60]
    except (ValueError, KeyError):
        return JsonResponse({"erro": "Dados inválidos."}, status=400)

    # próximo número disponível para a filial
    ultimo = Caixa.objects.for_filial(request.filial_ativa).order_by('-numero').first()
    proximo_numero = (ultimo.numero + 1) if ultimo else 1

    try:
        caixa = Caixa.objects.create(
            filial=request.filial_ativa,
            numero=proximo_numero,
            descricao=descricao,
            ativo=True,
        )
    except Exception as exc:
        return JsonResponse({"erro": f"Erro ao criar caixa: {exc}"}, status=400)

    return JsonResponse({
        "ok": True,
        "caixa": {"id": caixa.id, "numero": caixa.numero, "descricao": caixa.descricao},
    })


# ---------------------------------------------------------------------------
# API — Finalizar venda
# ---------------------------------------------------------------------------

@requer_permissao('pdv', 'ver')
@require_POST
def api_venda_finalizar(request):
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"erro": "JSON inválido."}, status=400)

    sessao = _sessao_aberta(request)
    if not sessao:
        return JsonResponse({"erro": "Nenhuma sessão de caixa aberta."}, status=400)

    itens = body.get("itens", [])
    pagamentos = body.get("pagamentos", [])
    if not itens:
        return JsonResponse({"erro": "Carrinho vazio."}, status=400)
    if not pagamentos:
        return JsonResponse({"erro": "Informe ao menos uma forma de pagamento."}, status=400)

    cliente_id = body.get("cliente_id")
    desconto = Decimal(str(body.get("desconto", "0")))
    acrescimo = Decimal(str(body.get("acrescimo", "0")))
    delivery = bool(body.get("delivery", False))
    endereco_entrega = body.get("endereco_entrega", {})
    credito_valor = Decimal(str(body.get("credito_valor", "0")))
    forcar_estoque_negativo = True

    try:
        venda = VendaPDVService.finalizar_venda(
            sessao=sessao,
            filial=request.filial_ativa,
            usuario=request.user,
            itens=itens,
            pagamentos=pagamentos,
            cliente_id=cliente_id,
            desconto=desconto,
            acrescimo=acrescimo,
            delivery=delivery,
            endereco_entrega=endereco_entrega,
            forcar_estoque_negativo=forcar_estoque_negativo,
            credito_valor=credito_valor,
        )
    except EstoqueInsuficienteError as exc:
        return JsonResponse({"erro": str(exc), "tipo": "estoque_insuficiente"}, status=400)
    except DadosInvalidosError as exc:
        return JsonResponse({"erro": str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse({"erro": str(exc)}, status=500)

    return JsonResponse({"ok": True, "numero_venda": venda.numero_venda, "venda_id": venda.id})


# ---------------------------------------------------------------------------
# API — Finalizar venda FORÇADO (ignora estoque insuficiente)
# ---------------------------------------------------------------------------

@requer_permissao('pdv', 'ver')
@require_POST
def api_venda_finalizar_forcado(request):
    """Finaliza venda ignorando verificação de estoque — operador já confirmou."""
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"erro": "JSON inválido."}, status=400)

    sessao = _sessao_aberta(request)
    if not sessao:
        return JsonResponse({"erro": "Nenhuma sessão de caixa aberta."}, status=400)

    itens = body.get("itens", [])
    pagamentos = body.get("pagamentos", [])
    if not itens:
        return JsonResponse({"erro": "Carrinho vazio."}, status=400)
    if not pagamentos:
        return JsonResponse({"erro": "Informe ao menos uma forma de pagamento."}, status=400)

    cliente_id = body.get("cliente_id")
    desconto = Decimal(str(body.get("desconto", "0")))
    acrescimo = Decimal(str(body.get("acrescimo", "0")))
    delivery = bool(body.get("delivery", False))
    endereco_entrega = body.get("endereco_entrega", {})
    credito_valor = Decimal(str(body.get("credito_valor", "0")))

    try:
        venda = VendaPDVService.finalizar_venda(
            sessao=sessao,
            filial=request.filial_ativa,
            usuario=request.user,
            itens=itens,
            pagamentos=pagamentos,
            cliente_id=cliente_id,
            desconto=desconto,
            acrescimo=acrescimo,
            delivery=delivery,
            endereco_entrega=endereco_entrega,
            forcar_estoque_negativo=True,
            credito_valor=credito_valor,
        )
    except DadosInvalidosError as exc:
        return JsonResponse({"erro": str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse({"erro": str(exc)}, status=500)

    return JsonResponse({"ok": True, "numero_venda": venda.numero_venda, "venda_id": venda.id})


# ---------------------------------------------------------------------------
# API — Salvar como pendente
# ---------------------------------------------------------------------------

@requer_permissao('pdv', 'ver')
@require_POST
def api_venda_pendente(request):
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"erro": "JSON inválido."}, status=400)

    sessao = _sessao_aberta(request)
    if not sessao:
        return JsonResponse({"erro": "Nenhuma sessão de caixa aberta."}, status=400)

    itens = body.get("itens", [])
    if not itens:
        return JsonResponse({"erro": "Carrinho vazio."}, status=400)

    cliente_id = body.get("cliente_id")
    desconto = Decimal(str(body.get("desconto", "0")))
    acrescimo = Decimal(str(body.get("acrescimo", "0")))
    delivery = bool(body.get("delivery", False))
    endereco_entrega = body.get("endereco_entrega", {})

    try:
        with transaction.atomic():
            numero = _proximo_numero_venda(request.filial_ativa)

            venda = VendaPDV.objects.create(
                sessao_pdv=sessao,
                filial=request.filial_ativa,
                numero_venda=numero,
                cliente_id=cliente_id or None,
                status="aberta",
                delivery=delivery,
                endereco_entrega=endereco_entrega,
                valor_desconto=desconto,
                valor_acrescimo=acrescimo,
                usuario=request.user,
                data_venda=timezone.now(),
            )

            subtotal = Decimal("0")
            for idx, item in enumerate(itens, start=1):
                produto = Produto.objects.select_related("unidade_medida").get(id=int(item["produto_id"]))
                quantidade = Decimal(str(item["quantidade"]))
                valor_unitario = Decimal(str(item["valor_unitario"]))
                valor_total_item = quantidade * valor_unitario
                um_sigla = produto.unidade_medida.sigla if produto.unidade_medida_id else "UN"
                ItemVendaPDV.objects.create(
                    venda_pdv=venda,
                    produto=produto,
                    numero_item=idx,
                    quantidade=quantidade,
                    unidade_medida=um_sigla,
                    valor_unitario=valor_unitario,
                    valor_total=valor_total_item,
                )
                subtotal += valor_total_item

            venda.valor_subtotal = subtotal
            venda.valor_total = subtotal - desconto + acrescimo
            venda.save(update_fields=["valor_subtotal", "valor_total"])

    except Produto.DoesNotExist:
        return JsonResponse({"erro": "Produto não encontrado."}, status=404)
    except Exception as exc:
        return JsonResponse({"erro": str(exc)}, status=500)

    return JsonResponse({"ok": True, "numero_venda": venda.numero_venda, "venda_id": venda.id})


# ---------------------------------------------------------------------------
# API — Listar pendentes
# ---------------------------------------------------------------------------

@requer_permissao('pdv', 'ver')
@require_GET
def api_pendentes(request):
    sessao = _sessao_aberta(request)
    qs = VendaPDV.objects.for_filial(request.filial_ativa).filter(
        status="aberta"
    ).select_related("cliente").order_by("-data_venda")[:50]

    pendentes = []
    for v in qs:
        pendentes.append({
            "id": v.id,
            "numero_venda": v.numero_venda,
            "cliente": v.cliente.razao_social if v.cliente else "Consumidor",
            "valor_total": float(v.valor_total),
            "data_venda": v.data_venda.isoformat(),
            "delivery": v.delivery,
        })

    return JsonResponse({"pendentes": pendentes, "sessao_ativa": sessao is not None})


# ---------------------------------------------------------------------------
# API — Detalhe de uma venda pendente (itens + cabeçalho)
# ---------------------------------------------------------------------------

@requer_permissao('pdv', 'ver')
@require_GET
def api_pendente_detalhe(request, pk):
    try:
        venda = (
            VendaPDV.objects
            .for_filial(request.filial_ativa)
            .prefetch_related("itens__produto__linha_producao", "cliente")
            .get(pk=pk, status="aberta")
        )
    except VendaPDV.DoesNotExist:
        return JsonResponse({"erro": "Venda pendente não encontrada."}, status=404)

    itens = []
    for item in venda.itens.select_related("produto__linha_producao"):
        p = item.produto
        itens.append({
            "produto_id": p.pk,
            "descricao": p.descricao_pdv or p.descricao,
            "codigo_barras": p.codigo_barras or "",
            "icone": p.linha_producao.icone if p.linha_producao else "📦",
            "cor": p.linha_producao.cor_identificacao if p.linha_producao else None,
            "linha": p.linha_producao.nome if p.linha_producao else None,
            "quantidade": float(item.quantidade),
            "valor_unitario": float(item.valor_unitario),
            "valor_total": float(item.valor_total),
            "desconto_percentual": float(item.desconto_percentual or 0),
        })

    return JsonResponse({
        "ok": True,
        "venda_id": venda.pk,
        "numero_venda": venda.numero_venda,
        "cliente_id": venda.cliente_id,
        "cliente_nome": venda.cliente.razao_social if venda.cliente else "Consumidor Final",
        "cliente_cpf_cnpj": venda.cliente.cpf_cnpj if venda.cliente else "",
        "desconto": float(venda.valor_desconto),
        "acrescimo": float(venda.valor_acrescimo),
        "delivery": venda.delivery,
        "endereco_entrega": venda.endereco_entrega or {},
        "itens": itens,
    })


# ---------------------------------------------------------------------------
# API — Cancelar venda pendente
# ---------------------------------------------------------------------------

@requer_permissao('pdv', 'ver')
@require_POST
def api_pendente_cancelar(request, pk):
    try:
        venda = VendaPDV.objects.for_filial(request.filial_ativa).get(pk=pk, status="aberta")
    except VendaPDV.DoesNotExist:
        return JsonResponse({"erro": "Venda pendente não encontrada."}, status=404)

    venda.delete()
    return JsonResponse({"ok": True})


# ---------------------------------------------------------------------------
# API — Histórico de compras (vendas finalizadas)
# ---------------------------------------------------------------------------

@requer_permissao('pdv', 'ver')
@require_GET
def api_historico(request):
    qs = (
        VendaPDV.objects.for_filial(request.filial_ativa)
        .filter(status="finalizada")
        .select_related("cliente", "documento_fiscal", "filial")
        .prefetch_related("itens")
        .order_by("-data_venda")[:30]
    )
    vendas = []
    for v in qs:
        vendas.append({
            "id": v.id,
            "numero_venda": v.numero_venda,
            "cliente": v.cliente.razao_social if v.cliente else "Consumidor Final",
            "valor_total": float(v.valor_total),
            "data_venda": v.data_venda.isoformat(),
            "delivery": v.delivery,
            "qtd_itens": v.itens.count(),
        })
    return JsonResponse({"vendas": vendas})


@requer_permissao('pdv', 'ver')
@require_GET
def api_historico_cliente(request, cliente_id):
    vendas = (
        VendaPDV.objects.for_filial(request.filial_ativa)
        .filter(cliente_id=cliente_id, status="finalizada")
        .prefetch_related("itens__produto__linha_producao", "pagamentos__forma_pagamento")
        .select_related("cliente", "documento_fiscal", "filial")
        .order_by("-data_venda")[:12]
    )

    compras = []
    for v in vendas:
        documento = obter_documento_fiscal(v)
        itens = []
        for item in v.itens.select_related("produto__linha_producao").order_by("numero_item"):
            p = item.produto
            itens.append({
                "descricao": p.descricao_pdv or p.descricao,
                "icone": p.linha_producao.icone if p.linha_producao else "📦",
                "cor": p.linha_producao.cor_identificacao if p.linha_producao else None,
                "quantidade": float(item.quantidade),
                "unidade_medida": item.unidade_medida or "UN",
                "valor_unitario": float(item.valor_unitario),
                "valor_total": float(item.valor_total),
            })
        pagamentos = [
            {
                "forma_descricao": pg.forma_pagamento.descricao,
                "valor": float(pg.valor),
                "troco": float(pg.troco or 0),
            }
            for pg in v.pagamentos.all()
        ]
        compras.append({
            "id": v.id,
            "numero_venda": v.numero_venda,
            "data_venda": v.data_venda.strftime("%d/%m/%Y"),
            "valor_total": float(v.valor_total),
            "qtd_itens": len(itens),
            "delivery": v.delivery,
            "endereco_entrega": v.endereco_entrega or {},
            "cliente_cpf_cnpj": v.cliente.cpf_cnpj if v.cliente else "",
            "cliente_endereco": {
                "rua": v.cliente.endereco or "",
                "numero": v.cliente.numero or "",
                "bairro": v.cliente.bairro or "",
                "complemento": v.cliente.complemento or "",
                "cidade": v.cliente.cidade or "",
            } if v.cliente else {},
            "itens": itens,
            "pagamentos": pagamentos,
            "documento_fiscal_status": documento.status if documento else "",
            "documento_fiscal_tipo": documento.tipo_documento if documento else "",
        })

    from django.db.models import Count as DCount
    top = (
        ItemVendaPDV.objects
        .filter(
            venda_pdv__filial=request.filial_ativa,
            venda_pdv__cliente_id=cliente_id,
            venda_pdv__status="finalizada",
        )
        .values("produto__descricao", "produto__descricao_pdv")
        .annotate(qtd_total=Sum("quantidade"), qtd_pedidos=DCount("venda_pdv", distinct=True))
        .order_by("-qtd_pedidos", "-qtd_total")[:8]
    )

    produtos_frequentes = [
        {
            "descricao": r["produto__descricao_pdv"] or r["produto__descricao"],
            "qtd_total": float(r["qtd_total"]),
            "qtd_pedidos": r["qtd_pedidos"],
        }
        for r in top
    ]

    total = VendaPDV.objects.for_filial(request.filial_ativa).filter(
        cliente_id=cliente_id, status="finalizada"
    ).count()

    return JsonResponse({
        "compras": compras,
        "produtos_frequentes": produtos_frequentes,
        "total_compras": total,
    })


# ---------------------------------------------------------------------------
# API — Detalhe e cancelamento de venda finalizada
# ---------------------------------------------------------------------------

@requer_permissao('pdv', 'ver')
@require_GET
def api_venda_detalhe(request, pk):
    try:
        venda = (
            VendaPDV.objects
            .for_filial(request.filial_ativa)
            .prefetch_related("itens__produto__linha_producao")
            .select_related("cliente", "usuario", "cancelado_por", "documento_fiscal")
            .get(pk=pk, status__in=["finalizada", "cancelada"])
        )
    except VendaPDV.DoesNotExist:
        return JsonResponse({"erro": "Venda não encontrada."}, status=404)

    itens = []
    for item in venda.itens.select_related("produto__linha_producao"):
        p = item.produto
        itens.append({
            "produto_id": p.pk,
            "descricao": p.descricao_pdv or p.descricao,
            "codigo_barras": p.codigo_barras or "",
            "icone": p.linha_producao.icone if p.linha_producao else "📦",
            "cor": p.linha_producao.cor_identificacao if p.linha_producao else None,
            "linha": p.linha_producao.nome if p.linha_producao else None,
            "quantidade": float(item.quantidade),
            "valor_unitario": float(item.valor_unitario),
            "valor_total": float(item.valor_total),
            "desconto_percentual": float(item.desconto_percentual or 0),
            "unidade_medida": item.unidade_medida or "UN",
        })

    pagamentos = [
        {
            "forma_descricao": pg.forma_pagamento.descricao,
            "valor": float(pg.valor),
            "troco": float(pg.troco or 0),
        }
        for pg in venda.pagamentos.select_related("forma_pagamento").order_by("id")
    ]

    return JsonResponse({
        "ok": True,
        "venda_id": venda.pk,
        "numero_venda": venda.numero_venda,
        "status": venda.status,
        "data_venda": timezone.localtime(venda.data_venda).strftime("%d/%m/%Y %H:%M"),
        "operador": venda.usuario.nome if venda.usuario_id else "",
        "cliente_id": venda.cliente_id,
        "cliente_nome": venda.cliente.razao_social if venda.cliente else "Consumidor Final",
        "cliente_cpf_cnpj": venda.cliente.cpf_cnpj if venda.cliente else "",
        "cliente_endereco": {
            "rua": venda.cliente.endereco or "" if venda.cliente else "",
            "numero": venda.cliente.numero or "" if venda.cliente else "",
            "bairro": venda.cliente.bairro or "" if venda.cliente else "",
            "complemento": venda.cliente.complemento or "" if venda.cliente else "",
            "cidade": venda.cliente.cidade or "" if venda.cliente else "",
        } if venda.cliente else {},
        "delivery": venda.delivery,
        "endereco_entrega": venda.endereco_entrega or {},
        "desconto": float(venda.valor_desconto or 0),
        "acrescimo": float(venda.valor_acrescimo or 0),
        "valor_total": float(venda.valor_total),
        "itens": itens,
        "pagamentos": pagamentos,
        "documento_fiscal": (
            {
                "tipo": venda.documento_fiscal.tipo_documento.upper(),
                "numero": venda.documento_fiscal.numero,
                "status": venda.documento_fiscal.status,
            }
            if venda.documento_fiscal_id else None
        ),
        "cancelamento": (
            {
                "motivo": venda.motivo_cancelamento or "Motivo não informado.",
                "usuario": venda.cancelado_por.nome if venda.cancelado_por_id else "Usuário não identificado",
                "data_hora": (
                    timezone.localtime(venda.cancelado_em).strftime("%d/%m/%Y %H:%M")
                    if venda.cancelado_em else "Data não informada"
                ),
            }
            if venda.status == "cancelada" else None
        ),
    })


@requer_permissao('pdv', 'ver')
@require_POST
def api_venda_cancelar(request, pk):
    try:
        venda = (
            VendaPDV.objects.for_filial(request.filial_ativa)
            .select_related("documento_fiscal", "filial")
            .get(pk=pk, status="finalizada")
        )
    except VendaPDV.DoesNotExist:
        return JsonResponse({"erro": "Venda não encontrada ou já cancelada."}, status=404)

    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        body = {}

    try:
        documento = cancelar_venda_e_documento(
            venda, request.user, body.get("justificativa", "")
        )
    except DadosInvalidosError as exc:
        return JsonResponse({"erro": str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse(
            {"erro": f"Cancelamento fiscal nao confirmado: {exc}"}, status=502
        )

    return JsonResponse({
        "ok": True,
        "documento_status": documento.status if documento else "sem_documento",
    })


# ---------------------------------------------------------------------------
# API — Formas de Pagamento (gestão rápida no PDV)
# ---------------------------------------------------------------------------

@requer_permissao('pdv', 'ver')
def api_formas_pagamento(request):
    filial = request.filial_ativa
    empresa = filial.empresa

    if request.method == 'GET':
        formas = list(
            FormaPagamento.objects.filter(empresa=empresa, filial=filial)
            .values('id', 'descricao', 'tipo', 'ativo', 'taxa_administrativa', 'gera_parcelas', 'prazo_liquidacao_dias')
            .order_by('descricao')
        )
        tipos = [{'valor': v, 'label': l} for v, l in TipoFormaPagamento.choices]
        return JsonResponse({'formas': formas, 'tipos': tipos})

    if request.method == 'POST':
        if not (request.user.tem_permissao('financeiro', 'criar') or request.user.tem_permissao('financeiro', 'editar')):
            return JsonResponse({'erro': 'Sem permissão para cadastrar formas de pagamento.'}, status=403)
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'erro': 'JSON inválido'}, status=400)
        descricao = (data.get('descricao') or '').strip()
        tipo = data.get('tipo', '')
        if not descricao:
            return JsonResponse({'erro': 'Descrição obrigatória.'}, status=400)
        if FormaPagamento.objects.filter(filial=filial, descricao__iexact=descricao).exists():
            return JsonResponse({'erro': 'Já existe forma de pagamento com esta descrição.'}, status=400)
        forma = FormaPagamento.objects.create(
            empresa=empresa, filial=filial,
            descricao=descricao, tipo=tipo,
            taxa_administrativa=data.get('taxa_administrativa', 0),
            gera_parcelas=bool(data.get('gera_parcelas', False)),
            prazo_liquidacao_dias=int(data.get('prazo_liquidacao_dias', 0)),
        )
        return JsonResponse({'id': forma.pk, 'descricao': forma.descricao, 'tipo': forma.tipo})

    if request.method == 'DELETE':
        if not (request.user.tem_permissao('financeiro', 'criar') or request.user.tem_permissao('financeiro', 'editar')):
            return JsonResponse({'erro': 'Sem permissão.'}, status=403)
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'erro': 'JSON inválido'}, status=400)
        pk = data.get('id')
        FormaPagamento.objects.filter(empresa=empresa, filial=filial, pk=pk).update(ativo=False)
        return JsonResponse({'ok': True})

    return JsonResponse({'erro': 'Método não permitido'}, status=405)


# API — Criar cliente rápido no PDV
# ---------------------------------------------------------------------------

@requer_permissao('pdv', 'ver')
@require_POST
def api_cliente_criar(request):
    from apps.cadastros.models import Cliente, ClienteFilial
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"erro": "JSON inválido."}, status=400)

    razao_social = body.get("razao_social", "").strip()
    if not razao_social:
        return JsonResponse({"erro": "Nome / Razão Social é obrigatório."}, status=400)

    tipo_pessoa = body.get("tipo_pessoa", "F")
    cpf_cnpj = (body.get("cpf_cnpj") or "").replace(".", "").replace("-", "").replace("/", "").strip()

    try:
        with transaction.atomic():
            cliente = Cliente.objects.create(
                filial=request.filial_ativa,
                tipo_pessoa=tipo_pessoa,
                razao_social=razao_social,
                nome_fantasia=body.get("nome_fantasia", ""),
                cpf_cnpj=cpf_cnpj,
                telefone=body.get("telefone", ""),
                celular=body.get("celular", ""),
                email=body.get("email", ""),
                cep=(body.get("cep") or "").replace("-", ""),
                endereco=body.get("endereco", ""),
                numero=body.get("numero", ""),
                complemento=body.get("complemento", ""),
                bairro=body.get("bairro", ""),
                cidade=body.get("cidade", ""),
                uf=body.get("uf", ""),
                consumidor_final=True,
                ativo=True,
            )
            ClienteFilial.objects.create(
                cliente=cliente,
                filial=request.filial_ativa,
                ativo=True,
            )
    except Exception as exc:
        return JsonResponse({"erro": str(exc)}, status=500)

    return JsonResponse({
        "ok": True,
        "cliente": {
            "id": cliente.id,
            "razao_social": cliente.razao_social,
            "cpf_cnpj": cliente.cpf_cnpj,
            "celular": cliente.celular,
            "endereco_entrega": {
                "rua": cliente.endereco or "",
                "numero": cliente.numero or "",
                "bairro": cliente.bairro or "",
                "complemento": cliente.complemento or "",
                "cidade": cliente.cidade or "",
                "uf": cliente.uf or "",
                "cep": cliente.cep or "",
            },
            "tem_endereco": bool(cliente.endereco and cliente.bairro),
        },
    })


# ---------------------------------------------------------------------------
# API — Gerar orçamento
# ---------------------------------------------------------------------------

@requer_permissao('pdv', 'ver')
@require_POST
def api_venda_orcamento(request):
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"erro": "JSON inválido."}, status=400)

    # Orçamentos não exigem caixa aberto — sessão é opcional
    sessao = _sessao_aberta(request)

    itens = body.get("itens", [])
    if not itens:
        return JsonResponse({"erro": "Carrinho vazio."}, status=400)

    cliente_id = body.get("cliente_id")
    desconto = Decimal(str(body.get("desconto", "0")))
    acrescimo = Decimal(str(body.get("acrescimo", "0")))
    delivery = bool(body.get("delivery", False))
    endereco_entrega = body.get("endereco_entrega", {})

    try:
        with transaction.atomic():
            numero = _proximo_numero_venda(request.filial_ativa)

            venda = VendaPDV.objects.create(
                sessao_pdv=sessao,  # pode ser None se não houver caixa aberto
                filial=request.filial_ativa,
                numero_venda=numero,
                cliente_id=cliente_id or None,
                status="orcamento",
                origem="pdv",
                delivery=delivery,
                endereco_entrega=endereco_entrega,
                valor_desconto=desconto,
                valor_acrescimo=acrescimo,
                usuario=request.user,
                data_venda=timezone.now(),
            )

            subtotal = Decimal("0")
            for idx, item in enumerate(itens, start=1):
                produto = Produto.objects.select_related("unidade_medida").get(id=int(item["produto_id"]))
                quantidade = Decimal(str(item["quantidade"]))
                valor_unitario = Decimal(str(item["valor_unitario"]))
                valor_total_item = quantidade * valor_unitario
                um_sigla = produto.unidade_medida.sigla if produto.unidade_medida_id else "UN"
                ItemVendaPDV.objects.create(
                    venda_pdv=venda,
                    produto=produto,
                    numero_item=idx,
                    quantidade=quantidade,
                    unidade_medida=um_sigla,
                    valor_unitario=valor_unitario,
                    valor_total=valor_total_item,
                )
                subtotal += valor_total_item

            venda.valor_subtotal = subtotal
            venda.valor_total = subtotal - desconto + acrescimo
            venda.save(update_fields=["valor_subtotal", "valor_total"])

    except Produto.DoesNotExist:
        return JsonResponse({"erro": "Produto não encontrado."}, status=404)
    except Exception as exc:
        return JsonResponse({"erro": str(exc)}, status=500)

    return JsonResponse({"ok": True, "numero_venda": venda.numero_venda, "venda_id": venda.id})


# ---------------------------------------------------------------------------
# Helper — Resumo de fechamento de caixa
# ---------------------------------------------------------------------------

def _resumo_sessao(sessao):
    """Monta o resumo completo de uma sessão de caixa para relatório."""
    vendas = (
        VendaPDV.objects.filter(sessao_pdv=sessao, status="finalizada")
        .select_related("cliente")
        .prefetch_related("pagamentos__forma_pagamento", "itens")
        .order_by("numero_venda")
    )

    total_balcao = Decimal("0")
    total_delivery = Decimal("0")
    qtd_balcao = 0
    qtd_delivery = 0
    qtd_itens = Decimal("0")
    desconto_total = Decimal("0")
    troco_dinheiro = Decimal("0")
    dinheiro_bruto = Decimal("0")

    formas_acc = {}   # forma_id -> {descricao, tipo, valor, qtd}
    vendas_list = []

    for v in vendas:
        if v.delivery:
            total_delivery += v.valor_total
            qtd_delivery += 1
        else:
            total_balcao += v.valor_total
            qtd_balcao += 1
        desconto_total += v.valor_desconto or Decimal("0")

        for it in v.itens.all():
            qtd_itens += it.quantidade

        for pg in v.pagamentos.all():
            fp = pg.forma_pagamento
            acc = formas_acc.setdefault(fp.id, {
                "descricao": fp.descricao, "tipo": fp.tipo,
                "valor": Decimal("0"), "qtd": 0,
            })
            acc["valor"] += pg.valor
            acc["qtd"] += 1
            if fp.tipo == "dinheiro":
                dinheiro_bruto += pg.valor
                troco_dinheiro += pg.troco

        vendas_list.append({
            "id": v.id,
            "numero_venda": v.numero_venda,
            "cliente": v.cliente.razao_social if v.cliente else "Consumidor Final",
            "valor_total": float(v.valor_total),
            "delivery": v.delivery,
            "tipo": "Delivery" if v.delivery else "Balcão",
            "data_venda": v.data_venda.isoformat(),
            "qtd_itens": v.itens.count(),
        })

    movs = list(MovimentacaoCaixa.objects.filter(sessao_pdv=sessao))
    total_sangrias = sum((m.valor for m in movs if m.tipo == "sangria"), Decimal("0"))
    total_suprimentos = sum((m.valor for m in movs if m.tipo == "suprimento"), Decimal("0"))
    movimentacoes = [{
        "tipo": m.tipo,
        "valor": float(m.valor),
        "observacao": m.observacao,
        "data": m.data_movimentacao.isoformat(),
    } for m in movs if m.tipo in ("sangria", "suprimento")]

    # Dinheiro físico esperado na gaveta
    esperado_dinheiro = (
        sessao.valor_abertura + dinheiro_bruto - troco_dinheiro
        + total_suprimentos - total_sangrias
    )

    total_geral = total_balcao + total_delivery

    return {
        "sessao": {
            "id": sessao.id,
            "caixa_numero": sessao.caixa.numero,
            "caixa_descricao": sessao.caixa.descricao,
            "operador": sessao.usuario.nome or sessao.usuario.email,
            "data_abertura": sessao.data_abertura.isoformat(),
            "valor_abertura": float(sessao.valor_abertura),
            "status": sessao.status,
            "data_fechamento": sessao.data_fechamento.isoformat() if sessao.data_fechamento else None,
        },
        "resumo": {
            "total_geral": float(total_geral),
            "qtd_vendas": qtd_balcao + qtd_delivery,
            "qtd_itens": float(qtd_itens),
            "desconto_total": float(desconto_total),
            "total_balcao": float(total_balcao),
            "qtd_balcao": qtd_balcao,
            "total_delivery": float(total_delivery),
            "qtd_delivery": qtd_delivery,
        },
        "formas_pagamento": sorted([
            {"descricao": f["descricao"], "tipo": f["tipo"],
             "valor": float(f["valor"]), "qtd": f["qtd"]}
            for f in formas_acc.values()
        ], key=lambda x: -x["valor"]),
        "caixa": {
            "valor_abertura": float(sessao.valor_abertura),
            "dinheiro_vendas": float(dinheiro_bruto),
            "troco": float(troco_dinheiro),
            "suprimentos": float(total_suprimentos),
            "sangrias": float(total_sangrias),
            "esperado_dinheiro": float(esperado_dinheiro),
        },
        "movimentacoes": movimentacoes,
        "vendas": vendas_list,
    }


# ---------------------------------------------------------------------------
# API - Resumo do caixa (relatorio de fechamento)
# ---------------------------------------------------------------------------

def _combinar_resumos_caixa(sessoes):
    """Combina varias sessoes de caixa em um resumo por data."""
    combinado = {
        "sessao": {
            "id": None, "caixa_numero": "Todos", "caixa_descricao": "Relatorio por data",
            "operador": "Todos", "data_abertura": None, "valor_abertura": 0,
            "status": "relatorio", "data_fechamento": None,
        },
        "resumo": {
            "total_geral": 0, "qtd_vendas": 0, "qtd_itens": 0,
            "desconto_total": 0, "total_balcao": 0, "qtd_balcao": 0,
            "total_delivery": 0, "qtd_delivery": 0,
        },
        "formas_pagamento": [],
        "caixa": {
            "valor_abertura": 0, "dinheiro_vendas": 0, "troco": 0,
            "suprimentos": 0, "sangrias": 0, "esperado_dinheiro": 0,
        },
        "movimentacoes": [], "vendas": [], "sessoes": [],
    }
    formas = {}
    for sessao in sessoes:
        resumo = _resumo_sessao(sessao)
        combinado["sessoes"].append(resumo["sessao"])
        for chave, valor in resumo["resumo"].items():
            combinado["resumo"][chave] += valor or 0
        for chave, valor in resumo["caixa"].items():
            combinado["caixa"][chave] += valor or 0
        combinado["movimentacoes"].extend(resumo["movimentacoes"])
        combinado["vendas"].extend(resumo["vendas"])
        for forma in resumo["formas_pagamento"]:
            acc = formas.setdefault(forma["descricao"], {
                "descricao": forma["descricao"], "tipo": forma.get("tipo", ""),
                "valor": 0, "qtd": 0,
            })
            acc["valor"] += forma.get("valor") or 0
            acc["qtd"] += forma.get("qtd") or 0
    combinado["formas_pagamento"] = sorted(formas.values(), key=lambda item: -item["valor"])
    return combinado


@requer_permissao('pdv', 'ver')
@require_GET
def api_caixa_resumo(request):
    sessao = _sessao_aberta(request)
    if not sessao:
        return JsonResponse({"erro": "Nenhuma sessao de caixa aberta."}, status=400)
    return JsonResponse(_resumo_sessao(sessao))


@requer_permissao('pdv', 'ver')
@require_GET
def api_caixa_relatorio_data(request):
    data_txt = (request.GET.get("data") or "").strip()
    try:
        data_ref = datetime.datetime.strptime(data_txt, "%Y-%m-%d").date() if data_txt else timezone.localdate()
    except ValueError:
        return JsonResponse({"erro": "Data invalida."}, status=400)

    sessoes = list(
        SessaoPDV.objects.for_filial(request.filial_ativa)
        .filter(data_abertura__date=data_ref)
        .select_related("caixa", "usuario")
        .order_by("data_abertura")
    )
    relatorio = _combinar_resumos_caixa(sessoes)
    relatorio["data"] = data_ref.isoformat()
    relatorio["data_label"] = data_ref.strftime("%d/%m/%Y")
    return JsonResponse(relatorio)

# ---------------------------------------------------------------------------
# API — Fechar caixa (com conferência)
# ---------------------------------------------------------------------------

@requer_permissao('pdv', 'ver')
@require_POST
def api_caixa_fechar(request):
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"erro": "JSON inválido."}, status=400)

    sessao = _sessao_aberta(request)
    if not sessao:
        return JsonResponse({"erro": "Nenhuma sessão de caixa aberta."}, status=400)

    try:
        valor_contado = Decimal(str(body.get("valor_contado", "0")))
    except (ValueError, TypeError):
        return JsonResponse({"erro": "Valor contado inválido."}, status=400)

    resumo = _resumo_sessao(sessao)
    esperado = Decimal(str(resumo["caixa"]["esperado_dinheiro"]))
    diferenca = valor_contado - esperado

    with transaction.atomic():
        sessao.status = "fechado"
        sessao.data_fechamento = timezone.now()
        sessao.valor_fechamento_informado = valor_contado
        sessao.valor_fechamento_sistema = esperado
        sessao.diferenca_caixa = diferenca
        sessao.conferido_por = request.user
        sessao.conferido_em = timezone.now()
        sessao.observacao_conferencia = body.get("observacao", "")
        sessao.save(update_fields=[
            "status", "data_fechamento", "valor_fechamento_informado",
            "valor_fechamento_sistema", "diferenca_caixa",
            "conferido_por", "conferido_em", "observacao_conferencia",
        ])
        MovimentacaoCaixa.objects.create(
            sessao_pdv=sessao,
            filial=request.filial_ativa,
            tipo="fechamento",
            valor=valor_contado,
            observacao=body.get("observacao", "")[:200],
            usuario=request.user,
            data_movimentacao=timezone.now(),
        )

    return JsonResponse({
        "ok": True,
        "esperado_dinheiro": float(esperado),
        "valor_contado": float(valor_contado),
        "diferenca": float(diferenca),
    })



# ---------------------------------------------------------------------------
# API - Registrar sangria / suprimento
# ---------------------------------------------------------------------------

@requer_permissao('pdv', 'ver')
@require_POST
def api_caixa_movimentacao(request):
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"erro": "JSON invalido."}, status=400)

    sessao = _sessao_aberta(request)
    if not sessao:
        return JsonResponse({"erro": "Nenhuma sessao de caixa aberta."}, status=400)

    tipo = body.get("tipo")
    if tipo not in ("sangria", "suprimento"):
        return JsonResponse({"erro": "Tipo invalido. Use sangria ou suprimento."}, status=400)

    try:
        valor = Decimal(str(body.get("valor", "0")))
    except (ValueError, TypeError):
        return JsonResponse({"erro": "Valor invalido."}, status=400)
    if valor <= 0:
        return JsonResponse({"erro": "O valor deve ser maior que zero."}, status=400)

    with transaction.atomic():
        MovimentacaoCaixa.objects.create(
            sessao_pdv=sessao,
            filial=request.filial_ativa,
            tipo=tipo,
            valor=valor,
            observacao=body.get("observacao", "")[:200],
            usuario=request.user,
            data_movimentacao=timezone.now(),
        )
        if tipo == "sangria":
            sessao.total_sangrias = (sessao.total_sangrias or Decimal("0")) + valor
            sessao.save(update_fields=["total_sangrias"])
        else:
            sessao.total_suprimentos = (sessao.total_suprimentos or Decimal("0")) + valor
            sessao.save(update_fields=["total_suprimentos"])

    return JsonResponse({"ok": True})
# ---------------------------------------------------------------------------
# Delivery Kanban
# ---------------------------------------------------------------------------

DELIVERY_COLUNAS = [
    ('novo', 'Novo Pedido', '#3b82f6'),
    ('preparando', 'Em Preparo', '#f59e0b'),
    ('em_entrega', 'Saiu para Entrega', '#8b5cf6'),
    ('entregue', 'Entregue', '#10b981'),
]

DELIVERY_STATUS_VALIDOS = {c[0] for c in DELIVERY_COLUNAS} | {'cancelado'}


@requer_permissao('pdv', 'ver')
def delivery_kanban(request):
    qs = (
        VendaPDV.objects
        .for_filial(request.filial_ativa)
        .filter(delivery=True)
        .exclude(status='cancelada')
        .exclude(status_delivery='cancelado')
        .select_related('cliente', 'usuario')
        .prefetch_related('itens__produto', 'pagamentos__forma_pagamento')
        .order_by('data_venda')
    )

    colunas = []
    for status_key, label, cor in DELIVERY_COLUNAS:
        pedidos = [v for v in qs if v.status_delivery == status_key]
        colunas.append({
            'key': status_key,
            'label': label,
            'cor': cor,
            'pedidos': pedidos,
        })

    pedidos_data = {}
    for v in qs:
        pagamentos = [
            {
                'forma_descricao': pg.forma_pagamento.descricao if pg.forma_pagamento else 'Pagamento',
                'valor': float(pg.valor),
                'troco': float(pg.troco or 0),
            }
            for pg in v.pagamentos.all()
        ]
        itens = [
            {
                'descricao': item.produto.descricao if item.produto else '',
                'quantidade': float(item.quantidade),
                'unidade_medida': item.unidade_medida or 'UN',
                'valor_unitario': float(item.valor_unitario),
                'valor_total': float(item.valor_total),
            }
            for item in v.itens.all()
        ]
        pedidos_data[str(v.pk)] = {
            'numero_venda': v.numero_venda,
            'data_venda': v.data_venda.strftime('%d/%m/%Y %H:%M'),
            'cliente_nome': (v.cliente.nome_fantasia or v.cliente.razao_social) if v.cliente else 'Consumidor Final',
            'cliente_cpf_cnpj': v.cliente.cpf_cnpj if v.cliente else '',
            'endereco_entrega': v.endereco_entrega or {},
            'observacao_delivery': v.observacao_delivery or '',
            'valor_total': float(v.valor_total),
            'itens': itens,
            'pagamentos': pagamentos,
        }

    filial = request.filial_ativa
    filial_info = {
        'nome': filial.nome_fantasia or filial.razao_social,
        'cnpj': filial.cnpj,
        'endereco': filial.endereco,
        'numero': filial.numero,
        'bairro': filial.bairro,
        'cidade': filial.cidade,
        'uf': filial.uf,
        'telefone': filial.telefone,
        'logo_url': request.build_absolute_uri(filial.imagem.url) if filial.imagem else '',
    }

    return render(request, 'pdv/delivery_kanban.html', {
        'colunas': colunas,
        'total': qs.count(),
        'pedidos_json': json.dumps(pedidos_data, ensure_ascii=False),
        'filial_json': json.dumps(filial_info, ensure_ascii=False),
    })


@require_POST
@requer_permissao('pdv', 'editar')
def delivery_mover(request, pk):
    try:
        body = json.loads(request.body or b'{}')
    except ValueError:
        return JsonResponse({'erro': 'JSON invalido'}, status=400)

    novo_status = body.get('status', '').strip()
    if novo_status not in DELIVERY_STATUS_VALIDOS:
        return JsonResponse({'erro': 'Status invalido'}, status=400)

    venda = VendaPDV.objects.for_filial(request.filial_ativa).filter(pk=pk, delivery=True).first()
    if not venda:
        return JsonResponse({'erro': 'Pedido nao encontrado'}, status=404)

    campos = ['status_delivery']
    venda.status_delivery = novo_status

    observacao = body.get('observacao', '').strip()
    if observacao:
        venda.observacao_delivery = observacao
        campos.append('observacao_delivery')

    entregador = body.get('entregador', '').strip()
    if entregador:
        venda.entregador = entregador[:100]
        campos.append('entregador')

    venda.save(update_fields=campos)
    return JsonResponse({
        'ok': True,
        'status': venda.status_delivery,
        'status_label': venda.get_status_delivery_display(),
    })


@require_POST
@requer_permissao('pdv', 'editar')
def delivery_atualizar(request, pk):
    try:
        body = json.loads(request.body or b'{}')
    except ValueError:
        return JsonResponse({'erro': 'JSON invalido'}, status=400)

    venda = VendaPDV.objects.for_filial(request.filial_ativa).filter(pk=pk, delivery=True).first()
    if not venda:
        return JsonResponse({'erro': 'Pedido nao encontrado'}, status=404)

    campos = []
    if 'entregador' in body:
        venda.entregador = str(body['entregador'])[:100]
        campos.append('entregador')
    if 'observacao_delivery' in body:
        venda.observacao_delivery = str(body['observacao_delivery'])
        campos.append('observacao_delivery')

    if campos:
        venda.save(update_fields=campos)

    return JsonResponse({'ok': True})


# ---------------------------------------------------------------------------
# API — Emitir NFC-e para uma venda finalizada
# ---------------------------------------------------------------------------

@requer_permissao('pdv', 'ver')
@require_GET
def api_prontidao_fiscal(request, pk):
    """Explica o que precisa ser ajustado antes da NF-e/NFC-e."""
    tipo = (request.GET.get("tipo") or "nfce").lower()
    try:
        venda = (
            VendaPDV.objects.for_filial(request.filial_ativa)
            .select_related("cliente", "filial__empresa")
            .get(pk=pk)
        )
    except VendaPDV.DoesNotExist:
        return JsonResponse({"erro": "Venda nao encontrada."}, status=404)
    return JsonResponse(verificar_prontidao_fiscal(venda, tipo))


@requer_permissao('pdv', 'ver')
@require_POST
def api_emitir_nfce(request, pk):
    """
    Emite a NFC-e (Nota Fiscal de Consumidor Eletrônica) para uma venda PDV.

    Produtos SEM código de barras aparecem com cEAN = "SEM GTIN" no XML,
    conforme exigência da SEFAZ (NT 2011/004).
    """
    try:
        venda = (
            VendaPDV.objects.for_filial(request.filial_ativa)
            .prefetch_related("itens__produto__unidade_medida", "pagamentos__forma_pagamento")
            .select_related("cliente", "filial")
            .get(pk=pk)
        )
    except VendaPDV.DoesNotExist:
        return JsonResponse({"erro": "Venda não encontrada."}, status=404)

    if venda.status not in ("finalizada", "orcamento"):
        return JsonResponse(
            {"erro": f"Não é possível emitir NFC-e para venda com status '{venda.status}'."},
            status=400,
        )

    prontidao = verificar_prontidao_fiscal(venda, "nfce")
    if not prontidao["ok"]:
        return JsonResponse(prontidao, status=422)

    try:
        from apps.pdv.services.nfce_payload_builder import (
            NfcePayloadBuilder,
            emitir_nfce_para_venda,
        )
        documento = emitir_nfce_para_venda(
            venda,
            request.user,
            informacoes_adicionais=_informacoes_adicionais_request(request),
        )
    except DadosInvalidosError as exc:
        return JsonResponse({"erro": str(exc)}, status=400)
    except Exception as exc:
        permite_contingencia = isinstance(exc, (FocusNFeNetworkError, FocusNFeServerError))
        return JsonResponse({
            "erro": f"Erro ao emitir NFC-e: {exc}",
            "permite_contingencia": permite_contingencia,
        }, status=502)

    return JsonResponse({
        "ok": True,
        "documento_id": documento.pk,
        "status": documento.status,
        "chave": documento.chave or "",
        "pdf_danfe_url": documento.pdf_danfe_url or "",
        "mensagem": documento.mensagem_sefaz or "",
    })


@requer_permissao('pdv', 'ver')
@require_POST
def api_emitir_nfce_contingencia(request, pk):
    """Emite NFC-e em contingencia quando a autorizacao normal estiver indisponivel."""
    try:
        venda = (
            VendaPDV.objects.for_filial(request.filial_ativa)
            .select_related("cliente", "filial__empresa")
            .get(pk=pk)
        )
    except VendaPDV.DoesNotExist:
        return JsonResponse({"erro": "Venda nao encontrada."}, status=404)
    prontidao = verificar_prontidao_fiscal(venda, "nfce")
    if not prontidao["ok"]:
        return JsonResponse(prontidao, status=422)
    try:
        from apps.pdv.services.nfce_payload_builder import emitir_nfce_para_venda
        documento = emitir_nfce_para_venda(
            venda,
            request.user,
            contingencia=True,
            informacoes_adicionais=_informacoes_adicionais_request(request),
        )
    except DadosInvalidosError as exc:
        return JsonResponse({"erro": str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse({"erro": f"Nao foi possivel emitir em contingencia: {exc}"}, status=502)
    return JsonResponse({
        "ok": True,
        "documento_id": documento.pk,
        "status": documento.status,
        "chave": documento.chave or "",
        "pdf_danfe_url": documento.pdf_danfe_url or "",
        "mensagem": documento.mensagem_sefaz or "NFC-e emitida em contingencia; acompanhe a autorizacao.",
    })


@requer_permissao('pdv', 'ver')
@require_GET
def api_preview_nfce(request, pk):
    """
    Retorna o payload JSON que seria enviado para o Focus NFe (sem emitir).
    Útil para debug e verificação de GTIN/dados fiscais antes da emissão.
    """
    try:
        venda = (
            VendaPDV.objects.for_filial(request.filial_ativa)
            .prefetch_related("itens__produto__unidade_medida", "pagamentos__forma_pagamento")
            .select_related("cliente", "filial")
            .get(pk=pk)
        )
    except VendaPDV.DoesNotExist:
        return JsonResponse({"erro": "Venda não encontrada."}, status=404)

    try:
        from apps.pdv.services.nfce_payload_builder import NfcePayloadBuilder
        payload = NfcePayloadBuilder.build(venda)
    except DadosInvalidosError as exc:
        return JsonResponse({"erro": str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse({"erro": f"Erro ao montar payload: {exc}"}, status=500)

    return JsonResponse({"ok": True, "payload": payload})


# ---------------------------------------------------------------------------
# API — Inutilizar faixa de numeração NFC-e / NF-e
# ---------------------------------------------------------------------------

@requer_permissao('pdv', 'ver')
@require_POST
def api_inutilizar_faixa(request):
    """
    Inutiliza uma faixa de numeração junto à SEFAZ via Focus NF-e.

    Body JSON:
        tipo_documento  : "nfce" | "nfe"
        serie           : int
        numero_inicial  : int
        numero_final    : int
        justificativa   : str (mín. 15 chars)
    """
    import json as _json
    from apps.fiscal.integrations.focusnfe import FocusNFeClient
    from apps.fiscal.integrations.focusnfe.config import FocusNFeConfig
    from apps.financeiro.constants.enums import StatusDocumentoFiscal
    from apps.financeiro.models.fiscal import DocumentoFiscal

    try:
        body = _json.loads(request.body)
    except Exception:
        return JsonResponse({"erro": "JSON inválido."}, status=400)

    tipo_documento = body.get("tipo_documento", "nfce")
    if tipo_documento not in ("nfce", "nfe"):
        return JsonResponse({"erro": "tipo_documento deve ser 'nfce' ou 'nfe'."}, status=400)

    justificativa = (body.get("justificativa") or "").strip()
    if len(justificativa) < 15:
        return JsonResponse({"erro": "Justificativa deve ter ao menos 15 caracteres."}, status=400)

    try:
        serie = int(body.get("serie", 1))
        numero_inicial = int(body.get("numero_inicial"))
        numero_final = int(body.get("numero_final"))
    except (TypeError, ValueError):
        return JsonResponse({"erro": "serie, numero_inicial e numero_final devem ser inteiros."}, status=400)

    if numero_final < numero_inicial:
        return JsonResponse({"erro": "numero_final deve ser >= numero_inicial."}, status=400)

    filial = request.filial_ativa
    cnpj = (filial.cnpj or "").replace(".", "").replace("/", "").replace("-", "")
    if not cnpj:
        return JsonResponse({"erro": "Filial sem CNPJ cadastrado."}, status=400)

    try:
        filial_token = getattr(filial, "focusnfe_token", "") or ""
        filial_ambiente = getattr(filial, "focusnfe_ambiente", None)
        if filial_token:
            config = FocusNFeConfig.from_env(token=filial_token, ambiente=filial_ambiente)
            client = FocusNFeClient(config=config)
        else:
            client = FocusNFeClient()

        resource = getattr(client, tipo_documento)
        resposta = resource.inutilizar(
            cnpj=cnpj,
            serie=serie,
            numero_inicial=numero_inicial,
            numero_final=numero_final,
            justificativa=justificativa,
        )
    except ValueError as exc:
        return JsonResponse({"erro": str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse({"erro": f"Erro ao inutilizar: {exc}"}, status=500)

    # Registra DocumentoFiscal para cada número inutilizado
    for num in range(numero_inicial, numero_final + 1):
        DocumentoFiscal.objects.get_or_create(
            filial=filial,
            tipo_documento=tipo_documento,
            numero=num,
            serie=serie,
            defaults={
                "origem_tipo": "inutilizacao",
                "origem_id": 0,
                "emitente_cnpj": cnpj,
                "status": StatusDocumentoFiscal.INUTILIZADA,
                "valor_total": 0,
                "usuario": request.user,
            },
        )

    return JsonResponse({
        "ok": True,
        "tipo_documento": tipo_documento,
        "serie": serie,
        "numero_inicial": numero_inicial,
        "numero_final": numero_final,
        "resposta_sefaz": resposta,
    })


# ---------------------------------------------------------------------------
# Orçamentos — Página de listagem
# ---------------------------------------------------------------------------

@requer_permissao('pdv', 'ver')
def orcamentos_list(request):
    return render(request, "pdv/orcamentos_list.html", {"title": "Orçamentos PDV"})


# ---------------------------------------------------------------------------
# API — Cancelar NF-e/NFC-e de uma venda
# ---------------------------------------------------------------------------

@requer_permissao('pdv', 'ver')
@require_POST
def api_cancelar_nfce(request, pk):
    """Cancela a NF-e/NFC-e na Focus e somente depois cancela a venda."""
    try:
        venda = (
            VendaPDV.objects.for_filial(request.filial_ativa)
            .select_related('documento_fiscal')
            .get(pk=pk)
        )
    except VendaPDV.DoesNotExist:
        return JsonResponse({"erro": "Venda não encontrada."}, status=404)

    doc = obter_documento_fiscal(venda)
    if not doc:
        return JsonResponse({"erro": "Esta venda não possui documento fiscal."}, status=400)

    if doc.status != "autorizada":
        return JsonResponse(
            {"erro": f"Apenas documentos autorizados podem ser cancelados (status atual: {doc.status})."},
            status=400,
        )

    try:
        body = json.loads(request.body or b'{}')
    except json.JSONDecodeError:
        body = {}

    justificativa = body.get("justificativa", "").strip()
    if len(justificativa) < 15:
        return JsonResponse(
            {"erro": "Informe uma justificativa com ao menos 15 caracteres."},
            status=400,
        )

    try:
        doc = cancelar_venda_e_documento(venda, request.user, justificativa)
    except DadosInvalidosError as exc:
        return JsonResponse({"erro": str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse(
            {"erro": f"Cancelamento fiscal nao confirmado: {exc}"}, status=502
        )

    return JsonResponse({"ok": True, "status": doc.status})


# ---------------------------------------------------------------------------
# API — Listar orçamentos
# ---------------------------------------------------------------------------

@requer_permissao('pdv', 'ver')
@require_GET
def api_credito_cliente(request):
    """Retorna saldo de crédito disponível para um cliente."""
    from apps.financeiro.models.credito_cliente import CreditoCliente
    cliente_id = request.GET.get('cliente_id')
    if not cliente_id:
        return JsonResponse({'saldo': '0.00', 'creditos': []})
    filial = request.filial_ativa
    creditos = CreditoCliente.objects.filter(
        filial=filial,
        cliente_id=cliente_id,
        status=CreditoCliente.Status.DISPONIVEL,
    ).order_by('created_at')
    saldo_total = Decimal('0')
    lista = []
    for c in creditos:
        saldo = c.valor_saldo
        if saldo > 0:
            saldo_total += saldo
            lista.append({'id': c.pk, 'saldo': float(saldo), 'motivo': c.motivo})
    return JsonResponse({'saldo': float(saldo_total), 'creditos': lista})


@requer_permissao('pdv', 'ver')
@require_GET
def api_orcamentos(request):
    q = request.GET.get("q", "").strip()
    qs = (
        VendaPDV.objects.for_filial(request.filial_ativa)
        .filter(status="orcamento")
        .select_related("cliente", "usuario")
        .prefetch_related("itens")
        .order_by("-data_venda")
    )
    cliente_id = request.GET.get("cliente_id")
    if cliente_id:
        qs = qs.filter(cliente_id=cliente_id)
    if q:
        qs = qs.filter(
            Q(numero_venda__icontains=q)
            | Q(cliente__razao_social__icontains=q)
            | Q(cliente__nome_fantasia__icontains=q)
            | Q(cliente__cpf_cnpj__icontains=q)
        )

    orcamentos = []
    for v in qs[:60]:
        orcamentos.append({
            "id": v.id,
            "numero_venda": v.numero_venda,
            "cliente": v.cliente.razao_social if v.cliente else "Consumidor Final",
            "cliente_id": v.cliente_id,
            "valor_total": float(v.valor_total),
            "valor_desconto": float(v.valor_desconto or 0),
            "qtd_itens": v.itens.count(),
            "data_venda": v.data_venda.strftime("%d/%m/%Y %H:%M"),
            "usuario": getattr(v.usuario, 'nome', None) or getattr(v.usuario, 'email', '') or str(v.usuario),
        })

    return JsonResponse({"orcamentos": orcamentos})


# ---------------------------------------------------------------------------
# API — Detalhe de um orçamento
# ---------------------------------------------------------------------------

@requer_permissao('pdv', 'ver')
@require_GET
def api_orcamento_detalhe(request, pk):
    try:
        venda = (
            VendaPDV.objects.for_filial(request.filial_ativa)
            .prefetch_related("itens__produto__linha_producao")
            .select_related("cliente", "usuario")
            .get(pk=pk, status="orcamento")
        )
    except VendaPDV.DoesNotExist:
        return JsonResponse({"erro": "Orçamento não encontrado."}, status=404)

    itens = []
    for item in venda.itens.select_related("produto__linha_producao").order_by("numero_item"):
        p = item.produto
        itens.append({
            "produto_id": p.pk,
            "descricao": p.descricao_pdv or p.descricao,
            "codigo_barras": p.codigo_barras or "",
            "icone": p.linha_producao.icone if p.linha_producao else "📦",
            "cor": p.linha_producao.cor_identificacao if p.linha_producao else None,
            "linha": p.linha_producao.nome if p.linha_producao else None,
            "quantidade": float(item.quantidade),
            "valor_unitario": float(item.valor_unitario),
            "valor_total": float(item.valor_total),
            "desconto_percentual": float(item.desconto_percentual or 0),
            "unidade_medida": item.unidade_medida,
        })

    return JsonResponse({
        "ok": True,
        "id": venda.pk,
        "numero_venda": venda.numero_venda,
        "cliente_id": venda.cliente_id,
        "cliente_nome": venda.cliente.razao_social if venda.cliente else "Consumidor Final",
        "cliente_cpf_cnpj": venda.cliente.cpf_cnpj if venda.cliente else "",
        "valor_subtotal": float(venda.valor_subtotal or 0),
        "valor_desconto": float(venda.valor_desconto or 0),
        "valor_acrescimo": float(venda.valor_acrescimo or 0),
        "valor_total": float(venda.valor_total),
        "data_venda": venda.data_venda.strftime("%d/%m/%Y %H:%M"),
        "usuario": getattr(venda.usuario, 'nome', None) or getattr(venda.usuario, 'email', '') or str(venda.usuario),
        "delivery": venda.delivery,
        "endereco_entrega": venda.endereco_entrega or {},
        "itens": itens,
    })


# ---------------------------------------------------------------------------
# API — Cancelar orçamento
# ---------------------------------------------------------------------------

@requer_permissao('pdv', 'ver')
@require_POST
def api_orcamento_cancelar(request, pk):
    try:
        venda = VendaPDV.objects.for_filial(request.filial_ativa).get(pk=pk, status="orcamento")
    except VendaPDV.DoesNotExist:
        return JsonResponse({"erro": "Orçamento não encontrado."}, status=404)

    venda.delete()
    return JsonResponse({"ok": True})


# ---------------------------------------------------------------------------
# API — Retomar orçamento (carrega de volta no PDV como pendente)
# ---------------------------------------------------------------------------

@requer_permissao('pdv', 'ver')
@require_POST
def api_orcamento_retomar(request, pk):
    """
    Converte o orçamento em venda pendente (status='aberta') dentro da sessão
    ativa do caixa, permitindo que o operador retome e finalize a venda no PDV.
    """
    sessao = _sessao_aberta(request)
    if not sessao:
        return JsonResponse({"erro": "Nenhuma sessão de caixa aberta. Abra o caixa para retomar o orçamento."}, status=400)

    try:
        venda = (
            VendaPDV.objects.for_filial(request.filial_ativa)
            .prefetch_related("itens__produto")
            .get(pk=pk, status="orcamento")
        )
    except VendaPDV.DoesNotExist:
        return JsonResponse({"erro": "Orçamento não encontrado."}, status=404)

    # Atualiza para pendente na sessão atual
    venda.status = "aberta"
    venda.sessao_pdv = sessao
    venda.save(update_fields=["status", "sessao_pdv"])

    # Retorna os dados completos para o PDV carregar
    itens = []
    for item in venda.itens.select_related("produto__linha_producao").order_by("numero_item"):
        p = item.produto
        itens.append({
            "produto_id": p.pk,
            "descricao": p.descricao_pdv or p.descricao,
            "codigo_barras": p.codigo_barras or "",
            "icone": p.linha_producao.icone if p.linha_producao else "📦",
            "cor": p.linha_producao.cor_identificacao if p.linha_producao else None,
            "linha": p.linha_producao.nome if p.linha_producao else None,
            "quantidade": float(item.quantidade),
            "valor_unitario": float(item.valor_unitario),
            "valor_total": float(item.valor_total),
            "desconto_percentual": float(item.desconto_percentual or 0),
            "unidade_medida": item.unidade_medida,
        })

    return JsonResponse({
        "ok": True,
        "venda_id": venda.pk,
        "numero_venda": venda.numero_venda,
        "cliente_id": venda.cliente_id,
        "cliente_nome": venda.cliente.razao_social if venda.cliente else "Consumidor Final",
        "cliente_cpf_cnpj": venda.cliente.cpf_cnpj if venda.cliente else "",
        "desconto": float(venda.valor_desconto or 0),
        "acrescimo": float(venda.valor_acrescimo or 0),
        "itens": itens,
    })


# ---------------------------------------------------------------------------
# API — Enviar NFC-e por e-mail
# ---------------------------------------------------------------------------

@requer_permissao('pdv', 'ver')
@require_POST
def api_email_nfce(request, pk):
    """Envia o documento fiscal (NFC-e/NF-e) por e-mail via Focus NFe."""
    import json as _json
    try:
        body = _json.loads(request.body)
    except Exception:
        body = {}

    emails = body.get('emails', [])
    if not emails:
        return JsonResponse({"erro": "Informe ao menos um e-mail."}, status=400)

    try:
        venda = (
            VendaPDV.objects.for_filial(request.filial_ativa)
            .select_related('documento_fiscal')
            .get(pk=pk)
        )
    except VendaPDV.DoesNotExist:
        return JsonResponse({"erro": "Venda não encontrada."}, status=404)

    doc = venda.documento_fiscal
    if not doc:
        return JsonResponse({"erro": "Esta venda não possui documento fiscal."}, status=400)
    if doc.status != "autorizada":
        return JsonResponse({"erro": "Só é possível enviar e-mail de documentos autorizados."}, status=400)

    try:
        from apps.fiscal.services.focusnfe_service import FocusNFeService, gerar_ref
        from apps.fiscal.integrations.focusnfe import FocusNFeClient
        from apps.fiscal.integrations.focusnfe.config import FocusNFeConfig

        filial = venda.filial_ativa if hasattr(venda, 'filial_ativa') else request.filial_ativa
        filial_token = getattr(filial, 'focusnfe_token', '') or ''
        filial_ambiente = getattr(filial, 'focusnfe_ambiente', None)
        if filial_token:
            config = FocusNFeConfig.from_env(token=filial_token, ambiente=filial_ambiente)
            client = FocusNFeClient(config=config)
        else:
            client = FocusNFeClient()

        resource_map = {"nfce": client.nfce, "nfe": client.nfe}
        resource = resource_map.get(doc.tipo_documento)
        if not resource:
            return JsonResponse({"erro": f"Tipo de documento '{doc.tipo_documento}' não suportado."}, status=400)

        ref = gerar_ref(doc)
        resource.enviar_email(ref, emails)
        return JsonResponse({"ok": True})
    except Exception as exc:
        return JsonResponse({"erro": str(exc)}, status=500)

# ---------------------------------------------------------------------------
# API — Download XML NFC-e/NF-e
# ---------------------------------------------------------------------------

from django.http import HttpResponse as _HttpResponse

@requer_permissao('pdv', 'ver')
def api_xml_nfce(request, pk):
    """Retorna o XML assinado da NFC-e/NF-e para download."""
    try:
        venda = (
            VendaPDV.objects.for_filial(request.filial_ativa)
            .select_related('documento_fiscal')
            .get(pk=pk)
        )
    except VendaPDV.DoesNotExist:
        return JsonResponse({"erro": "Venda não encontrada."}, status=404)

    doc = venda.documento_fiscal
    if not doc or not doc.xml_assinado:
        return JsonResponse({"erro": "XML não disponível para esta venda."}, status=404)

    resp = _HttpResponse(doc.xml_assinado, content_type='application/xml; charset=utf-8')
    fname = f"{doc.tipo_documento.upper()}_{doc.numero:09d}_{doc.serie}.xml"
    resp['Content-Disposition'] = f'attachment; filename="{fname}"'
    return resp

# ---------------------------------------------------------------------------
# API — Emitir NF-e para uma venda finalizada
# ---------------------------------------------------------------------------

@requer_permissao('pdv', 'ver')
@require_POST
def api_emitir_nfe(request, pk):
    """Emite a NF-e para uma venda PDV."""
    try:
        venda = (
            VendaPDV.objects.for_filial(request.filial_ativa)
            .prefetch_related("itens__produto__unidade_medida", "pagamentos__forma_pagamento")
            .select_related("cliente", "filial")
            .get(pk=pk)
        )
    except VendaPDV.DoesNotExist:
        return JsonResponse({"erro": "Venda não encontrada."}, status=404)

    if venda.status not in ("finalizada", "orcamento"):
        return JsonResponse(
            {"erro": f"Não é possível emitir NF-e para venda com status '{venda.status}'."},
            status=400,
        )

    prontidao = verificar_prontidao_fiscal(venda, "nfe")
    if not prontidao["ok"]:
        return JsonResponse(prontidao, status=422)

    try:
        from apps.pdv.services.nfce_payload_builder import emitir_nfe_para_venda
        documento = emitir_nfe_para_venda(
            venda,
            request.user,
            informacoes_adicionais=_informacoes_adicionais_request(request),
        )
    except DadosInvalidosError as exc:
        return JsonResponse({"erro": str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse({"erro": f"Erro ao emitir NF-e: {exc}"}, status=500)

    return JsonResponse({
        "ok": True,
        "documento_id": documento.pk,
        "status": documento.status,
        "chave": documento.chave or "",
        "pdf_danfe_url": documento.pdf_danfe_url or "",
        "mensagem": documento.mensagem_sefaz or "",
    })


# ---------------------------------------------------------------------------
# API — Cancelar venda e documento fiscal
# ---------------------------------------------------------------------------

@requer_permissao('pdv', 'ver')
@require_POST
def api_cancelar_venda_historico(request, pk):
    """Rota legada que usa o mesmo fluxo fiscal seguro do PDV."""
    try:
        venda = (
            VendaPDV.objects.for_filial(request.filial_ativa)
            .select_related('documento_fiscal')
            .get(pk=pk, status='finalizada')
        )
    except VendaPDV.DoesNotExist:
        return JsonResponse({"erro": "Venda não encontrada ou já cancelada."}, status=404)

    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        body = {}
    try:
        documento = cancelar_venda_e_documento(
            venda, request.user, body.get("justificativa", "")
        )
    except DadosInvalidosError as exc:
        return JsonResponse({"erro": str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse(
            {"erro": f"Cancelamento fiscal nao confirmado: {exc}"}, status=502
        )
    return JsonResponse({
        "ok": True,
        "status": documento.status if documento else "sem_documento",
    })
