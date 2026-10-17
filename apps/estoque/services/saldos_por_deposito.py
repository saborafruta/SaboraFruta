from decimal import Decimal

from apps.estoque.models import Deposito, Estoque


ZERO = Decimal('0')


def anexar_saldos_por_deposito(*, produtos, filial, depositos=None):
    """Anexa uma matriz estável produto × depósito com zeros explícitos."""
    produtos = list(produtos)
    if depositos is None:
        depositos = list(
            Deposito.objects.filter(filial=filial, ativo=True)
            .order_by('-is_padrao', 'nome')
        )
    else:
        depositos = list(depositos)

    produto_ids = [produto.pk for produto in produtos]
    deposito_ids = [deposito.pk for deposito in depositos]
    saldos = {
        (row['produto_id'], row['deposito_id']): row
        for row in Estoque.objects.filter(
            filial=filial,
            produto_id__in=produto_ids,
            deposito_id__in=deposito_ids,
        ).values(
            'produto_id', 'deposito_id', 'quantidade_atual',
            'quantidade_reservada', 'quantidade_disponivel',
        )
    }

    deposito_venda_id = Deposito.venda_id(filial.pk) if depositos else None
    for produto in produtos:
        produto.saldos_depositos = []
        for deposito in depositos:
            saldo = saldos.get((produto.pk, deposito.pk), {})
            produto.saldos_depositos.append({
                'id': deposito.pk,
                'nome': deposito.nome,
                'atual': saldo.get('quantidade_atual', ZERO),
                'reservada': saldo.get('quantidade_reservada', ZERO),
                'disponivel': saldo.get('quantidade_disponivel', ZERO),
                'permite_venda': deposito.permite_venda,
                'origem_pedido': deposito.pk == deposito_venda_id,
            })
    return depositos
