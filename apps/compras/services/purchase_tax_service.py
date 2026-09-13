"""Motor tributario isolado da interface de cotacao."""
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Q

from apps.compras.models import RegraTributariaCompra


CENTAVO_CONTABIL = Decimal('0.0001')


@dataclass(frozen=True)
class ResultadoTributario:
    valor_bruto: Decimal
    custos_nao_recuperaveis: Decimal
    credito_ibs: Decimal
    credito_cbs: Decimal
    outros_creditos: Decimal
    credito_total: Decimal
    custo_efetivo: Decimal
    regra: RegraTributariaCompra | None
    regra_snapshot: dict


class PurchaseTaxService:
    """Resolve regras vigentes sem embutir aliquotas fiscais no codigo."""

    @staticmethod
    def _compativel(valor_regra, valor_operacao):
        return not valor_regra or valor_regra == valor_operacao

    @classmethod
    def resolver_regra(
        cls, *, empresa, data_referencia, regime_comprador,
        regime_ibs_cbs_comprador, regime_fornecedor,
        regime_ibs_cbs_fornecedor, ncm, classe_fiscal_id,
    ):
        candidatas = RegraTributariaCompra.objects.filter(
            empresa=empresa,
            ativo=True,
            data_inicial__lte=data_referencia,
        ).filter(Q(data_final__isnull=True) | Q(data_final__gte=data_referencia))

        validas = []
        for regra in candidatas:
            if not cls._compativel(regra.regime_comprador, regime_comprador):
                continue
            if not cls._compativel(regra.regime_fornecedor, regime_fornecedor):
                continue
            if not cls._compativel(regra.regime_ibs_cbs_comprador, regime_ibs_cbs_comprador):
                continue
            if not cls._compativel(regra.regime_ibs_cbs_fornecedor, regime_ibs_cbs_fornecedor):
                continue
            if regra.ncm_prefixo and not (ncm or '').startswith(regra.ncm_prefixo):
                continue
            if regra.classe_fiscal_id and regra.classe_fiscal_id != classe_fiscal_id:
                continue
            especificidade = (
                sum(bool(valor) for valor in (
                    regra.regime_comprador,
                    regra.regime_fornecedor,
                    regra.regime_ibs_cbs_comprador,
                    regra.regime_ibs_cbs_fornecedor,
                )) * 10
                + len(regra.ncm_prefixo or '')
                + (20 if regra.classe_fiscal_id else 0)
            )
            validas.append((especificidade, regra.data_inicial, regra.pk, regra))
        return max(validas, default=(0, data_referencia, 0, None))[-1]

    @classmethod
    def calcular(
        cls, *, empresa, data_referencia, produto, quantidade, valor_unitario,
        frete_nao_recuperavel, desconto, regime_comprador,
        regime_ibs_cbs_comprador, regime_fornecedor, regime_ibs_cbs_fornecedor,
    ):
        valor_bruto = (quantidade * valor_unitario).quantize(CENTAVO_CONTABIL, rounding=ROUND_HALF_UP)
        frete = Decimal(frete_nao_recuperavel or 0)
        desconto = min(Decimal(desconto or 0), valor_bruto + frete)
        base_credito = max(valor_bruto - desconto, Decimal('0'))
        regra = cls.resolver_regra(
            empresa=empresa,
            data_referencia=data_referencia,
            regime_comprador=regime_comprador,
            regime_ibs_cbs_comprador=regime_ibs_cbs_comprador,
            regime_fornecedor=regime_fornecedor,
            regime_ibs_cbs_fornecedor=regime_ibs_cbs_fornecedor,
            ncm=produto.ncm,
            classe_fiscal_id=produto.classe_fiscal_id,
        )

        def credito(percentual):
            return (base_credito * Decimal(percentual or 0) / 100).quantize(
                CENTAVO_CONTABIL, rounding=ROUND_HALF_UP,
            )

        credito_ibs = credito(regra.percentual_credito_ibs) if regra else Decimal('0')
        credito_cbs = credito(regra.percentual_credito_cbs) if regra else Decimal('0')
        outros = credito(regra.percentual_outros_creditos) if regra else Decimal('0')
        total_creditos = min(credito_ibs + credito_cbs + outros, valor_bruto + frete - desconto)
        custo_efetivo = (valor_bruto + frete - desconto - total_creditos).quantize(
            CENTAVO_CONTABIL, rounding=ROUND_HALF_UP,
        )
        snapshot = {
            'regra_id': regra.pk if regra else None,
            'regra_nome': regra.nome if regra else 'Nenhuma regra parametrizada aplicavel',
            'classe_fiscal_id': regra.classe_fiscal_id if regra else None,
            'data_inicial': regra.data_inicial.isoformat() if regra else None,
            'data_final': regra.data_final.isoformat() if regra and regra.data_final else None,
            'percentual_credito_ibs': str(regra.percentual_credito_ibs) if regra else '0',
            'percentual_credito_cbs': str(regra.percentual_credito_cbs) if regra else '0',
            'percentual_outros_creditos': str(regra.percentual_outros_creditos) if regra else '0',
            'condicoes': regra.condicoes if regra else {},
            'base_credito': str(base_credito),
        }
        return ResultadoTributario(
            valor_bruto=valor_bruto,
            custos_nao_recuperaveis=frete,
            credito_ibs=credito_ibs,
            credito_cbs=credito_cbs,
            outros_creditos=outros,
            credito_total=total_creditos,
            custo_efetivo=custo_efetivo,
            regra=regra,
            regra_snapshot=snapshot,
        )
