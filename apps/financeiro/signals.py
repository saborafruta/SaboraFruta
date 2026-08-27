from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.financeiro.models import (
    ContaReceber, ExtratoBancario, FormaPagamento, PagamentoContaPagar,
)
from apps.financeiro.services.taxas_transacao_service import (
    sincronizar_tarifa_pagamento,
    sincronizar_taxa_transacao,
)
from apps.financeiro.services.conta_bancaria_resolver import vincular_conta_bancaria
from apps.core.models import Filial
from apps.financeiro.services.formas_pagamento_padrao import garantir_formas_padrao
from apps.pdv.models import PagamentoVendaPDV


@receiver(post_save, sender=FormaPagamento)
def vincular_forma_a_conta(sender, instance, **kwargs):
    vincular_conta_bancaria(instance)


@receiver(post_save, sender=ExtratoBancario)
def sincronizar_taxa_extrato(sender, instance, **kwargs):
    sincronizar_taxa_transacao(
        origem="extrato", origem_id=instance.pk, filial=instance.filial,
        data=instance.data_credito or instance.data_lancamento,
        valor=instance.valor_taxa if (instance.valor or 0) > 0 else 0,
        forma_pagamento=instance.forma_pagamento,
        conta_bancaria=instance.conta_bancaria,
    )


@receiver(post_save, sender=ContaReceber)
def sincronizar_taxa_recebimento(sender, instance, **kwargs):
    conta_bancaria = instance.conta_bancaria or vincular_conta_bancaria(
        instance.forma_pagamento,
    )
    sincronizar_taxa_transacao(
        origem="receber", origem_id=instance.pk, filial=instance.filial,
        data=instance.data_liquidacao_prevista or instance.data_pagamento,
        valor=instance.valor_taxa_recebimento,
        forma_pagamento=instance.forma_pagamento,
        conta_bancaria=conta_bancaria,
    )


@receiver(post_save, sender=PagamentoVendaPDV)
def sincronizar_taxa_venda(sender, instance, **kwargs):
    sincronizar_taxa_transacao(
        origem="pdv", origem_id=instance.pk, filial=instance.venda_pdv.filial,
        data=instance.data_liquidacao_prevista,
        valor=instance.valor_taxa,
        forma_pagamento=instance.forma_pagamento,
        conta_bancaria=(
            instance.conta_bancaria
            or vincular_conta_bancaria(instance.forma_pagamento)
        ),
    )


@receiver(post_save, sender=PagamentoContaPagar)
def sincronizar_tarifa_saida(sender, instance, **kwargs):
    sincronizar_tarifa_pagamento(instance)


@receiver(post_save, sender=Filial)
def semear_formas_de_pagamento(sender, instance, created, **kwargs):
    """
    Filial nova ja' nasce podendo receber.

    SEM FORMA DE PAGAMENTO O CAIXA NAO FECHA VENDA: a tela de finalizacao
    fica sem botao, e o erro nao diz isso -- aparece uma lista vazia na hora
    de receber. Exigir que alguem cadastre "Dinheiro" a mao antes da primeira
    venda e' atrito puro.

    NA CRIACAO, e nao a cada consulta: isto morava no `api_estado`, um GET,
    que semeava na primeira visita ao PDV. Escrever num GET fazia a consulta
    de estado depender de gravar, e a mesma verificacao rodava em toda
    abertura de tela.
    """
    if created:
        garantir_formas_padrao(instance)
