"""FEFO — First Expired, First Out.

Retorna os lotes de um produto ordenados pela política FEFO:
  1. Lotes com validade, ordenados por data de validade crescente (mais antigo primeiro)
  2. Lotes sem validade, ordenados por data de criação crescente

Apenas lotes ATIVO com quantidade_atual > 0 e não vencidos são retornados.
"""
from django.utils import timezone

from apps.estoque.models import LoteProduto


def sugerir_lotes_fefo(produto_id: int, filial_id: int) -> list[LoteProduto]:
    """Retorna lista de lotes disponíveis para saída em ordem FEFO."""
    hoje = timezone.localdate()

    com_validade = list(
        LoteProduto.objects
        .filter(
            produto_id=produto_id,
            filial_id=filial_id,
            status=LoteProduto.Status.ATIVO,
            quantidade_atual__gt=0,
            data_validade__isnull=False,
            data_validade__gte=hoje,
        )
        .order_by('data_validade', 'created_at')
        .select_related('produto')
    )

    sem_validade = list(
        LoteProduto.objects
        .filter(
            produto_id=produto_id,
            filial_id=filial_id,
            status=LoteProduto.Status.ATIVO,
            quantidade_atual__gt=0,
            data_validade__isnull=True,
        )
        .order_by('created_at')
        .select_related('produto')
    )

    return com_validade + sem_validade


def proximo_lote_fefo(produto_id: int, filial_id: int) -> LoteProduto | None:
    """Retorna o lote que deve ser consumido primeiro pelo critério FEFO."""
    lotes = sugerir_lotes_fefo(produto_id, filial_id)
    return lotes[0] if lotes else None
