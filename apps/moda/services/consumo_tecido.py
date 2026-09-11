"""
Consumo do tecido principal pela grade: metros pelo peso, quando o tecido,
o tipo de peça e a grade têm peso cadastrado (`PesoTecidoGrade`); senão, o
consumo fixo da ficha continua valendo.

O peso NÃO é do produto nem da ficha técnica -- é do tecido, do tipo de
peça e da grade, os três lidos direto do item da OP (`ItemPedidoProducao`),
sem depender de o item estar ligado a um produto do catálogo. Uma camisa
"CONJUNTO (CAMISA + CALÇÃO)" sem cadastro nenhum de produto já usa esta
conta, desde que a malha, o tipo de peça e a grade batam com uma linha
pesada.

Uma conta só, chamada dos três lugares que precisam da mesma resposta — o
painel de necessidade, a reserva automática ao iniciar a produção e a
sugestão de planejado no corte. Duas contas para a mesma pergunta é como
uma delas passa a mentir (mesmo raciocínio de `corte_list.html`).
"""
from __future__ import annotations

from decimal import Decimal

from .op2_estrutura import parse_estrutura_campos, parse_tipo_peca


def tecido_da_malha(filial, observacoes: str):
    """
    A malha que a OP pediu, resolvida para o cadastro de Tecido.

    A OP 2.0 ainda guarda a malha como texto nas observações do item (ver
    `parse_estrutura_campos`), então só liga quando o nome bate exato (sem
    diferenciar maiúsculas) com um Tecido já cadastrado.
    """
    from apps.moda.models import Tecido

    malha = (parse_estrutura_campos(observacoes).get('malha') or '').strip()
    if not malha or filial is None:
        return None
    return Tecido.objects.for_filial(filial).filter(nome__iexact=malha).first()


def consumo_tecido_principal(filial_id, item, tecido, quantidades_por_tamanho) -> Decimal | None:
    """
    Metros do tecido principal para a grade deste item, pelo peso — ou
    `None` quando falta tecido, tipo de peça, grade ou peso cadastrado
    para algum tamanho pedido.

    `item` é o `ItemPedidoProducao`: fornece o tipo de peça (das
    observações) e a grade (`grade_tamanho`) escolhidos na OP, os dois
    independentes de produto -- funciona mesmo em item sem produto de
    catálogo ligado.
    """
    from apps.moda.models import PesoTecidoGrade

    if tecido is None or not tecido.gramatura or not tecido.largura_cm:
        return None
    if not item.grade_tamanho_id or not quantidades_por_tamanho:
        return None
    tipo_peca = parse_tipo_peca(item.observacoes)
    if not tipo_peca:
        return None

    pesos = {
        p.tamanho_id: p.peso_g
        for p in PesoTecidoGrade.objects.filter(
            filial_id=filial_id, tecido=tecido,
            tipo_peca__iexact=tipo_peca, grade_id=item.grade_tamanho_id,
        )
        if p.peso_g is not None
    }
    if not pesos:
        return None

    total_g = Decimal('0')
    for tamanho_id, quantidade in quantidades_por_tamanho.items():
        if not quantidade:
            continue
        peso = pesos.get(tamanho_id)
        if peso is None:
            return None
        total_g += peso * quantidade
    if not total_g:
        return None

    largura_m = tecido.largura_cm / Decimal('100')
    if not largura_m:
        return None
    return (total_g / Decimal(tecido.gramatura) / largura_m).quantize(Decimal('0.0001'))
