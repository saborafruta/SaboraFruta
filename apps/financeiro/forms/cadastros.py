from django import forms

from apps.financeiro.models import CentroCusto, FormaPagamento, PlanoContas


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
            "ativo": "Ativo",
        }

    def __init__(self, *args, empresa=None, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.empresa = empresa
        self.filial = filial
        self.fields["codigo_sefaz"].required = False
        self.fields["taxa_administrativa"].widget.attrs.setdefault("step", "0.01")
        self.fields["prazo_liquidacao_dias"].widget.attrs.setdefault("min", "0")

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
