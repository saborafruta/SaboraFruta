from datetime import timedelta

from django import forms
from django.utils import timezone

from apps.cadastros.models import Cliente, Fornecedor, Motorista, Transportadora, Veiculo
from apps.logistica.models import (
    CTe,
    DocumentoCTe,
    DocumentoMDFe,
    DocumentoManifestoCarga,
    ItemOrdemColeta,
    ItemPedidoExpedicao,
    ItemRomaneioCarga,
    MDFe,
    ManifestoCarga,
    OrdemColeta,
    PedidoExpedicao,
    RomaneioCarga,
)


BASE_INPUT_CLASS = "form-input w-full"


class RomaneioCargaForm(forms.ModelForm):
    class Meta:
        model = RomaneioCarga
        fields = [
            "numero",
            "data",
            "status",
            "transportadora",
            "motorista_nome",
            "motorista_documento",
            "veiculo_placa",
            "veiculo_descricao",
            "origem",
            "destino_rota",
            "observacao",
        ]
        widgets = {
            "data": forms.DateInput(attrs={"type": "date"}),
            "observacao": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["transportadora"].queryset = Transportadora.objects.for_filial(filial).filter(ativo=True)
        self.fields["transportadora"].required = False
        for field in self.fields.values():
            field.widget.attrs["class"] = BASE_INPUT_CLASS


class ItemRomaneioCargaForm(forms.ModelForm):
    endereco = forms.CharField(label="Endereco", required=False)
    numero_endereco = forms.CharField(label="Numero", required=False)
    bairro = forms.CharField(label="Bairro", required=False)
    cidade = forms.CharField(label="Cidade", required=False)
    uf = forms.CharField(label="UF", required=False, max_length=2)

    class Meta:
        model = ItemRomaneioCarga
        fields = [
            "ordem",
            "cliente_nome",
            "documento",
            "status_entrega",
            "volumes",
            "peso_kg",
            "valor",
            "observacao",
        ]
        widgets = {
            "observacao": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        instance = kwargs.get("instance")
        initial = kwargs.setdefault("initial", {})
        if instance and instance.endereco_entrega:
            initial.update({
                "endereco": instance.endereco_entrega.get("endereco", ""),
                "numero_endereco": instance.endereco_entrega.get("numero", ""),
                "bairro": instance.endereco_entrega.get("bairro", ""),
                "cidade": instance.endereco_entrega.get("cidade", ""),
                "uf": instance.endereco_entrega.get("uf", ""),
            })
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = BASE_INPUT_CLASS

    def save(self, commit=True):
        item = super().save(commit=False)
        item.endereco_entrega = {
            "endereco": self.cleaned_data.get("endereco", ""),
            "numero": self.cleaned_data.get("numero_endereco", ""),
            "bairro": self.cleaned_data.get("bairro", ""),
            "cidade": self.cleaned_data.get("cidade", ""),
            "uf": self.cleaned_data.get("uf", ""),
        }
        if commit:
            item.save()
        return item


class OrdemColetaForm(forms.ModelForm):
    coleta_cep = forms.CharField(label="CEP", required=False, max_length=9)
    coleta_endereco = forms.CharField(label="Endereco de coleta", required=False)
    coleta_numero = forms.CharField(label="Numero", required=False)
    coleta_bairro = forms.CharField(label="Bairro", required=False)
    coleta_cidade = forms.CharField(label="Cidade", required=False)
    coleta_uf = forms.CharField(label="UF", required=False, max_length=2)
    entrega_cep = forms.CharField(label="CEP", required=False, max_length=9)
    entrega_endereco = forms.CharField(label="Endereco de entrega", required=False)
    entrega_numero = forms.CharField(label="Numero", required=False)
    entrega_bairro = forms.CharField(label="Bairro", required=False)
    entrega_cidade = forms.CharField(label="Cidade", required=False)
    entrega_uf = forms.CharField(label="UF", required=False, max_length=2)

    class Meta:
        model = OrdemColeta
        fields = [
            "numero",
            "data_solicitacao",
            "data_coleta_prevista",
            "data_coleta_realizada",
            "status",
            "tipo_solicitante",
            "cliente",
            "fornecedor",
            "transportadora",
            "romaneio",
            "solicitante_nome",
            "contato_nome",
            "contato_telefone",
            "motorista_nome",
            "veiculo_placa",
            "observacao",
        ]
        widgets = {
            "data_solicitacao": forms.DateInput(attrs={"type": "date"}),
            "data_coleta_prevista": forms.DateInput(attrs={"type": "date"}),
            "data_coleta_realizada": forms.DateInput(attrs={"type": "date"}),
            "observacao": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, filial=None, **kwargs):
        instance = kwargs.get("instance")
        initial = kwargs.setdefault("initial", {})
        if instance:
            coleta = instance.endereco_coleta or {}
            entrega = instance.endereco_entrega or {}
            initial.update({
                "coleta_cep": coleta.get("cep", ""),
                "coleta_endereco": coleta.get("endereco", ""),
                "coleta_numero": coleta.get("numero", ""),
                "coleta_bairro": coleta.get("bairro", ""),
                "coleta_cidade": coleta.get("cidade", ""),
                "coleta_uf": coleta.get("uf", ""),
                "entrega_cep": entrega.get("cep", ""),
                "entrega_endereco": entrega.get("endereco", ""),
                "entrega_numero": entrega.get("numero", ""),
                "entrega_bairro": entrega.get("bairro", ""),
                "entrega_cidade": entrega.get("cidade", ""),
                "entrega_uf": entrega.get("uf", ""),
            })
        super().__init__(*args, **kwargs)
        self.fields["cliente"].queryset = Cliente.objects.for_filial(filial).filter(ativo=True)
        self.fields["fornecedor"].queryset = Fornecedor.objects.for_filial(filial).filter(ativo=True)
        self.fields["transportadora"].queryset = Transportadora.objects.for_filial(filial).filter(ativo=True)
        self.fields["romaneio"].queryset = RomaneioCarga.objects.for_filial(filial).exclude(
            status__in=[RomaneioCarga.Status.ENTREGUE, RomaneioCarga.Status.CANCELADO]
        )
        for nome in ("cliente", "fornecedor", "transportadora", "romaneio"):
            self.fields[nome].required = False
        for field in self.fields.values():
            field.widget.attrs["class"] = BASE_INPUT_CLASS

    def save(self, commit=True):
        ordem = super().save(commit=False)
        ordem.endereco_coleta = {
            "cep": self.cleaned_data.get("coleta_cep", ""),
            "endereco": self.cleaned_data.get("coleta_endereco", ""),
            "numero": self.cleaned_data.get("coleta_numero", ""),
            "bairro": self.cleaned_data.get("coleta_bairro", ""),
            "cidade": self.cleaned_data.get("coleta_cidade", ""),
            "uf": self.cleaned_data.get("coleta_uf", ""),
        }
        ordem.endereco_entrega = {
            "cep": self.cleaned_data.get("entrega_cep", ""),
            "endereco": self.cleaned_data.get("entrega_endereco", ""),
            "numero": self.cleaned_data.get("entrega_numero", ""),
            "bairro": self.cleaned_data.get("entrega_bairro", ""),
            "cidade": self.cleaned_data.get("entrega_cidade", ""),
            "uf": self.cleaned_data.get("entrega_uf", ""),
        }
        if commit:
            ordem.save()
        return ordem


class ItemOrdemColetaForm(forms.ModelForm):
    class Meta:
        model = ItemOrdemColeta
        fields = ["descricao", "quantidade", "unidade", "volumes", "peso_kg", "valor", "observacao"]
        widgets = {
            "observacao": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = BASE_INPUT_CLASS


class ManifestoCargaForm(forms.ModelForm):
    class Meta:
        model = ManifestoCarga
        fields = [
            "numero",
            "data_emissao",
            "data_saida",
            "status",
            "modal",
            "romaneio",
            "transportadora",
            "motorista_nome",
            "motorista_documento",
            "veiculo_placa",
            "veiculo_descricao",
            "cidade_origem",
            "uf_origem",
            "cidade_destino",
            "uf_destino",
            "percurso",
            "observacao",
        ]
        widgets = {
            "data_emissao": forms.DateInput(attrs={"type": "date"}),
            "data_saida": forms.DateInput(attrs={"type": "date"}),
            "percurso": forms.Textarea(attrs={"rows": 2}),
            "observacao": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["romaneio"].queryset = RomaneioCarga.objects.for_filial(filial).exclude(
            status__in=[RomaneioCarga.Status.ENTREGUE, RomaneioCarga.Status.CANCELADO]
        )
        self.fields["transportadora"].queryset = Transportadora.objects.for_filial(filial).filter(ativo=True)
        self.fields["romaneio"].required = False
        self.fields["transportadora"].required = False
        for field in self.fields.values():
            field.widget.attrs["class"] = BASE_INPUT_CLASS


class CTeForm(forms.ModelForm):
    class Meta:
        model = CTe
        fields = [
            "numero",
            "numero_cte",
            "serie",
            "data_emissao",
            "data_saida",
            "status",
            "modal",
            "tipo_cte",
            "cfop",
            "natureza_operacao",
            "transportadora",
            "tomador",
            "remetente_nome",
            "remetente_documento",
            "destinatario_nome",
            "destinatario_documento",
            "cidade_origem",
            "uf_origem",
            "cidade_destino",
            "uf_destino",
            "percurso",
            "motorista_nome",
            "motorista_documento",
            "veiculo_placa",
            "veiculo_descricao",
            "valor_frete",
            "valor_pedagio",
            "valor_outros",
            "chave_acesso",
            "protocolo_autorizacao",
            "data_autorizacao",
            "observacao",
        ]
        widgets = {
            "data_emissao": forms.DateInput(attrs={"type": "date"}),
            "data_saida": forms.DateInput(attrs={"type": "date"}),
            "data_autorizacao": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "percurso": forms.Textarea(attrs={"rows": 2}),
            "observacao": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["transportadora"].queryset = Transportadora.objects.for_filial(filial).filter(ativo=True)
        self.fields["transportadora"].required = False
        for field in self.fields.values():
            field.widget.attrs["class"] = BASE_INPUT_CLASS


class DocumentoCTeForm(forms.ModelForm):
    class Meta:
        model = DocumentoCTe
        fields = [
            "tipo_documento",
            "numero_documento",
            "serie",
            "chave_acesso",
            "emitente_nome",
            "volumes",
            "peso_kg",
            "valor",
            "observacao",
        ]
        widgets = {
            "observacao": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = BASE_INPUT_CLASS


class DocumentoManifestoCargaForm(forms.ModelForm):
    class Meta:
        model = DocumentoManifestoCarga
        fields = [
            "tipo_documento",
            "numero_documento",
            "serie",
            "chave_acesso",
            "remetente_nome",
            "destinatario_nome",
            "cidade_origem",
            "uf_origem",
            "cidade_destino",
            "uf_destino",
            "volumes",
            "peso_kg",
            "valor",
            "observacao",
        ]
        widgets = {
            "observacao": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = BASE_INPUT_CLASS


# ── OMS ──────────────────────────────────────────────────────────────────────

class PedidoExpedicaoForm(forms.ModelForm):
    entrega_cep      = forms.CharField(label="CEP", required=False, max_length=9)
    entrega_endereco = forms.CharField(label="Endereço", required=False)
    entrega_numero   = forms.CharField(label="Número", required=False)
    entrega_bairro   = forms.CharField(label="Bairro", required=False)
    entrega_cidade   = forms.CharField(label="Cidade", required=False)
    entrega_uf       = forms.CharField(label="UF", required=False, max_length=2)

    class Meta:
        model = PedidoExpedicao
        fields = [
            "numero",
            "data_pedido",
            "data_previsao_entrega",
            "data_expedicao",
            "status",
            "prioridade",
            "cliente",
            "transportadora",
            "romaneio",
            "contato_nome",
            "contato_telefone",
            "motorista_nome",
            "veiculo_placa",
            "observacao",
        ]
        widgets = {
            "data_pedido": forms.DateInput(attrs={"type": "date"}),
            "data_previsao_entrega": forms.DateInput(attrs={"type": "date"}),
            "data_expedicao": forms.DateInput(attrs={"type": "date"}),
            "observacao": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, filial=None, **kwargs):
        instance = kwargs.get("instance")
        initial = kwargs.setdefault("initial", {})
        if instance:
            end = instance.endereco_entrega or {}
            initial.update({
                "entrega_cep":      end.get("cep", ""),
                "entrega_endereco": end.get("endereco", ""),
                "entrega_numero":   end.get("numero", ""),
                "entrega_bairro":   end.get("bairro", ""),
                "entrega_cidade":   end.get("cidade", ""),
                "entrega_uf":       end.get("uf", ""),
            })
        super().__init__(*args, **kwargs)
        self.fields["cliente"].queryset = Cliente.objects.for_filial(filial).filter(ativo=True)
        self.fields["transportadora"].queryset = Transportadora.objects.for_filial(filial).filter(ativo=True)
        self.fields["romaneio"].queryset = RomaneioCarga.objects.for_filial(filial).exclude(
            status__in=[RomaneioCarga.Status.ENTREGUE, RomaneioCarga.Status.CANCELADO]
        )
        for nome in ("transportadora", "romaneio", "data_expedicao", "data_previsao_entrega"):
            self.fields[nome].required = False
        for field in self.fields.values():
            field.widget.attrs["class"] = BASE_INPUT_CLASS

    def save(self, commit=True):
        pedido = super().save(commit=False)
        pedido.endereco_entrega = {
            "cep":      self.cleaned_data.get("entrega_cep", ""),
            "endereco": self.cleaned_data.get("entrega_endereco", ""),
            "numero":   self.cleaned_data.get("entrega_numero", ""),
            "bairro":   self.cleaned_data.get("entrega_bairro", ""),
            "cidade":   self.cleaned_data.get("entrega_cidade", ""),
            "uf":       self.cleaned_data.get("entrega_uf", ""),
        }
        if commit:
            pedido.save()
        return pedido


class ItemPedidoExpedicaoForm(forms.ModelForm):
    class Meta:
        model = ItemPedidoExpedicao
        fields = [
            "produto_codigo",
            "produto_nome",
            "quantidade",
            "unidade",
            "volumes",
            "peso_kg",
            "valor_unitario",
            "observacao",
        ]
        widgets = {
            "observacao": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = BASE_INPUT_CLASS


# ─── MDF-e ────────────────────────────────────────────────────────────────────

class MDFeForm(forms.ModelForm):
    inicio_viagem = forms.DateTimeField(
        required=True,
        label="Início previsto da viagem",
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local"},
        ),
    )
    previsao_chegada = forms.DateTimeField(
        required=True,
        label="Previsão de chegada",
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local"},
        ),
    )
    motorista_cadastro = forms.ModelChoiceField(
        queryset=Motorista.objects.none(),
        required=False,
        empty_label="Selecione o motorista...",
        label="Motorista",
    )
    veiculo_cadastro = forms.ModelChoiceField(
        queryset=Veiculo.objects.none(),
        required=False,
        empty_label="Selecione o veículo...",
        label="Veículo",
    )
    peso_carga_kg = forms.DecimalField(
        required=False,
        min_value=0,
        decimal_places=3,
        max_digits=12,
        label="Peso bruto da carga (kg)",
        widget=forms.NumberInput(attrs={"step": "0.001", "min": "0"}),
    )

    class Meta:
        model = MDFe
        fields = [
            "numero", "serie", "data_emissao", "data_encerramento",
            "modal",
            "transportadora", "romaneio",
            "motorista_nome", "motorista_cpf", "motorista_cnh",
            "veiculo_placa", "veiculo_rntrc", "veiculo_descricao",
            "uf_carregamento", "municipio_carregamento", "codigo_municipio_carregamento",
            "percurso_ufs",
            "uf_descarregamento", "municipio_descarregamento", "codigo_municipio_descarregamento",
            "observacao",
        ]
        widgets = {
            "numero": forms.HiddenInput(),
            "serie": forms.HiddenInput(),
            "data_emissao": forms.HiddenInput(),
            "data_encerramento": forms.DateInput(attrs={"type": "date"}),
            "observacao": forms.Textarea(attrs={"rows": 3}),
            "percurso_ufs": forms.TextInput(attrs={"placeholder": "SP, RJ, MG, ES..."}),
            "codigo_municipio_carregamento": forms.HiddenInput(),
            "codigo_municipio_descarregamento": forms.HiddenInput(),
        }

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        agora = timezone.localtime().replace(second=0, microsecond=0)
        if self.instance and self.instance.pk:
            self.fields["inicio_viagem"].initial = self.instance.data_hora_inicio_viagem
            self.fields["previsao_chegada"].initial = self.instance.data_hora_previsao_fim
        else:
            self.fields["inicio_viagem"].initial = agora
            self.fields["previsao_chegada"].initial = agora + timedelta(hours=1)
        if filial:
            self.fields["transportadora"].queryset = Transportadora.objects.for_filial(filial).filter(ativo=True)
            self.fields["romaneio"].queryset = RomaneioCarga.objects.for_filial(filial).exclude(
                status__in=[RomaneioCarga.Status.CANCELADO]
            )
            self.fields["motorista_cadastro"].queryset = (
                Motorista.objects.for_filial(filial).filter(ativo=True).order_by("nome")
            )
            self.fields["veiculo_cadastro"].queryset = (
                Veiculo.objects.for_filial(filial).filter(ativo=True).order_by("placa")
            )
            if self.instance and self.instance.pk:
                cpf = "".join(filter(str.isdigit, self.instance.motorista_cpf or ""))
                placa = (self.instance.veiculo_placa or "").replace("-", "").upper()
                if cpf:
                    self.fields["motorista_cadastro"].initial = next(
                        (
                            motorista.pk
                            for motorista in self.fields["motorista_cadastro"].queryset
                            if "".join(filter(str.isdigit, motorista.cpf or "")) == cpf
                        ),
                        None,
                    )
                if placa:
                    self.fields["veiculo_cadastro"].initial = next(
                        (
                            veiculo.pk
                            for veiculo in self.fields["veiculo_cadastro"].queryset
                            if (veiculo.placa or "").replace("-", "").upper() == placa
                        ),
                        None,
                    )
                self.fields["peso_carga_kg"].initial = self.instance.peso_total_kg
        for nome in ("transportadora", "romaneio", "data_encerramento"):
            self.fields[nome].required = False
        self.fields["transportadora"].empty_label = "Transporte por conta própria"
        self.fields["romaneio"].empty_label = "Dispensado"
        for field in self.fields.values():
            field.widget.attrs["class"] = BASE_INPUT_CLASS
        self.fields["numero"].widget.attrs["readonly"] = True
        self.fields["serie"].widget.attrs["readonly"] = True

    def clean(self):
        cleaned = super().clean()
        motorista = cleaned.get("motorista_cadastro")
        veiculo = cleaned.get("veiculo_cadastro")
        if not motorista and not cleaned.get("motorista_nome"):
            self.add_error(
                "motorista_cadastro",
                "Selecione o motorista responsável pelo transporte.",
            )
        if not veiculo and not cleaned.get("veiculo_placa"):
            self.add_error(
                "veiculo_cadastro",
                "Selecione o veículo que fará o transporte.",
            )
        if not cleaned.get("peso_carga_kg"):
            self.add_error(
                "peso_carga_kg",
                "Informe o peso bruto da carga para emitir o MDF-e.",
            )
        inicio = cleaned.get("inicio_viagem")
        fim = cleaned.get("previsao_chegada")
        if inicio and fim and fim <= inicio:
            self.add_error(
                "previsao_chegada",
                "A previsão de chegada deve ser posterior ao início da viagem.",
            )
        return cleaned

    def save(self, commit=True):
        mdfe = super().save(commit=False)
        motorista = self.cleaned_data.get("motorista_cadastro")
        veiculo = self.cleaned_data.get("veiculo_cadastro")
        if motorista:
            mdfe.motorista_nome = motorista.nome
            mdfe.motorista_cpf = motorista.cpf
            mdfe.motorista_cnh = motorista.cnh
        if veiculo:
            mdfe.veiculo_placa = (veiculo.placa or "").replace("-", "").upper()
            mdfe.veiculo_rntrc = (
                getattr(veiculo.transportadora, "rntrc", "") if veiculo.transportadora else ""
            )
            mdfe.veiculo_descricao = (
                veiculo.descricao or f"{veiculo.marca} {veiculo.modelo}".strip()
            )
            mdfe.transporte_metadados = {
                "tara": str(veiculo.tara or ""),
                "capacidade_kg": str(veiculo.capacidade_kg or ""),
                "renavam": veiculo.renavam,
                "uf_placa": veiculo.uf_placa,
                "tipo_rodado": veiculo.tipo_rodado,
                "tipo_carroceria": veiculo.tipo_carroceria,
            }
        mdfe.peso_total_kg = self.cleaned_data.get("peso_carga_kg") or 0
        mdfe.data_hora_inicio_viagem = self.cleaned_data.get("inicio_viagem")
        mdfe.data_hora_previsao_fim = self.cleaned_data.get("previsao_chegada")
        if mdfe.data_hora_inicio_viagem:
            mdfe.data_emissao = timezone.localtime(
                mdfe.data_hora_inicio_viagem
            ).date()
        if commit:
            mdfe.save()
        return mdfe


class DocumentoMDFeForm(forms.ModelForm):
    class Meta:
        model = DocumentoMDFe
        fields = [
            "tipo_documento", "chave_acesso", "numero_documento", "serie",
            "emitente_nome", "emitente_documento",
            "municipio_descarga", "uf_descarga",
            "peso_kg", "valor", "observacao",
        ]
        widgets = {
            "observacao": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for nome in ("numero_documento", "serie", "emitente_nome", "emitente_documento",
                     "municipio_descarga", "uf_descarga", "observacao"):
            self.fields[nome].required = False
        for field in self.fields.values():
            field.widget.attrs["class"] = BASE_INPUT_CLASS
