from django import forms
from django.db.models import Q

from apps.financeiro.models import CentroCusto, ContaBancaria, FormaPagamento, PlanoContas
from apps.financeiro.forms.plano_contas import CategoriaFinanceiraChoiceField
from apps.financeiro.forms.cartao import campo_parcelas, configurar_forma_pagamento, limpar_dados_cartao


class ContaBancariaForm(forms.ModelForm):
    class Meta:
        model = ContaBancaria
        fields = [
            "descricao",
            "banco_codigo",
            "banco_nome",
            "agencia",
            "agencia_digito",
            "conta",
            "conta_digito",
            "tipo_conta",
            "saldo_inicial",
            "chave_pix",
            "tipo_chave_pix",
            "ativo",
        ]
        labels = {
            "descricao": "Apelido da conta",
            "banco_codigo": "Codigo do banco",
            "banco_nome": "Banco",
            "agencia": "Agencia",
            "agencia_digito": "Digito",
            "conta": "Conta",
            "conta_digito": "Digito",
            "tipo_conta": "Tipo",
            "saldo_inicial": "Saldo inicial",
            "chave_pix": "Chave Pix",
            "tipo_chave_pix": "Tipo da chave Pix",
            "ativo": "Ativa",
        }

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.filial = filial
        self.fields["descricao"].required = True
        self.fields["banco_codigo"].required = False
        self.fields["saldo_inicial"].widget.attrs.setdefault("step", "0.01")
        self.fields["saldo_inicial"].widget.attrs.setdefault("inputmode", "decimal")

    def clean(self):
        cleaned = super().clean()
        agencia = (cleaned.get("agencia") or "").strip()
        conta = (cleaned.get("conta") or "").strip()
        banco_codigo = (cleaned.get("banco_codigo") or "").strip()
        if agencia and conta and banco_codigo and self.filial:
            qs = ContaBancaria.objects.filter(
                filial=self.filial,
                banco_codigo__iexact=banco_codigo,
                agencia__iexact=agencia,
                conta__iexact=conta,
            )
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise forms.ValidationError("Ja existe uma conta com este banco, agencia e numero.")
        return cleaned

    def save(self, commit=True):
        criando = self.instance.pk is None
        instance = super().save(commit=False)
        instance.filial = self.filial
        if criando:
            instance.saldo_atual = instance.saldo_inicial
        if commit:
            instance.save()
            self.save_m2m()
        return instance


BANDEIRAS_CARTAO = [
    ("", "Não informar"),
    ("visa", "Visa"),
    ("mastercard", "Mastercard"),
    ("elo", "Elo"),
    ("amex", "Amex"),
    ("hiper", "Hiper / Hipercard"),
]


