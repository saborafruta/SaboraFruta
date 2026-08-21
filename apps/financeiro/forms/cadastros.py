from django import forms

from apps.financeiro.models import CentroCusto, ContaBancaria, FormaPagamento, PlanoContas


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

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = ContaBancaria.objects.none()
        if filial:
            qs = ContaBancaria.objects.for_filial(filial).filter(ativo=True).order_by("descricao", "banco_nome")
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
        if tipo == self.TIPO_DEBITO and not origem:
            self.add_error("conta_origem", "Escolha a conta de onde o valor vai sair.")
        if tipo == self.TIPO_TRANSFERENCIA:
            if not origem:
                self.add_error("conta_origem", "Escolha a conta de origem.")
            if not destino:
                self.add_error("conta_destino", "Escolha a conta de destino.")
            if origem and destino and origem.pk == destino.pk:
                self.add_error("conta_destino", "A conta de destino deve ser diferente da origem.")
        return cleaned


class DirecionarContaBancariaForm(forms.Form):
    conta_bancaria = forms.ModelChoiceField(queryset=ContaBancaria.objects.none())

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        if filial:
            self.fields["conta_bancaria"].queryset = (
                ContaBancaria.objects.for_filial(filial)
                .filter(ativo=True)
                .order_by("descricao", "banco_nome")
            )


class EditarMovimentoBancarioForm(forms.Form):
    conta_bancaria = forms.ModelChoiceField(queryset=ContaBancaria.objects.none(), label="Conta")
    data_lancamento = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}), label="Data")
    valor = forms.DecimalField(max_digits=14, decimal_places=2, label="Valor")
    historico = forms.CharField(max_length=200, label="Historico")
    documento = forms.CharField(max_length=30, required=False, label="Documento")
    justificativa = forms.CharField(max_length=300, label="Motivo da alteracao")

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        if filial:
            self.fields["conta_bancaria"].queryset = (
                ContaBancaria.objects.for_filial(filial)
                .filter(ativo=True)
                .order_by("descricao", "banco_nome")
            )
        self.fields["valor"].widget.attrs.update({"step": "0.01", "inputmode": "decimal"})


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
            "taxa_administrativa",
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
            "taxa_administrativa": "Taxa administrativa (%)",
            "conta_bancaria_padrao": "Conta bancaria padrao",
            "ativo": "Ativo",
        }

    def __init__(self, *args, empresa=None, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.empresa = empresa
        self.filial = filial
        self.fields["codigo_sefaz"].required = False
        self.fields["conta_bancaria_padrao"].required = False
        self.fields["taxa_administrativa"].widget.attrs.setdefault("step", "0.01")
        self.fields["prazo_liquidacao_dias"].widget.attrs.setdefault("min", "0")
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

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.empresa = self.empresa
        instance.filial = self.filial
        if commit:
            instance.save()
            self.save_m2m()
        return instance
