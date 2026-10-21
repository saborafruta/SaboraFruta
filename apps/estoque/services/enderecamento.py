from decimal import Decimal

from apps.estoque.models import SaldoEndereco


ZERO = Decimal('0')


def localizacoes_por_produto_deposito(*, produto_ids, filial):
    """Retorna os enderecos com saldo, prontos para web e API mobile."""
    resultado = {}
    if not produto_ids:
        return resultado
    saldos = (
        SaldoEndereco.objects
        .filter(
            estoque__filial=filial,
            estoque__produto_id__in=produto_ids,
            endereco__ativo=True,
            quantidade__gt=0,
        )
        .select_related('estoque', 'endereco')
        .order_by(
            'estoque__produto_id', 'estoque__deposito_id',
            'prioridade_separacao', 'endereco__codigo', 'pk',
        )
    )
    for saldo in saldos:
        resultado.setdefault(
            (saldo.estoque.produto_id, saldo.estoque.deposito_id), [],
        ).append({
            'id': saldo.endereco_id,
            'codigo': saldo.endereco.codigo,
            'descricao': saldo.endereco.descricao,
            'quantidade': saldo.quantidade,
            'prioridade': saldo.prioridade_separacao,
        })
    return resultado


def baixar_saldos_enderecados(*, estoque, quantidade):
    """Consome automaticamente os enderecos pela prioridade de separacao.

    Entrada sem endereco permanece como saldo nao enderecado. Na saida, apenas
    o que estava enderecado e baixado; assim a soma nunca fica maior que o
    saldo fisico do deposito e cadastros antigos continuam funcionando.
    """
    restante = Decimal(quantidade or 0)
    if restante <= 0:
        return
    saldos = list(
        SaldoEndereco.objects.select_for_update()
        .filter(estoque=estoque, quantidade__gt=0)
        .select_related('endereco')
        .order_by('prioridade_separacao', 'endereco__codigo', 'pk')
    )
    for saldo in saldos:
        if restante <= 0:
            break
        baixa = min(restante, saldo.quantidade)
        saldo.quantidade -= baixa
        saldo.save(update_fields=['quantidade', 'updated_at'])
        restante -= baixa
