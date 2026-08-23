from django import forms

from apps.financeiro.constants.enums import TipoFormaPagamento


BANDEIRAS_CARTAO = [
    ("", "Nao informar"),
    ("visa", "Visa"),
    ("mastercard", "Mastercard"),
    ("elo", "Elo"),
    ("amex", "Amex"),
    ("hiper", "Hiper / Hipercard"),
]


class FormaPagamentoCartaoSelect(forms.Select):
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        forma = getattr(value, "instance", None)
        if forma is None:
            return option
        maximo = max((taxa.parcelas for taxa in forma.taxas_parcelamento.all()), default=1)
        option["attrs"]["data-tipo"] = forma.tipo
        option["attrs"]["data-max-parcelas"] = str(
            maximo if forma.tipo == TipoFormaPagamento.CARTAO_CREDITO else 1
        )
        return option


def campo_parcelas():
    return forms.TypedChoiceField(
        choices=[("", "Nao informar")] + [
            (numero, "A vista (1x)" if numero == 1 else f"{numero}x")
            for numero in range(1, 100)
        ],
        coerce=int,
        empty_value=None,
        required=False,
        label="Parcelas da operacao",
    )


def configurar_forma_pagamento(form, queryset):
    form.fields["forma_pagamento"].queryset = queryset.prefetch_related("taxas_parcelamento")
    form.fields["forma_pagamento"].widget = FormaPagamentoCartaoSelect()


def limpar_dados_cartao(form, cleaned):
    forma = cleaned.get("forma_pagamento")
    if not forma or forma.tipo not in {
        TipoFormaPagamento.CARTAO_DEBITO,
        TipoFormaPagamento.CARTAO_CREDITO,
    }:
        cleaned["bandeira"] = ""
        cleaned["numero_parcelas"] = None
        return cleaned

    cleaned["bandeira"] = forma.normalizar_bandeira(cleaned.get("bandeira", ""))
    if forma.tipo == TipoFormaPagamento.CARTAO_DEBITO:
        cleaned["numero_parcelas"] = 1
        return cleaned

    parcelas = cleaned.get("numero_parcelas")
    maximo = max((taxa.parcelas for taxa in forma.taxas_parcelamento.all()), default=1)
    if parcelas and parcelas > maximo:
        form.add_error("numero_parcelas", f"Esta forma aceita no maximo {maximo} parcelas.")
    return cleaned
