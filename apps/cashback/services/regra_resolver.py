"""
Resolução da regra de cashback aplicável a um produto/venda.

Prioridade (do mais específico para o mais genérico):
    Produto -> Categoria (sobe a hierarquia de categoria_pai) -> Campanha
    -> Filial -> Empresa -> Configuração global (0% se nada existir).

Cada nível pode desativar cashback explicitamente (produto/categoria com
`gera_cashback=False`), o que interrompe a resolução e retorna 0 —
mesmo que um nível mais genérico tivesse uma regra configurada.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

from django.utils import timezone


@dataclass
class ResultadoRegraCashback:
    percentual: Decimal
    origem: str  # 'produto' | 'categoria' | 'campanha' | 'filial' | 'empresa' | 'global' | 'desabilitado'
    valor_minimo_gerar: Optional[Decimal] = None
    regra_id: Optional[int] = None
    campanha_id: Optional[int] = None
    valor_fixo_unidade: Optional[Decimal] = None  # R$ por unidade vendida — se informado, tem prioridade sobre percentual

    @property
    def gera_cashback(self) -> bool:
        if self.origem == "desabilitado":
            return False
        return self.percentual > 0 or bool(self.valor_fixo_unidade and self.valor_fixo_unidade > 0)


def obter_configuracao_cashback(filial):
    """Configuração da filial, com fallback para a configuração global da empresa."""
    from apps.cashback.models import ConfiguracaoCashback

    config = (
        ConfiguracaoCashback.objects.filter(filial=filial, ativo=True).first()
        or ConfiguracaoCashback.objects.filter(
            empresa=filial.empresa_id, filial__isnull=True, ativo=True,
        ).first()
    )
    return config


def _categorias_ate_a_raiz(categoria):
    """Percorre a categoria e seus ancestrais (categoria_pai) até a raiz."""
    atual = categoria
    visitados = set()
    while atual is not None and atual.pk not in visitados:
        yield atual
        visitados.add(atual.pk)
        atual = atual.categoria_pai


def _melhor_campanha(*, empresa, filial, produto, categoria, data: date):
    from apps.cashback.models import CampanhaCashback

    candidatas = (
        CampanhaCashback.objects.filter(
            empresa=empresa, ativo=True, data_inicio__lte=data, data_fim__gte=data,
        )
        .prefetch_related("produtos", "categorias", "filiais")
        .order_by("-prioridade", "-percentual")
    )
    for campanha in candidatas:
        if not campanha.vigente_em(data):
            continue
        if campanha.aplica_a(produto=produto, categoria=categoria, filial=filial):
            return campanha
    return None


def resolver_percentual(*, produto, filial, data: date | None = None) -> ResultadoRegraCashback:
    """
    Resolve o percentual de cashback aplicável para `produto` vendido em
    `filial`, seguindo a cadeia de prioridade. `produto` pode ser None
    (ex.: item sem produto vinculado) — nesse caso pula direto para
    filial/empresa/global.
    """
    from apps.cashback.models import RegraCashbackEmpresa, RegraCashbackFilial

    data = data or timezone.localdate()
    empresa = filial.empresa

    # 1) Produto
    if produto is not None:
        regra_produto = getattr(produto, "regra_cashback", None)
        if regra_produto is not None and regra_produto.ativo:
            if not regra_produto.gera_cashback:
                return ResultadoRegraCashback(Decimal("0"), "desabilitado")
            if regra_produto.valor_fixo_unidade and regra_produto.valor_fixo_unidade > 0:
                return ResultadoRegraCashback(
                    Decimal("0"), "produto", regra_produto.valor_minimo_gerar,
                    regra_id=regra_produto.pk, valor_fixo_unidade=regra_produto.valor_fixo_unidade,
                )
            if regra_produto.percentual > 0:
                return ResultadoRegraCashback(
                    regra_produto.percentual, "produto",
                    regra_produto.valor_minimo_gerar, regra_id=regra_produto.pk,
                )

    # 2) Categoria (sobe a hierarquia até achar uma regra ou uma exclusão)
    categoria = getattr(produto, "categoria", None) if produto is not None else None
    categoria_efetiva = categoria
    if categoria is not None:
        for cat in _categorias_ate_a_raiz(categoria):
            regra_categoria = getattr(cat, "regra_cashback", None)
            if regra_categoria is not None and regra_categoria.ativo:
                if not regra_categoria.gera_cashback:
                    return ResultadoRegraCashback(Decimal("0"), "desabilitado")
                if regra_categoria.valor_fixo_unidade and regra_categoria.valor_fixo_unidade > 0:
                    return ResultadoRegraCashback(
                        Decimal("0"), "categoria", regra_categoria.valor_minimo_gerar,
                        regra_id=regra_categoria.pk, valor_fixo_unidade=regra_categoria.valor_fixo_unidade,
                    )
                if regra_categoria.percentual > 0:
                    return ResultadoRegraCashback(
                        regra_categoria.percentual, "categoria",
                        regra_categoria.valor_minimo_gerar, regra_id=regra_categoria.pk,
                    )

    # 3) Campanha promocional vigente
    campanha = _melhor_campanha(
        empresa=empresa, filial=filial, produto=produto, categoria=categoria_efetiva, data=data,
    )
    if campanha is not None and campanha.percentual > 0:
        return ResultadoRegraCashback(campanha.percentual, "campanha", campanha_id=campanha.pk)

    # 4) Filial
    regra_filial = RegraCashbackFilial.objects.filter(filial=filial, ativo=True).first()
    if regra_filial is not None:
        if regra_filial.valor_fixo_unidade and regra_filial.valor_fixo_unidade > 0:
            return ResultadoRegraCashback(
                Decimal("0"), "filial", regra_filial.valor_minimo_gerar,
                regra_id=regra_filial.pk, valor_fixo_unidade=regra_filial.valor_fixo_unidade,
            )
        if regra_filial.percentual > 0:
            return ResultadoRegraCashback(
                regra_filial.percentual, "filial", regra_filial.valor_minimo_gerar, regra_id=regra_filial.pk,
            )

    # 5) Empresa
    regra_empresa = RegraCashbackEmpresa.objects.filter(empresa=empresa, ativo=True).first()
    if regra_empresa is not None:
        if regra_empresa.valor_fixo_unidade and regra_empresa.valor_fixo_unidade > 0:
            return ResultadoRegraCashback(
                Decimal("0"), "empresa", regra_empresa.valor_minimo_gerar,
                regra_id=regra_empresa.pk, valor_fixo_unidade=regra_empresa.valor_fixo_unidade,
            )
        if regra_empresa.percentual > 0:
            return ResultadoRegraCashback(
                regra_empresa.percentual, "empresa", regra_empresa.valor_minimo_gerar, regra_id=regra_empresa.pk,
            )

    # 6) Configuração global (percentual padrão) — 0% se não houver nada configurado.
    config = obter_configuracao_cashback(filial)
    if config is not None and config.percentual_global > 0:
        return ResultadoRegraCashback(config.percentual_global, "global", config.valor_minimo_gerar)

    return ResultadoRegraCashback(Decimal("0"), "global")
