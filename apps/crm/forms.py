from django import forms

from apps.cadastros.models import Cliente
from apps.core.models import Usuario
from apps.crm.models import EtapaPipelineCRM, OportunidadeCRM, PipelineCRM, TagCRM


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

    def __init__(self, *args, filial, **kwargs):
        super().__init__(*args, **kwargs)
        self.filial = filial
        self.fields['cliente'].queryset = Cliente.objects.for_filial(filial).filter(
            ativo=True,
        ).order_by('razao_social')
        self.fields['pipeline'].queryset = PipelineCRM.objects.filter(
            filial=filial, ativo=True,
        ).order_by('nome')
        self.fields['responsavel'].queryset = Usuario.objects.filter(
            empresa=filial.empresa, ativo=True,
        ).order_by('nome')
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
        if cliente and not Cliente.objects.for_filial(self.filial).filter(pk=cliente.pk).exists():
            self.add_error('cliente', 'O cliente não está disponível nesta filial.')
        if responsavel and responsavel.empresa_id != self.filial.empresa_id:
            self.add_error('responsavel', 'O responsável pertence a outra empresa.')
        return dados
