"""Serviço de explosão de BOM (Bill of Materials)."""
from decimal import Decimal
from django.db import transaction
from apps.produtos.models import FichaTecnica


class BOMService:
    """Calcula necessidades de matéria-prima a partir da Ficha Técnica vigente."""

    @staticmethod
    def explodir(ficha_tecnica: FichaTecnica, quantidade_planejada: Decimal):
        """Retorna lista de dicts {materia_prima, quantidade_necessaria, unidade}.

        A quantidade é proporcional: para cada 1 unidade do produto acabado,
        consome `quantidade_padrao` da matéria-prima. Aqui multiplicamos pela
        quantidade planejada.
        """
        resultado = []
        for item in ficha_tecnica.itens.select_related("materia_prima").all():
            necessario = Decimal(item.quantidade_padrao) * Decimal(quantidade_planejada)
            resultado.append({
                "materia_prima": item.materia_prima,
                "quantidade_necessaria": necessario,
                "unidade": item.unidade_medida,
                "tolerancia_percentual": item.tolerancia_percentual,
                "ordem_mistura": item.ordem_mistura,
            })
        return sorted(resultado, key=lambda r: r.get("ordem_mistura") or 999)

    @staticmethod
    def vigente_para(produto_acabado, data_referencia=None) -> FichaTecnica | None:
        """Retorna a ficha vigente para um produto na data referência."""
        from django.utils import timezone
        data_referencia = data_referencia or timezone.localdate()
        return (
            FichaTecnica.objects
            .filter(
                produto_acabado=produto_acabado, ativo=True,
                data_vigencia_inicio__lte=data_referencia,
            )
            .filter(
                models_data_fim_null_or_after(data_referencia)
            )
            .order_by("-versao").first()
        )


def models_data_fim_null_or_after(data):
    from django.db.models import Q
    return Q(data_vigencia_fim__isnull=True) | Q(data_vigencia_fim__gte=data)