class MovimentoContaBancariaForm(forms.Form):
    TIPO_CREDITO = "credito"
    TIPO_DEBITO = "debito"
    TIPO_TRANSFERENCIA = "transferencia"
    TIPO_CHOICES = [
        (TIPO_CREDITO, "Adicionar valor"),
        (TIPO_DEBITO, "Remover valor"),
        (TIPO_TRANSFERENCIA, "Transferencia entre contas"),
    ]

    tipo = forms.ChoiceField(choices=TIPO_CHOICES)
    conta_origem = forms.ModelChoiceField(queryset=ContaBancaria.objects.none(), required=False)
    conta_destino = forms.ModelChoiceField(queryset=ContaBancaria.objects.none(), required=False)
    data_lancamento = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    valor = forms.DecimalField(max_digits=14, decimal_places=2, min_value=0.01)
    historico = forms.CharField(max_length=200, required=False)
    documento = forms.CharField(max_length=30, required=False)
    forma_pagamento = forms.ModelChoiceField(
        queryset=FormaPagamento.objects.none(), required=False, label="Forma de pagamento",
    )
    bandeira = forms.ChoiceField(choices=BANDEIRAS_CARTAO, required=False, label="Bandeira do cartão")
    numero_parcelas = campo_parcelas()
    plano_contas = CategoriaFinanceiraChoiceField(
        queryset=PlanoContas.objects.none(), required=False, label="Classificacao financeira",
    )

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = ContaBancaria.objects.none()
        if filial:
            qs = ContaBancaria.objects.for_filial(filial).filter(ativo=True).order_by("descricao", "banco_nome")
            configurar_forma_pagamento(self, FormaPagamento.objects.filter(
                empresa=filial.empresa, ativo=True,
            ).filter(Q(filial=filial) | Q(filial__isnull=True)).order_by("descricao"))
            self.fields["plano_contas"].queryset = (
                PlanoContas.objects
                .filter(
                    empresa=filial.empresa, ativo=True,
                    aceita_lancamento=True, nivel=3,
                )
                .select_related("conta_pai__conta_pai", "conta_contabil")
                .order_by("codigo")
            )
        self.fields["conta_origem"].queryset = qs
        self.fields["conta_destino"].queryset = qs
        self.fields["valor"].widget.attrs.setdefault("step", "0.01")
        self.fields["valor"].widget.attrs.setdefault("inputmode", "decimal")

    def clean(self):
        cleaned = super().clean()
        tipo = cleaned.get("tipo")
        origem = cleaned.get("conta_origem")
        destino = cleaned.get("conta_destino")
        if tipo == self.TIPO_CREDITO and not destino:
            self.add_error("conta_destino", "Escolha a conta que vai receber o valor.")
        categoria = cleaned.get("plano_contas")
        if tipo in {self.TIPO_CREDITO, self.TIPO_DEBITO} and self.fields["plano_contas"].queryset.exists() and not categoria:
            self.add_error("plano_contas", "Escolha a classificacao financeira.")
        if categoria and tipo == self.TIPO_CREDITO and categoria.tipo != "R":
            self.add_error("plano_contas", "Escolha uma categoria de receita.")
        if categoria and tipo == self.TIPO_DEBITO and categoria.tipo != "D":
            self.add_error("plano_contas", "Escolha uma categoria de despesa.")
        if tipo == self.TIPO_DEBITO and not origem:
            self.add_error("conta_origem", "Escolha a conta de onde o valor vai sair.")
        if tipo == self.TIPO_TRANSFERENCIA:
            if not origem:
                self.add_error("conta_origem", "Escolha a conta de origem.")
            if not destino:
                self.add_error("conta_destino", "Escolha a conta de destino.")
            if origem and destino and origem.pk == destino.pk:
                self.add_error("conta_destino", "A conta de destino deve ser diferente da origem.")
        return limpar_dados_cartao(self, cleaned)


class ContaBancariaChoiceField(forms.ModelChoiceField):
    """Mostra o apelido operacional da conta nos seletores financeiros."""

    def label_from_instance(self, conta):
        apelido = (conta.descricao or "").strip()
        banco = (conta.banco_nome or "").strip()
        if apelido and banco and apelido.casefold() != banco.casefold():
            return f"{apelido} ({banco})"
        return apelido or banco or f"Conta #{conta.pk}"


class DirecionarContaBancariaForm(forms.Form):
    conta_bancaria = ContaBancariaChoiceField(queryset=ContaBancaria.objects.none())
    justificativa = forms.CharField(max_length=300, required=False, label="Motivo da alteracao")

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        if filial:
            self.fields["conta_bancaria"].queryset = (
                ContaBancaria.objects.for_filial(filial)
                .filter(ativo=True)
                .order_by("descricao", "banco_nome")
            )


class EditarMovimentoBancarioForm(forms.Form):
    conta_bancaria = ContaBancariaChoiceField(queryset=ContaBancaria.objects.none(), label="Conta")
    data_lancamento = forms.DateField(
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
        label="Data",
    )
    valor = forms.DecimalField(max_digits=14, decimal_places=2, min_value=0.01, label="Valor")
    historico = forms.CharField(max_length=200, label="Historico")
    documento = forms.CharField(max_length=30, required=False, label="Documento")
    forma_pagamento = forms.ModelChoiceField(
        queryset=FormaPagamento.objects.none(), required=False, label="Forma de pagamento",
    )
    bandeira = forms.ChoiceField(choices=BANDEIRAS_CARTAO, required=False, label="Bandeira do cartão")
    numero_parcelas = campo_parcelas()
    plano_contas = CategoriaFinanceiraChoiceField(
        queryset=PlanoContas.objects.none(), required=False, label="Classificacao financeira",
    )
    justificativa = forms.CharField(max_length=300, label="Motivo da alteracao")

    def __init__(self, *args, filial=None, natureza="entrada", **kwargs):
        super().__init__(*args, **kwargs)
        self.natureza = natureza
        tipo_categoria = "D" if natureza == "saida" else "R"
        self.fields["plano_contas"].label = (
            "Classificacao da despesa" if natureza == "saida" else "Classificacao da receita"
        )
        if filial:
            self.fields["conta_bancaria"].queryset = (
                ContaBancaria.objects.for_filial(filial)
                .filter(ativo=True)
                .order_by("descricao", "banco_nome")
            )
            configurar_forma_pagamento(self, FormaPagamento.objects.filter(
                empresa=filial.empresa, ativo=True,
            ).filter(Q(filial=filial) | Q(filial__isnull=True)).order_by("descricao"))
            self.fields["plano_contas"].queryset = (
                PlanoContas.objects
                .filter(
                    empresa=filial.empresa, tipo=tipo_categoria, ativo=True,
                    aceita_lancamento=True, nivel=3,
                )
                .select_related("conta_pai__conta_pai", "conta_contabil")
                .order_by("codigo")
            )
        self.fields["valor"].widget.attrs.update({"step": "0.01", "inputmode": "decimal"})

    def clean(self):
        cleaned = super().clean()
        valor = cleaned.get("valor")
        if valor is not None and self.natureza == "saida":
            cleaned["valor"] = -abs(valor)
        if valor and self.fields["plano_contas"].queryset.exists() and not cleaned.get("plano_contas"):
            self.add_error("plano_contas", f"Escolha a classificacao da {self.natureza}.")
        return limpar_dados_cartao(self, cleaned)


