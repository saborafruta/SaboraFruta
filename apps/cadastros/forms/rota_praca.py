"""Formulários de Praça e Rota."""
from django import forms

from apps.cadastros.models import Motorista, Praca, Representante, Rota, Veiculo


def _escopar(campo, model, filial):
    """
    Restringe um ModelChoiceField à filial ativa.

    ModelForm com `exclude` cria os selects de FK com queryset de TODOS os
    registros. Num sistema multiempresa isso mostraria dados de outro
    inquilino, então todo select de FK precisa passar por aqui.
    """
    if campo is None:
        return
    if filial is None:
        campo.queryset = model.objects.none()
        return
    qs = model.objects.for_filial(filial)
    if 'ativo' in {f.name for f in model._meta.get_fields()}:
        qs = qs.filter(ativo=True)
    campo.queryset = qs


class PracaForm(forms.ModelForm):
    class Meta:
        model = Praca
        # O polígono e sua caixa envolvente são desenhados no mapa e gravados
        # pela API (que mantém os dois em sincronia). Expor no form permitiria
        # salvar bbox incoerente com o polígono, e a atribuição de clientes
        # passaria a ignorar parte do território sem erro nenhum.
        exclude = [
            'filial', 'created_at', 'updated_at',
            'poligono', 'bbox_sul', 'bbox_norte', 'bbox_oeste', 'bbox_leste',
        ]
        widgets = {
            'cidades': forms.Textarea(attrs={
                'rows': 3,
                'placeholder': 'São Paulo, Guarulhos, Osasco, Barueri...',
            }),
            'observacao': forms.Textarea(attrs={'rows': 3}),
            'cor': forms.TextInput(attrs={'type': 'color'}),
        }

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Sem escopo de filial o select listaria representantes de TODAS as
        # empresas — vazamento entre inquilinos num SaaS multiempresa.
        _escopar(self.fields.get('representante'), Representante, filial)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-input w-full')


class RotaForm(forms.ModelForm):
    class Meta:
        model = Rota
        exclude = ['filial', 'created_at', 'updated_at']
        widgets = {
            'descricao': forms.Textarea(attrs={'rows': 3}),
            'pracas': forms.CheckboxSelectMultiple(),
        }

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        if filial is not None:
            self.fields['pracas'].queryset = Praca.objects.for_filial(filial).filter(ativo=True).order_by('nome')
        else:
            self.fields['pracas'].queryset = Praca.objects.none()
        self.fields['pracas'].required = False

        # Motorista/Veículo passaram de texto livre para FK; os selects têm de
        # respeitar a filial ativa.
        _escopar(self.fields.get('motorista'), Motorista, filial)
        _escopar(self.fields.get('veiculo'), Veiculo, filial)

        # Apply form-input class to all except the M2M checkbox field
        for name, field in self.fields.items():
            if name != 'pracas':
                field.widget.attrs.setdefault('class', 'form-input w-full')
