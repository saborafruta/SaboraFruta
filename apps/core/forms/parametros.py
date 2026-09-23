"""Forms for the system parameters screen."""
from django import forms
from django.conf import settings
from django.core.files.uploadedfile import UploadedFile

from apps.core.constants.choices import UF
from apps.core.models import Empresa, Filial
from apps.core.models.parametros import ParametrosSistema


REGIME_CODIGO_CHOICES = [
    ('', 'Usar padrao da empresa'),
] + list(Empresa.CodigoRegimeTributario.choices)


def _aplicar_estilo(form):
    """Apply the shared parameter input class."""
    for field in form.fields.values():
        widget = field.widget
        if isinstance(widget, (forms.CheckboxInput, forms.ClearableFileInput)):
            continue
        css = widget.attrs.get('class', '')
        widget.attrs['class'] = (css + ' param-input').strip()


class FilialIdentidadeForm(forms.ModelForm):
    """Branch identity, address and fiscal integration settings."""

    class Meta:
        model = Filial
        fields = [
            'nome_fantasia', 'razao_social', 'cnpj',
            'inscricao_estadual', 'inscricao_municipal', 'email', 'imagem',
            'cep', 'endereco', 'numero', 'bairro', 'cidade',
            'codigo_municipio_ibge', 'uf', 'telefone',
            'regime_tributario', 'codigo_regime_tributario',
            'focusnfe_token', 'focusnfe_ambiente',
        ]
        widgets = {
            'uf': forms.Select(choices=[('', '-')] + list(UF.choices)),
            'cnpj': forms.TextInput(attrs={'placeholder': '00000000000000', 'maxlength': '14'}),
            'cep': forms.TextInput(attrs={'placeholder': '00000000', 'maxlength': '8'}),
            'codigo_municipio_ibge': forms.TextInput(attrs={'placeholder': '0000000', 'maxlength': '7'}),
            'regime_tributario': forms.Select(
                choices=[('', 'Usar padrao da empresa')] + list(Empresa.RegimeTributario.choices),
            ),
            'codigo_regime_tributario': forms.Select(choices=REGIME_CODIGO_CHOICES),
            'focusnfe_token': forms.PasswordInput(
                render_value=False,
                attrs={
                    'autocomplete': 'new-password',
                    'placeholder': 'Configurado; deixe em branco para manter',
                },
            ),
            'focusnfe_ambiente': forms.Select(choices=Filial.AmbienteNFe.choices),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['regime_tributario'].required = False
        self.fields['codigo_regime_tributario'].required = False
        _aplicar_estilo(self)

    def clean_cnpj(self):
        cnpj = ''.join(filter(str.isdigit, self.cleaned_data.get('cnpj', '')))
        if len(cnpj) != 14:
            raise forms.ValidationError('CNPJ deve conter 14 digitos.')
        return cnpj

    def clean_cep(self):
        cep = ''.join(filter(str.isdigit, self.cleaned_data.get('cep', '')))
        if cep and len(cep) != 8:
            raise forms.ValidationError('CEP deve conter 8 digitos.')
        return cep

    def clean_focusnfe_token(self):
        token = (self.cleaned_data.get('focusnfe_token') or '').strip()
        if token:
            return token
        if self.instance and self.instance.pk:
            return self.instance.focusnfe_token
        return ''


class ParametrosSistemaForm(forms.ModelForm):
    """General parameters that belong to the current branch."""

    class Meta:
        model = ParametrosSistema
        fields = [
            'email_secundario',
            'controlar_entrega_contas_receber',
            'checkout_venda_ativo',
            'balanca_ean_prefixo', 'balanca_ean_plu_digitos', 'balanca_ean_conteudo',
            'logo_url',
            'certificado_digital', 'senha_certificado',
            'focusnfe_token_principal',
            'nfce_csc_id', 'nfce_csc_token', 'nfce_contingencia_automatica',
            'comunicador_offline_instalador', 'comunicador_offline_versao',
            'email_envio_automatico', 'email_resposta',
            'texto_padrao_email', 'informacoes_complementares_padrao',
        ]
        widgets = {
            'email_secundario': forms.EmailInput(attrs={'placeholder': 'contato@empresa.com.br'}),
            'balanca_ean_prefixo': forms.TextInput(attrs={
                'inputmode': 'numeric', 'maxlength': '2', 'placeholder': '20',
            }),
            'logo_url': forms.URLInput(attrs={'placeholder': 'https://... (URL pública da logo)'}),
            'senha_certificado': forms.PasswordInput(
                render_value=False,
                attrs={
                    'autocomplete': 'new-password',
                    'placeholder': 'Configurada; deixe em branco para manter',
                },
            ),
            'focusnfe_token_principal': forms.PasswordInput(
                render_value=False,
                attrs={
                    'autocomplete': 'new-password',
                    'placeholder': 'Configurado; deixe em branco para manter',
                },
            ),
            'nfce_csc_id': forms.TextInput(attrs={'placeholder': 'Ex.: 000001'}),
            'nfce_csc_token': forms.PasswordInput(
                render_value=False,
                attrs={
                    'autocomplete': 'new-password',
                    'placeholder': 'Configurado; deixe em branco para manter',
                },
            ),
            'comunicador_offline_versao': forms.TextInput(attrs={'placeholder': 'Ex.: 3.0.0'}),
            'email_resposta': forms.EmailInput(attrs={'placeholder': 'fiscal@empresa.com.br'}),
            'texto_padrao_email': forms.Textarea(attrs={
                'rows': 3,
                'placeholder': 'Mensagem enviada junto com XML/DANFE.',
            }),
            'informacoes_complementares_padrao': forms.Textarea(attrs={
                'rows': 3,
                'placeholder': 'Informacoes complementares padrao da nota.',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _aplicar_estilo(self)

    def _segredo_ou_atual(self, campo):
        valor = (self.cleaned_data.get(campo) or '').strip()
        if valor:
            return valor
        if self.instance and self.instance.pk:
            return getattr(self.instance, campo, '')
        return ''

    def clean_senha_certificado(self):
        return self._segredo_ou_atual('senha_certificado')

    def clean_focusnfe_token_principal(self):
        return self._segredo_ou_atual('focusnfe_token_principal')

    def clean_nfce_csc_token(self):
        return self._segredo_ou_atual('nfce_csc_token')

    def clean_balanca_ean_prefixo(self):
        prefixo = (self.cleaned_data.get('balanca_ean_prefixo') or '').strip()
        if len(prefixo) not in {1, 2} or not prefixo.isdigit():
            raise forms.ValidationError('Informe um prefixo com um ou dois dígitos numéricos.')
        return prefixo

    def clean_comunicador_offline_instalador(self):
        arquivo = self.cleaned_data.get('comunicador_offline_instalador')
        if not arquivo or not isinstance(arquivo, UploadedFile):
            return arquivo
        limite = int(getattr(settings, 'COMUNICADOR_OFFLINE_MAX_UPLOAD_BYTES', 250 * 1024 * 1024))
        if arquivo.size > limite:
            raise forms.ValidationError('O instalador excede o limite de 250 MB.')
        return arquivo
