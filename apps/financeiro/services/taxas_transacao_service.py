from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Max, Q
from django.utils import timezone

from apps.financeiro.constants.enums import StatusContaPagar
from apps.financeiro.models import (
    ContaPagar,
    PagamentoContaPagar,
    PlanoContabil,
    PlanoContas,
)
from apps.financeiro.services.conta_bancaria_resolver import vincular_conta_bancaria


ZERO = Decimal("0")


def _conta_contabil_taxas(empresa):
    conta = PlanoContabil.objects.filter(
        empresa=empresa,
    ).filter(
        Q(descricao__iexact="Taxas por transacao")
        | Q(classificacao="ITED.TAXAS.TRANSACAO")
    ).first()
    if conta:
        return conta

    pai = PlanoContabil.objects.filter(
        empresa=empresa,
        tipo_conta=PlanoContabil.TipoConta.SINTETICA,
    ).filter(
        Q(descricao__icontains="financeir") | Q(descricao__icontains="despesa")
    ).order_by("-nivel", "ordem").first()
    maximos = PlanoContabil.objects.filter(empresa=empresa).aggregate(
        codigo=Max("codigo_referencia"), ordem=Max("ordem"),
    )
    try:
        with transaction.atomic():
            return PlanoContabil.objects.create(
                empresa=empresa,
                conta_pai=pai,
                codigo_referencia=(maximos["codigo"] or 0) + 1,
                classificacao="ITED.TAXAS.TRANSACAO",
                tipo_conta=PlanoContabil.TipoConta.ANALITICA,
                descricao="Taxas por transacao",
                codigo_dre=getattr(pai, "codigo_dre", "") or "",
                data_inicio=timezone.localdate(),
                nivel=(pai.nivel + 1) if pai else 1,
                ordem=(maximos["ordem"] or 0) + 1,
                origem="Classificacao automatica - iTed",
                ativo=True,
            )
    except IntegrityError:
        return PlanoContabil.objects.get(
            empresa=empresa, classificacao="ITED.TAXAS.TRANSACAO",
        )


def _categoria_taxas(empresa):
    conta_contabil = _conta_contabil_taxas(empresa)

    grupo = PlanoContas.objects.filter(
        empresa=empresa, tipo="D", nivel=1,
        descricao__iexact="Despesas financeiras",
    ).first()
    if not grupo:
        grupo = PlanoContas.objects.create(
            empresa=empresa, codigo="399", descricao="Despesas financeiras",
            tipo="D", nivel=1, aceita_lancamento=False, ativo=True,
        )

    tipo = PlanoContas.objects.filter(
        empresa=empresa, tipo="D", nivel=2, conta_pai=grupo,
        descricao__iexact="Taxas e tarifas",
    ).first()
    if not tipo:
        tipo = PlanoContas.objects.create(
            empresa=empresa, conta_pai=grupo, codigo="39901",
            descricao="Taxas e tarifas", tipo="D", nivel=2,
            aceita_lancamento=False, ativo=True,
        )

    categoria = PlanoContas.objects.filter(
        empresa=empresa, tipo="D", nivel=3, conta_pai=tipo,
        descricao__iexact="Taxas por transacao",
    ).first()
    if not categoria:
        categoria = PlanoContas.objects.create(
            empresa=empresa, conta_pai=tipo, conta_contabil=conta_contabil,
            codigo="3990100001", descricao="Taxas por transacao", tipo="D",
            nivel=3, aceita_lancamento=True, ativo=True,
        )
    elif categoria.conta_contabil_id != conta_contabil.pk:
        categoria.conta_contabil = conta_contabil
        categoria.save(update_fields=["conta_contabil"])
    return categoria


@transaction.atomic
def sincronizar_taxa_transacao(
    *, origem, origem_id, filial, data, valor, forma_pagamento=None,
    conta_bancaria=None,
):
    conta_bancaria = conta_bancaria or vincular_conta_bancaria(forma_pagamento)
    documento_tipo = f"taxa_{origem}"
    existentes = ContaPagar.all_objects.filter(
        filial=filial, documento_tipo=documento_tipo, documento_id=origem_id,
    )
    valor = valor or ZERO
    if valor <= ZERO or not data:
        existentes.delete()
        return None

    categoria = _categoria_taxas(filial.empresa)
    descricao_forma = forma_pagamento.descricao if forma_pagamento else "recebimento"
    descricao = f"Taxa por transacao - {descricao_forma}"
    conta, _ = ContaPagar.all_objects.update_or_create(
        filial=filial,
        documento_tipo=documento_tipo,
        documento_id=origem_id,
        defaults={
            "descricao_despesa": descricao,
            "documento_numero": f"TX-{origem_id}"[:20],
            "valor_original": valor,
            "valor_final": valor,
            "valor_pago": valor,
            "valor_saldo": ZERO,
            "data_emissao": data,
            "data_vencimento": data,
            "data_pagamento": data,
            "data_competencia": data.replace(day=1),
            "forma_pagamento": forma_pagamento,
            "forma_pagamento_prevista": forma_pagamento,
            "conta_bancaria": conta_bancaria,
            "plano_contas": categoria,
            "conta_contabil": categoria.conta_contabil,
            "status": StatusContaPagar.PAGO,
            "observacao": "Taxa retida automaticamente na liquidacao do recebimento.",
            "excluido_em": None,
            "excluido_por": None,
            "motivo_exclusao": "",
        },
    )
    PagamentoContaPagar.objects.update_or_create(
        conta_pagar=conta,
        defaults={
            "filial": filial,
            "data_pagamento": data,
            "valor_pago": valor,
            "forma_pagamento": forma_pagamento,
            "conta_bancaria": conta_bancaria,
            "referencia_pagamento": "Retida na liquidacao do recebimento",
            "observacao": "Sem segunda movimentacao bancaria; o credito ja entrou liquido.",
        },
    )
    return conta
