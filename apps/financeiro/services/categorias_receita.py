from apps.financeiro.models.conta_bancaria import PlanoContas


CODIGO_VENDAS_PRODUTOS = '3100100001'


def categoria_vendas_produtos(filial):
    """Retorna a categoria operacional padrão usada por vendas e OPs."""
    return (
        PlanoContas.objects.filter(
            empresa_id=filial.empresa_id,
            codigo=CODIGO_VENDAS_PRODUTOS,
            tipo='R',
            ativo=True,
        )
        .select_related('conta_contabil')
        .first()
    )
