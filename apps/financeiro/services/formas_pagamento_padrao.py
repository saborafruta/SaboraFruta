"""
As formas de pagamento que toda filial precisa ter no primeiro dia.

O PDV NAO ABRE SEM FORMA DE PAGAMENTO: sem nenhuma cadastrada, a tela de
finalizacao fica sem botao e a venda nao fecha. Exigir que alguem cadastre
"Dinheiro" a mao antes da primeira venda e' atrito puro -- e o erro nao diz
isso, so' aparece uma lista vazia na hora de receber.
"""
from apps.financeiro.constants.enums import TipoFormaPagamento
from apps.financeiro.models.formas_pagamento import FormaPagamento

# Codigo SEFAZ conforme a tabela de meios de pagamento da NFe/NFCe.
PADRAO = [
    ('Dinheiro', TipoFormaPagamento.DINHEIRO, '01', False),
    ('PIX', TipoFormaPagamento.PIX, '17', False),
    ('Cartão de Crédito', TipoFormaPagamento.CARTAO_CREDITO, '03', True),
    ('Cartão de Débito', TipoFormaPagamento.CARTAO_DEBITO, '04', False),
    ('Transferência', TipoFormaPagamento.TED, '99', False),
    ('Boleto', TipoFormaPagamento.BOLETO, '15', False),
]


def garantir_formas_padrao(filial) -> int:
    """
    Cria as formas que faltam nesta filial. Devolve quantas criou.

    SO' AGE NO VAZIO: se a filial ja' tem qualquer forma propria, quem cuidou
    do cadastro foi uma pessoa, e recriar as padrao aqui traria de volta a
    forma que ela desativou de proposito.
    """
    if filial is None:
        return 0
    if FormaPagamento.objects.filter(filial=filial).exists():
        return 0

    criadas = 0
    for descricao, tipo, codigo_sefaz, gera_parcelas in PADRAO:
        _, criou = FormaPagamento.objects.get_or_create(
            empresa=filial.empresa, filial=filial, descricao=descricao,
            defaults={
                'tipo': tipo,
                'codigo_sefaz': codigo_sefaz,
                'gera_parcelas': gera_parcelas,
                'ativo': True,
            },
        )
        criadas += int(criou)
    return criadas
