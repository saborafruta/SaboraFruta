from django import forms

from apps.cadastros.models import Fornecedor


def _digitos_documento(valor):
    return ''.join(filter(str.isdigit, valor or ''))


def _cpf_valido(cpf):
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    soma = sum(int(digito) * peso for digito, peso in zip(cpf[:9], range(10, 1, -1)))
    primeiro = 0 if soma % 11 < 2 else 11 - soma % 11
    soma = sum(int(digito) * peso for digito, peso in zip(cpf[:10], range(11, 1, -1)))
    segundo = 0 if soma % 11 < 2 else 11 - soma % 11
    return cpf[-2:] == f'{primeiro}{segundo}'


def _cnpj_valido(cnpj):
    if len(cnpj) != 14 or cnpj == cnpj[0] * 14:
        return False

    def calcular(base, pesos):
        soma = sum(int(digito) * peso for digito, peso in zip(base, pesos))
        resto = soma % 11
        return '0' if resto < 2 else str(11 - resto)

    primeiro = calcular(cnpj[:12], [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    segundo = calcular(cnpj[:12] + primeiro, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    return cnpj[-2:] == primeiro + segundo


def _limpar_e_validar_documento(valor, tipo_pessoa):
    documento = _digitos_documento(valor)
    if not documento:
        return ''
    if tipo_pessoa == 'F':
        if len(documento) != 11:
            raise forms.ValidationError('CPF deve ter 11 dígitos.')
        if not _cpf_valido(documento):
            raise forms.ValidationError('CPF inválido. Confira os números informados.')
    elif tipo_pessoa == 'J':
        if len(documento) != 14:
            raise forms.ValidationError('CNPJ deve ter 14 dígitos.')
        if not _cnpj_valido(documento):
            raise forms.ValidationError('CNPJ inválido. Confira os números informados.')
    elif len(documento) not in (11, 14):
        raise forms.ValidationError('CPF deve ter 11 dígitos ou CNPJ deve ter 14.')
    elif len(documento) == 11 and not _cpf_valido(documento):
        raise forms.ValidationError('CPF inválido. Confira os números informados.')
    elif len(documento) == 14 and not _cnpj_valido(documento):
        raise forms.ValidationError('CNPJ inválido. Confira os números informados.')
    return documento


class FornecedorForm(forms.ModelForm):
    cpf_cnpj = forms.CharField(
        required=False,
        max_length=18,
        widget=forms.TextInput(attrs={'maxlength': '18'}),
    )
    cep = forms.CharField(
        required=False,
        max_length=9,
        widget=forms.TextInput(attrs={
            'maxlength': '9',
            'x-on:blur': 'consultarCep($event.target.value)',
        }),
    )

    class Meta:
        model = Fornecedor
        exclude = [
            'filial', 'nota_qualidade', 'total_entregas', 'entregas_no_prazo',
            'ativo', 'created_at', 'updated_at',
        ]
        widgets = {
            'observacao': forms.Textarea(attrs={'rows': 3}),
        }

    def clean_cpf_cnpj(self):
        return _limpar_e_validar_documento(
            self.cleaned_data.get('cpf_cnpj'),
            self.cleaned_data.get('tipo_pessoa'),
        )

    def clean_cep(self):
        valor = ''.join(filter(str.isdigit, self.cleaned_data.get('cep', '') or ''))
        if valor and len(valor) != 8:
            raise forms.ValidationError('CEP deve ter 8 digitos.')
        return valor


class FornecedorRapidoForm(forms.ModelForm):
    """Campos essenciais para criar fornecedor durante um lançamento."""

    cpf_cnpj = forms.CharField(required=False, max_length=18)
    cep = forms.CharField(required=False, max_length=9)

    class Meta:
        model = Fornecedor
        fields = [
            'tipo_pessoa', 'razao_social', 'nome_fantasia', 'cpf_cnpj',
            'inscricao_estadual', 'telefone', 'email', 'cep', 'endereco',
            'numero', 'bairro', 'cidade', 'uf', 'codigo_municipio_ibge',
        ]

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.filial = filial
        # SO' O NOME E' OBRIGATORIO. `tipo_pessoa` vinha obrigatorio por ser
        # `CharField` com `choices` e sem `blank`, e o cadastro relampago nao
        # pergunta isso -- quem esta' com o caminhao na balanca digita o nome do
        # produtor e segue. O formulario recusava com "Este campo e'
        # obrigatorio" sem dizer QUAL campo, porque o campo nem aparece na tela.
        self.fields['tipo_pessoa'].required = False
        # O ROTULO AGORA E' TEXTO QUE O USUARIO LE. Ele entra na mensagem de
        # erro devolvida ao modal, e o padrao do Django vinha do nome do campo:
        # "Razao social", sem acento. Dito aqui e nao no modelo, porque mexer em
        # `verbose_name` gera migration so' para trocar texto.
        self.fields['razao_social'].label = 'Razão social'
        self.fields['cpf_cnpj'].label = 'CPF / CNPJ'
        self.fields['telefone'].label = 'Telefone'

    def clean_tipo_pessoa(self):
        """
        Deduz do documento quando ninguem informou.

        Onze digitos e' CPF, catorze e' CNPJ -- a mesma leitura que
        `_limpar_e_validar_documento` ja' faz quando o tipo vem vazio. Sem
        deduzir, gravaria string vazia num campo com `choices`, que e' dado
        invalido silencioso: nao estoura, mas nenhuma tela sabe mostrar.

        SEM DOCUMENTO ASSUME FISICA. Produtor rural sem CPF a mao e' o caso
        normal aqui, e pessoa e' o palpite certo com mais frequencia -- o
        cadastro completo corrige depois, se for empresa.
        """
        informado = (self.cleaned_data.get('tipo_pessoa') or '').strip()
        if informado:
            return informado
        digitos = _digitos_documento(self.data.get('cpf_cnpj'))
        if len(digitos) == 14:
            return 'J'
        return 'F'

    def clean_cpf_cnpj(self):
        valor = _limpar_e_validar_documento(
            self.cleaned_data.get('cpf_cnpj'),
            self.cleaned_data.get('tipo_pessoa'),
        )
        if (
            valor
            and self.filial
            and Fornecedor.objects.for_filial(self.filial).filter(cpf_cnpj=valor).exists()
        ):
            raise forms.ValidationError('Já existe um fornecedor com este CPF/CNPJ nesta filial.')
        return valor

    def clean_cep(self):
        valor = ''.join(filter(str.isdigit, self.cleaned_data.get('cep', '') or ''))
        if valor and len(valor) != 8:
            raise forms.ValidationError('CEP deve ter 8 dígitos.')
        return valor

    def clean_uf(self):
        return (self.cleaned_data.get('uf') or '').strip().upper()
