"""
Consumo do tecido principal pela grade: metros pelo peso, quando a ficha e
o tecido têm os dados; senão, o consumo fixo da ficha continua valendo.

Uma conta só, chamada dos três lugares que precisam da mesma resposta — o
painel de necessidade, a reserva automática ao iniciar a produção e a
sugestão de planejado no corte. Duas contas para a mesma pergunta é como
uma delas passa a mentir (mesmo raciocínio de `corte_list.html`).
"""
from __future__ import annotations

from decimal import Decimal

from .op2_estrutura import parse_estrutura_campos


def tecido_da_malha(filial, observacoes: str):
    """
    A malha que a OP pediu, resolvida para o cadastro de Tecido.

    Mesma leitura que `OrdemMaterialSugeridoView` já faz para sugerir
    Tecido/Cor no corte: a OP 2.0 ainda guarda a malha como texto nas
    observações do item, então só liga quando o nome bate exato (sem
    diferenciar maiúsculas) com um Tecido já cadastrado.
    """
    from apps.moda.models import Tecido

    malha = (parse_estrutura_campos(observacoes).get('malha') or '').strip()
    if not malha or filial is None:
        return None
    return Tecido.objects.for_filial(filial).filter(nome__iexact=malha).first()


def consumo_tecido_principal(ficha, produto, tecido, quantidades_por_tamanho) -> Decimal | None:
    """
    Metros do tecido principal para esta grade, pelo peso — ou `None`
    quando falta ficha, tecido, grade do produto, ou peso cadastrado.
    """
    if ficha is None or tecido is None or not quantidades_por_tamanho:
        return None
    grade_id = getattr(produto, 'grade_id', None)
    if not grade_id:
        return None
    return ficha.consumo_tecido_por_peso(tecido, grade_id, quantidades_por_tamanho)
