from __future__ import annotations

from django.db.models import Sum
from django.utils import timezone

from apps.produtos.models import Produto
from apps.produtos.services.preco_service import PrecoService


class CardapioService:
    """Leituras do Cardápio Digital público. Não muta nada."""

    @staticmethod
    def produtos_para_filial(filial):
        return (
            Produto.objects.for_filial(filial)
            .filter(ativo=True)
            .select_related('categoria')
            .order_by('categoria__nome', 'descricao')
        )

    @staticmethod
    def mais_vendidos(filial, dias: int = 30, limite: int = 8):
        from apps.pdv.models import ItemVendaPDV

        desde = timezone.now() - timezone.timedelta(days=dias)
        produto_ids = list(
            ItemVendaPDV.objects
            .filter(venda_pdv__filial=filial, venda_pdv__data_venda__gte=desde)
            .values('produto_id')
            .annotate(total=Sum('quantidade'))
            .order_by('-total')
            .values_list('produto_id', flat=True)[:limite]
        )
        produtos = Produto.objects.for_filial(filial).filter(pk__in=produto_ids, ativo=True)
        por_id = {p.pk: p for p in produtos}
        return [por_id[pk] for pk in produto_ids if pk in por_id]

    @staticmethod
    def em_promocao(filial):
        resultado = []
        for produto in CardapioService.produtos_para_filial(filial):
            if PrecoService.produto_tem_promocao_vigente(produto, filial=filial):
                produto.preco_promocional_exibicao = PrecoService.preco_promocional_vigente(
                    produto, filial=filial,
                )
                resultado.append(produto)
        return resultado
