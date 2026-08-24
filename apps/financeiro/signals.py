from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.financeiro.models import ContaReceber, ExtratoBancario
from apps.financeiro.services.taxas_transacao_service import sincronizar_taxa_transacao
from apps.pdv.models import PagamentoVendaPDV


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
    sincronizar_taxa_transacao(
        origem="receber", origem_id=instance.pk, filial=instance.filial,
        data=instance.data_liquidacao_prevista or instance.data_pagamento,
        valor=instance.valor_taxa_recebimento,
        forma_pagamento=instance.forma_pagamento,
        conta_bancaria=instance.conta_bancaria,
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
            or instance.forma_pagamento.conta_bancaria_padrao
        ),
    )
