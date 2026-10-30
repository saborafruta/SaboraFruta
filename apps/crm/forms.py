from django import forms
from django.core.exceptions import ValidationError

from apps.cadastros.models import Cliente
from apps.core.models import Usuario
from apps.crm.models import EtapaPipelineCRM, OportunidadeCRM, PipelineCRM, TagCRM
from apps.crm.services.carteira_service import CarteiraService


class OportunidadeCRMForm(forms.ModelForm):
    class Meta:
        model = OportunidadeCRM
        fields = [
            'titulo', 'cliente', 'pipeline', 'etapa', 'valor_estimado',
            'prioridade', 'origem', 'responsavel', 'previsao_fechamento',
            'proxima_acao', 'proxima_acao_em', 'tags', 'descricao',
        ]
        widgets = {
            'previsao_fechamento': forms.DateInput(attrs={'type': 'date'}),
            'proxima_acao_em': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'descricao': forms.Textarea(attrs={'rows': 4}),
            'tags': forms.CheckboxSelectMultiple(),
        }

    def __init__(self, *args, filial, usuario, **kwargs):
        super().__init__(*args, **kwargs)
        self.filial = filial
        self.usuario = usuario
        self.fields['cliente'].queryset = CarteiraService.clientes_disponiveis_na_filial(
            filial=filial, usuario=usuario,
        ).order_by('razao_social')
        self.fields['pipeline'].queryset = PipelineCRM.objects.filter(
            filial=filial, ativo=True,
        ).order_by('nome')
        responsaveis = Usuario.objects.filter(
            empresa=filial.empresa, ativo=True,
        )
        if not CarteiraService.pode_supervisionar(usuario):
            responsaveis = responsaveis.filter(pk=usuario.pk)
            self.initial.setdefault('responsavel', usuario)
        self.fields['responsavel'].queryset = responsaveis.order_by('nome')
        self.fields['tags'].queryset = TagCRM.objects.filter(
            filial=filial, ativo=True,
        ).order_by('nome')

        pipeline_id = self.data.get('pipeline') if self.is_bound else None
        if not pipeline_id and self.instance.pk:
            pipeline_id = self.instance.pipeline_id
        if not pipeline_id:
            pipeline_id = self.initial.get('pipeline')
            pipeline_id = getattr(pipeline_id, 'pk', pipeline_id)
        self.fields['etapa'].queryset = EtapaPipelineCRM.objects.filter(
            pipeline_id=pipeline_id, pipeline__filial=filial, ativo=True,
        ).order_by('ordem') if pipeline_id else EtapaPipelineCRM.objects.none()

        for nome, campo in self.fields.items():
            if nome == 'tags':
                continue
            campo.widget.attrs['class'] = (
                f"{campo.widget.attrs.get('class', '')} form-input w-full"
            ).strip()
        self.fields['valor_estimado'].label = 'Valor estimado'
        self.fields['previsao_fechamento'].label = 'Previsão de fechamento'
        self.fields['proxima_acao'].label = 'Próxima ação'
        self.fields['proxima_acao_em'].label = 'Quando executar'

    def clean(self):
        dados = super().clean()
        pipeline = dados.get('pipeline')
        etapa = dados.get('etapa')
        cliente = dados.get('cliente')
        responsavel = dados.get('responsavel')
        if pipeline and pipeline.filial_id != self.filial.pk:
            self.add_error('pipeline', 'O funil selecionado pertence a outra filial.')
        if etapa and pipeline and etapa.pipeline_id != pipeline.pk:
            self.add_error('etapa', 'A etapa selecionada não pertence a este funil.')
        if cliente and not self.fields['cliente'].queryset.filter(pk=cliente.pk).exists():
            self.add_error('cliente', 'O cliente não está disponível na sua carteira.')
        if responsavel and responsavel.empresa_id != self.filial.empresa_id:
            self.add_error('responsavel', 'O responsável pertence a outra empresa.')
        if cliente and 'cliente' not in self.errors:
            try:
                dados['responsavel'] = CarteiraService.validar_responsavel(
                    cliente=cliente, responsavel=responsavel, usuario=self.usuario,
                )
            except ValidationError as exc:
                self.add_error('responsavel', exc)
        return dados


class CarteiraClienteForm(forms.Form):
    cliente = forms.ModelChoiceField(queryset=Cliente.objects.none())
    vendedor = forms.ModelChoiceField(queryset=Usuario.objects.none())

    def __init__(self, *args, empresa, **kwargs):
        super().__init__(*args, **kwargs)
        self.empresa = empresa
        self.fields['cliente'].queryset = Cliente.objects.for_empresa(empresa).filter(
            ativo=True,
        ).select_related('filial').order_by('razao_social', 'pk')
        self.fields['vendedor'].queryset = Usuario.objects.filter(
            empresa=empresa, ativo=True,
        ).order_by('nome')

    def clean(self):
        dados = super().clean()
        cliente = dados.get('cliente')
        vendedor = dados.get('vendedor')
        if cliente and cliente.filial.empresa_id != self.empresa.pk:
            self.add_error('cliente', 'O cliente pertence a outra empresa.')
        if vendedor and vendedor.empresa_id != self.empresa.pk:
            self.add_error('vendedor', 'O vendedor pertence a outra empresa.')
        return dados
