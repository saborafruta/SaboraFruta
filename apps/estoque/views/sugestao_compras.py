"""Modulo de Sugestao de Compras — giro, demanda, cobertura e reposicao inteligente."""
from __future__ import annotations

import csv
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.db.models import Max, Q, Sum
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views import View

from apps.cadastros.models import Fornecedor
from apps.core.models import Filial
from apps.core.services.permissions import PermissaoRequiredMixin
from apps.estoque.models import Estoque, LoteProduto, MovimentacaoEstoque
from apps.estoque.views.permissoes import permissoes_estoque
from apps.produtos.models import CategoriaProduto, LinhaProducao, MarcaProduto, Produto

ZERO = Decimal("0")
PERIODOS = [
    ("30",  "Últimos 30 dias"),
    ("60",  "Últimos 60 dias"),
    ("90",  "Últimos 90 dias"),
    ("180", "Últimos 180 dias"),
    ("365", "Últimos 365 dias"),
]

TIPOS_SAIDA = {
    "saida",
    "transferencia_saida",
    "producao_saida",
    "pedido_venda",
    "venda_pdv",
    "ajuste_negativo",
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _d(v) -> Decimal:
    try:
        return Decimal(str(v or "0"))
    except Exception:
        return ZERO


def _pct(a: Decimal, b: Decimal) -> Decimal:
    if not b:
        return ZERO
    return (a / b * 100).quantize(Decimal("0.01"))


def _calcular_linha(
    produto: Produto,
    estoque_obj,
    saida_periodo: Decimal,
    dias_periodo: int,
    dias_cobertura: int,
    ultimo_custo: Decimal,
    lotes_vencendo: bool,
    estoques_outras_filiais: list[dict],
) -> dict:
    saldo = _d(estoque_obj.quantidade_disponivel if estoque_obj else 0)
    saida = _d(saida_periodo)

    dias_periodo = max(dias_periodo, 1)
    demanda_diaria = (saida / dias_periodo).quantize(Decimal("0.0001"))
    demanda_mensal = (demanda_diaria * 30).quantize(Decimal("0.001"))

    cobertura_atual = (
        (saldo / demanda_diaria).quantize(Decimal("0.1"))
        if demanda_diaria > 0 else Decimal("9999")
    )

    sugestao = max(ZERO, (demanda_diaria * dias_cobertura - saldo)).quantize(Decimal("0.001"))

    cobertura_pos = (
        ((saldo + sugestao) / demanda_diaria).quantize(Decimal("0.1"))
        if demanda_diaria > 0 else Decimal("9999")
    )

    preco_venda = _d(produto.preco_venda)
    custo = _d(ultimo_custo) if ultimo_custo else _d(produto.preco_custo_medio or produto.preco_custo)

    margem = _pct(preco_venda - custo, preco_venda) if preco_venda else ZERO
    markup = _pct(preco_venda - custo, custo) if custo else ZERO

    estoque_min = _d(produto.estoque_minimo)
    ponto_rep = _d(produto.ponto_reposicao)

    status_critico = saldo <= 0 or (estoque_min > 0 and saldo < estoque_min)
    status_baixo = not status_critico and ponto_rep > 0 and saldo <= ponto_rep

    return {
        "produto": produto,
        "codigo_barras": produto.codigo_barras or "—",
        "saldo": saldo,
        "saida_periodo": saida,
        "demanda_diaria": demanda_diaria,
        "giro_mensal": demanda_mensal,
        "cobertura_atual": cobertura_atual,
        "dias_em_estoque": cobertura_atual,
        "sugestao": sugestao,
        "cobertura_pos": cobertura_pos,
        "ultimo_custo": custo,
        "preco_negociado": ZERO,  # expandir via tabela de preco negociado
        "margem": margem,
        "markup": markup,
        "transferencias": estoques_outras_filiais,
        "status_critico": status_critico,
        "status_baixo": status_baixo,
        "alerta_vencimento": lotes_vencendo,
        "tem_alerta": status_critico or status_baixo or lotes_vencendo,
    }


# ---------------------------------------------------------------------------
# View principal
# ---------------------------------------------------------------------------

class SugestaoComprasView(PermissaoRequiredMixin, View):
    permissao_modulo = "estoque"
    template_name = "estoque/sugestao_compras/list.html"

    # -- filtros da requisicao -----------------------------------------------

    def _parse_filtros(self, request) -> dict:
        GET = request.GET
        return {
            "periodo":          GET.get("periodo", "60"),
            "duracao":          int(GET.get("duracao", "60") or "60"),
            "manter_dias":      int(GET.get("manter_dias", "45") or "45"),
            "fornecedor_id":    GET.get("fornecedor_id", ""),
            "marca_id":         GET.get("marca_id", ""),
            "categoria_id":     GET.get("categoria_id", ""),
            "subcategoria_id":  GET.get("subcategoria_id", ""),
            "busca":            GET.get("busca", "").strip(),
            "mostrar_todos":    GET.get("mostrar_todos") == "1",
            "mostrar_cobertura": GET.get("mostrar_cobertura", "1") == "1",
            "mostrar_conversao": GET.get("mostrar_conversao") == "1",
            "so_criticos":      GET.get("so_criticos") == "1",
            "so_baixos":        GET.get("so_baixos") == "1",
            "so_vencimento":    GET.get("so_vencimento") == "1",
        }

    # -- queryset de produtos ------------------------------------------------

    def _qs_produtos(self, filial, filtros: dict):
        qs = (
            Produto.objects
            .for_filial(filial)
            .filter(ativo=True)
            .select_related("fornecedor", "categoria", "subcategoria", "marca", "linha_producao")
        )
        if filtros["fornecedor_id"]:
            qs = qs.filter(fornecedor_id=filtros["fornecedor_id"])
        if filtros["marca_id"]:
            qs = qs.filter(marca_id=filtros["marca_id"])
        if filtros["categoria_id"]:
            qs = qs.filter(categoria_id=filtros["categoria_id"])
        if filtros["subcategoria_id"]:
            qs = qs.filter(subcategoria_id=filtros["subcategoria_id"])
        if filtros["busca"]:
            q = filtros["busca"]
            qs = qs.filter(
                Q(descricao__icontains=q) | Q(codigo_barras__icontains=q) | Q(referencia__icontains=q)
            )
        return qs.order_by("descricao")

    # -- calculos de giro (saidas) -------------------------------------------

    def _saidas_por_produto(self, filial, produto_ids: list, data_ini: date) -> dict:
        """Retorna {produto_id: Decimal(quantidade_saida)} no periodo."""
        rows = (
            MovimentacaoEstoque.objects
            .filter(
                filial=filial,
                produto_id__in=produto_ids,
                tipo_operacao__in=TIPOS_SAIDA,
                data_movimentacao__date__gte=data_ini,
            )
            .values("produto_id")
            .annotate(total=Sum("quantidade"))
        )
        return {r["produto_id"]: _d(r["total"]) for r in rows}

    # -- ultimo custo (ultima entrada NF) ------------------------------------

    def _ultimos_custos(self, produto_ids: list) -> dict:
        """Retorna {produto_id: Decimal(custo)} baseado na ultima entrada de NF."""
        try:
            from apps.compras.models import ItemEntradaNF
            rows = (
                ItemEntradaNF.objects
                .filter(produto_id__in=produto_ids, custo_unitario_total__gt=0)
                .values("produto_id")
                .annotate(ultimo=Max("id"))
            )
            id_map = {r["produto_id"]: r["ultimo"] for r in rows}
            if not id_map:
                return {}
            items = ItemEntradaNF.objects.filter(id__in=id_map.values()).values("id", "produto_id", "custo_unitario_total")
            inv = {v: k for k, v in id_map.items()}
            return {item["produto_id"]: _d(item["custo_unitario_total"]) for item in items}
        except Exception:
            return {}

    # -- lotes vencendo (proximos 30 dias) -----------------------------------

    def _lotes_vencendo(self, filial, produto_ids: list) -> set:
        hoje = timezone.localdate()
        limite = hoje + timedelta(days=30)
        ids = (
            LoteProduto.objects
            .filter(
                filial=filial,
                produto_id__in=produto_ids,
                status=LoteProduto.Status.ATIVO,
                quantidade_atual__gt=0,
                data_validade__isnull=False,
                data_validade__lte=limite,
            )
            .values_list("produto_id", flat=True)
            .distinct()
        )
        return set(ids)

    # -- estoques em outras filiais ------------------------------------------

    def _outras_filiais(self, filial, produto_ids: list) -> dict:
        """Retorna {produto_id: [{"filial": nome, "saldo": Decimal}]}."""
        rows = (
            Estoque.objects
            .filter(produto_id__in=produto_ids, quantidade_disponivel__gt=0)
            .exclude(filial=filial)
            .select_related("filial")
        )
        result: dict = {}
        for row in rows:
            result.setdefault(row.produto_id, []).append({
                "filial": row.filial.nome_fantasia or row.filial.razao_social,
                "saldo": _d(row.quantidade_disponivel),
            })
        return result

    # -- GET -----------------------------------------------------------------

    def get(self, request):
        filtros = self._parse_filtros(request)
        filial = request.filial_ativa

        dias_periodo = int(filtros["periodo"])
        data_ini = timezone.localdate() - timedelta(days=dias_periodo)

        produtos_qs = self._qs_produtos(filial, filtros)

        # listas de opcoes para os filtros
        fornecedores   = Fornecedor.objects.for_filial(filial).order_by("razao_social")
        marcas         = MarcaProduto.objects.for_filial(filial).order_by("nome")
        categorias     = CategoriaProduto.objects.filter(
            filial=filial, categoria_pai__isnull=True
        ).order_by("nome")
        subcategorias  = CategoriaProduto.objects.filter(
            filial=filial, categoria_pai__isnull=False
        ).order_by("nome")

        linhas = LinhaProducao.objects.order_by("nome")

        # busca foi solicitada ou ha filtro ativo
        busca_realizada = bool(
            filtros["busca"] or filtros["fornecedor_id"] or filtros["marca_id"]
            or filtros["categoria_id"] or filtros["subcategoria_id"]
            or request.GET.get("buscar")
        )

        linhas_tabela = []
        resumo = {
            "total": 0, "criticos": 0, "baixos": 0,
            "vencimentos": 0, "sugestao_total": ZERO,
        }

        if busca_realizada or request.GET.get("buscar"):
            produto_ids = list(produtos_qs.values_list("id", flat=True))

            estoques_map = {
                row["produto_id"]: SimpleNamespace(quantidade_disponivel=row["disp"] or ZERO)
                for row in (
                    Estoque.objects.filter(produto_id__in=produto_ids, filial=filial)
                    .values("produto_id")
                    .annotate(disp=Sum("quantidade_disponivel"))
                )
            }
            saidas_map    = self._saidas_por_produto(filial, produto_ids, data_ini)
            custos_map    = self._ultimos_custos(produto_ids)
            vencendo_set  = self._lotes_vencendo(filial, produto_ids)
            outras_map    = self._outras_filiais(filial, produto_ids)

            for produto in produtos_qs:
                estoque_obj   = estoques_map.get(produto.id)
                saida         = saidas_map.get(produto.id, ZERO)
                custo         = custos_map.get(produto.id, ZERO)
                vencendo      = produto.id in vencendo_set
                outras        = outras_map.get(produto.id, [])

                linha = _calcular_linha(
                    produto=produto,
                    estoque_obj=estoque_obj,
                    saida_periodo=saida,
                    dias_periodo=dias_periodo,
                    dias_cobertura=filtros["manter_dias"],
                    ultimo_custo=custo,
                    lotes_vencendo=vencendo,
                    estoques_outras_filiais=outras,
                )

                # filtros de status
                if filtros["so_criticos"] and not linha["status_critico"]:
                    continue
                if filtros["so_baixos"] and not linha["status_baixo"]:
                    continue
                if filtros["so_vencimento"] and not linha["alerta_vencimento"]:
                    continue
                if not filtros["mostrar_todos"] and linha["sugestao"] <= 0 and not linha["tem_alerta"]:
                    continue

                linhas_tabela.append(linha)

            # resumo
            resumo["total"]          = len(linhas_tabela)
            resumo["criticos"]       = sum(1 for l in linhas_tabela if l["status_critico"])
            resumo["baixos"]         = sum(1 for l in linhas_tabela if l["status_baixo"])
            resumo["vencimentos"]    = sum(1 for l in linhas_tabela if l["alerta_vencimento"])
            resumo["sugestao_total"] = sum((l["sugestao"] for l in linhas_tabela), ZERO)

        # exportar CSV
        if request.GET.get("export") == "csv" and linhas_tabela:
            return self._exportar_csv(linhas_tabela)

        return render(request, self.template_name, {
            "filtros":        filtros,
            "periodos":       PERIODOS,
            "fornecedores":   fornecedores,
            "marcas":         marcas,
            "categorias":     categorias,
            "subcategorias":  subcategorias,
            "linhas":         linhas_tabela,
            "resumo":         resumo,
            "busca_realizada": busca_realizada or bool(request.GET.get("buscar")),
            "permissoes_estoque": permissoes_estoque(request),
        })

    # -- exportar CSV --------------------------------------------------------

    def _exportar_csv(self, linhas: list) -> HttpResponse:
        response = HttpResponse(content_type="text/csv; charset=utf-8-sig")
        response["Content-Disposition"] = 'attachment; filename="sugestao_compras.csv"'
        writer = csv.writer(response, delimiter=";")
        writer.writerow([
            "Produto", "Cód. Barras", "Saldo", "Saída Período",
            "Giro Mensal", "Demanda Diária", "Cobertura (dias)", "Sugestão",
            "Cob. Pós Compra", "Último Custo", "Margem %", "Markup %",
            "Crítico", "Baixo", "Vencimento",
        ])
        for l in linhas:
            writer.writerow([
                l["produto"].descricao,
                l["codigo_barras"],
                str(l["saldo"]).replace(".", ","),
                str(l["saida_periodo"]).replace(".", ","),
                str(l["giro_mensal"]).replace(".", ","),
                str(l["demanda_diaria"]).replace(".", ","),
                str(l["cobertura_atual"]).replace(".", ","),
                str(l["sugestao"]).replace(".", ","),
                str(l["cobertura_pos"]).replace(".", ","),
                str(l["ultimo_custo"]).replace(".", ","),
                str(l["margem"]).replace(".", ","),
                str(l["markup"]).replace(".", ","),
                "Sim" if l["status_critico"] else "Não",
                "Sim" if l["status_baixo"] else "Não",
                "Sim" if l["alerta_vencimento"] else "Não",
            ])
        return response