class EditarEntradaFinanceiraForm(forms.Form):
    valor = forms.DecimalField(max_digits=14, decimal_places=2, min_value=0.01, label="Valor bruto")
    forma_pagamento = forms.ModelChoiceField(
        queryset=FormaPagamento.objects.none(), label="Forma de pagamento",
    )
    conta_bancaria = ContaBancariaChoiceField(
        queryset=ContaBancaria.objects.none(), label="Conta bancaria",
    )
    bandeira = forms.ChoiceField(choices=BANDEIRAS_CARTAO, required=False, label="Bandeira do cartão")
    numero_parcelas = campo_parcelas()
    data_entrada = forms.DateField(
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
        label="Data da entrada no caixa",
    )
    descricao = forms.CharField(max_length=200, required=False, label="Descricao")
    plano_contas = CategoriaFinanceiraChoiceField(
        queryset=PlanoContas.objects.none(), required=False, label="Classificacao da entrada",
    )
    justificativa = forms.CharField(
        max_length=300, label="Motivo da alteracao",
        widget=forms.Textarea(attrs={"rows": 2}),
    )

    def __init__(self, *args, filial=None, origem=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.origem = origem
        if filial:
            self.fields["conta_bancaria"].queryset = (
                ContaBancaria.objects.for_filial(filial)
                .filter(ativo=True)
                .order_by("descricao", "banco_nome")
            )
            configurar_forma_pagamento(self, FormaPagamento.objects.filter(
                empresa=filial.empresa, ativo=True,
            ).filter(Q(filial=filial) | Q(filial__isnull=True)).order_by("descricao"))
            self.fields["plano_contas"].queryset = (
                PlanoContas.objects
                .filter(
                    empresa=filial.empresa, tipo="R", ativo=True,
                    aceita_lancamento=True, nivel=3,
                )
                .select_related("conta_pai__conta_pai", "conta_contabil")
                .order_by("codigo")
            )
        self.fields["valor"].widget.attrs.update({"step": "0.01", "inputmode": "decimal"})
        if origem != "manual":
            self.fields.pop("descricao")
        if origem == "venda":
            self.fields.pop("plano_contas")

    def clean(self):
        cleaned = super().clean()
        if "plano_contas" in self.fields and self.fields["plano_contas"].queryset.exists() and not cleaned.get("plano_contas"):
            self.add_error("plano_contas", "Escolha a classificacao da entrada.")
        return limpar_dados_cartao(self, cleaned)


class CentroCustoForm(forms.ModelForm):
    class Meta:
        model = CentroCusto
        fields = ["codigo", "nome", "descricao", "ativo"]
        labels = {
            "codigo": "Código",
            "nome": "Nome",
            "descricao": "Descrição",
            "ativo": "Ativo",
        }
        widgets = {
            "descricao": forms.TextInput(),
        }

    def __init__(self, *args, empresa=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.empresa = empresa

    def clean_codigo(self):
        codigo = (self.cleaned_data.get("codigo") or "").strip()
        qs = CentroCusto.objects.filter(empresa=self.empresa, codigo__iexact=codigo)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if self.empresa and qs.exists():
            raise forms.ValidationError("Já existe centro de custo com este código.")
        return codigo


class PlanoContasDespesaForm(forms.ModelForm):
    class Meta:
        model = PlanoContas
        fields = ["conta_pai", "codigo", "descricao", "aceita_lancamento", "ativo"]
        labels = {
            "conta_pai": "Conta pai",
            "codigo": "Código",
            "descricao": "Descrição",
            "aceita_lancamento": "Aceita lançamento",
            "ativo": "Ativo",
        }

    def __init__(self, *args, empresa=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.empresa = empresa
        qs = PlanoContas.objects.none()
        if empresa:
            qs = PlanoContas.objects.filter(empresa=empresa, tipo="D", ativo=True).order_by("codigo")
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk).exclude(conta_pai=self.instance)
        self.fields["conta_pai"].queryset = qs
        self.fields["conta_pai"].required = False
        self.fields["conta_pai"].empty_label = "Sem conta pai, criar categoria"

    def clean_codigo(self):
        codigo = (self.cleaned_data.get("codigo") or "").strip()
        qs = PlanoContas.objects.filter(empresa=self.empresa, codigo__iexact=codigo, tipo="D")
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if self.empresa and qs.exists():
            raise forms.ValidationError("Já existe despesa com este código.")
        return codigo

    def clean(self):
        cleaned = super().clean()
        conta_pai = cleaned.get("conta_pai")
        if conta_pai and conta_pai.nivel >= 3:
            raise forms.ValidationError("Tipo de despesa é o terceiro nível. Escolha uma categoria ou subcategoria como pai.")
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.empresa = self.empresa
        instance.tipo = "D"
        instance.nivel = (instance.conta_pai.nivel + 1) if instance.conta_pai_id else 1
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class FormaPagamentoForm(forms.ModelForm):
    class Meta:
        model = FormaPagamento
        fields = [
            "descricao",
            "tipo",
            "codigo_sefaz",
            "requer_tef",
            "gera_parcelas",
            "movimenta_caixa",
            "prazo_liquidacao_dias",
            "prazo_compensacao_dias_uteis",
            "taxa_administrativa",
            "taxa_fixa",
            "conta_bancaria_padrao",
            "ativo",
        ]
        labels = {
            "descricao": "Descrição",
            "tipo": "Tipo",
            "codigo_sefaz": "Código SEFAZ",
            "requer_tef": "Usa TEF",
            "gera_parcelas": "Gera parcelas",
            "movimenta_caixa": "Movimenta o caixa",
            "prazo_liquidacao_dias": "Liquidação em dias",
            "prazo_compensacao_dias_uteis": "Compensacao bancaria (dias uteis)",
            "taxa_administrativa": "Taxa administrativa (%)",
            "taxa_fixa": "Taxa fixa por transacao (R$)",
            "conta_bancaria_padrao": "Conta bancaria padrao",
            "ativo": "Ativo",
        }

    def __init__(self, *args, empresa=None, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.empresa = empresa
        self.filial = filial
        self.fields["codigo_sefaz"].required = False
        self.fields["conta_bancaria_padrao"].required = False
        self.fields["conta_bancaria_padrao"].help_text = (
            "Entradas, previsoes e taxas desta forma serao direcionadas automaticamente para esta conta."
        )
        self.fields["taxa_administrativa"].widget.attrs.setdefault("step", "0.01")
        self.fields["taxa_fixa"].widget.attrs.update({"step": "0.01", "min": "0", "inputmode": "decimal"})
        self.fields["prazo_liquidacao_dias"].widget.attrs.setdefault("min", "0")
        self.fields["prazo_compensacao_dias_uteis"].required = False
        self.fields["prazo_compensacao_dias_uteis"].initial = 0
        self.fields["prazo_compensacao_dias_uteis"].widget.attrs.setdefault("min", "0")
        if filial:
            self.fields["conta_bancaria_padrao"].queryset = (
                ContaBancaria.objects.for_filial(filial).filter(ativo=True).order_by("descricao", "banco_nome")
            )

    def clean_descricao(self):
        descricao = (self.cleaned_data.get("descricao") or "").strip()
        qs = FormaPagamento.objects.filter(filial=self.filial, descricao__iexact=descricao)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if self.filial and qs.exists():
            raise forms.ValidationError("Já existe forma de pagamento com esta descrição nesta filial.")
        return descricao

    def clean_taxa_administrativa(self):
        taxa = self.cleaned_data.get("taxa_administrativa") or 0
        if taxa < 0 or taxa > 100:
            raise forms.ValidationError("Informe uma taxa entre 0% e 100%.")
        return taxa

    def clean_taxa_fixa(self):
        taxa = self.cleaned_data.get("taxa_fixa") or 0
        if taxa < 0:
            raise forms.ValidationError("A taxa fixa nao pode ser negativa.")
        return taxa

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.empresa = self.empresa
        instance.filial = self.filial
        if commit:
            instance.save()
            self.save_m2m()
        return instance
