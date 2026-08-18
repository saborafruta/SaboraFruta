"""Formulários do vertical Moda."""
from django import forms

from .models import Cor, Grade, PedidoProducao, ProdutoModa, Tamanho


class _FilialFormMixin:
    """
    Restringe os selects à filial ativa.

    Sem isto, o formulário ofereceria cores e grades de outras filiais —
    e gravaria um produto apontando para cadastro de outra unidade.
    """

    campos_por_filial: dict = {}

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.filial = filial
        for campo, model in self.campos_por_filial.items():
            if campo in self.fields:
                qs = model.objects.filter(filial=filial, ativo=True)
                self.fields[campo].queryset = qs
        for campo in self.fields.values():
            css = campo.widget.attrs.get('class', '')
            if 'form-input' not in css:
                campo.widget.attrs['class'] = (css + ' form-input').strip()


class GradeForm(_FilialFormMixin, forms.ModelForm):
    class Meta:
        model = Grade
        fields = ['nome', 'tipo', 'descricao', 'ativo']
        widgets = {
            'descricao': forms.TextInput(attrs={'placeholder': 'Ex.: grade do uniforme escolar'}),
        }

    def clean_nome(self):
        nome = (self.cleaned_data['nome'] or '').strip()
        qs = Grade.objects.filter(filial=self.filial, nome__iexact=nome)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError('Já existe uma grade com esse nome nesta filial.')
        return nome


class TamanhoForm(_FilialFormMixin, forms.ModelForm):
    class Meta:
        model = Tamanho
        fields = ['sigla', 'nome', 'tipo', 'ordem']

    def clean_sigla(self):
        sigla = (self.cleaned_data['sigla'] or '').strip().upper()
        qs = Tamanho.objects.filter(filial=self.filial, sigla=sigla)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError(f'O tamanho {sigla} já existe nesta filial.')
        return sigla


class CorForm(_FilialFormMixin, forms.ModelForm):
    class Meta:
        model = Cor
        fields = ['nome', 'sigla', 'hex_cor', 'codigo_pantone', 'ativo']
        widgets = {
            'hex_cor': forms.TextInput(attrs={'type': 'color'}),
            'sigla': forms.TextInput(attrs={'placeholder': 'AMA', 'maxlength': 6}),
        }

    def clean_sigla(self):
        sigla = (self.cleaned_data['sigla'] or '').strip().upper()
        qs = Cor.objects.filter(filial=self.filial, sigla=sigla)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            # A sigla entra no SKU: duas cores com a mesma sigla gerariam
            # SKUs iguais para peças diferentes.
            raise forms.ValidationError(
                f'A sigla {sigla} já está em uso — ela entra no SKU e precisa ser única.'
            )
        return sigla


class ProdutoModaForm(_FilialFormMixin, forms.ModelForm):
    campos_por_filial = {}  # preenchido em __init__, para evitar import circular

    class Meta:
        model = ProdutoModa
        fields = [
            'codigo', 'referencia', 'nome', 'descricao',
            'categoria', 'colecao', 'linha', 'modelo', 'marca',
            'tecido', 'grade', 'status',
            'foto', 'desenho_tecnico', 'ficha_tecnica',
        ]
        widgets = {
            'descricao': forms.Textarea(attrs={'rows': 3}),
            'codigo': forms.TextInput(attrs={'placeholder': 'CAM001'}),
        }

    def __init__(self, *args, filial=None, **kwargs):
        from .models import Categoria, Colecao, Linha, Marca, Modelo, Tecido
        self.campos_por_filial = {
            'categoria': Categoria, 'colecao': Colecao, 'linha': Linha,
            'modelo': Modelo, 'marca': Marca, 'tecido': Tecido, 'grade': Grade,
        }
        super().__init__(*args, filial=filial, **kwargs)
        for nome in self.campos_por_filial:
            if nome in self.fields:
                self.fields[nome].required = False

    def clean_codigo(self):
        codigo = (self.cleaned_data['codigo'] or '').strip().upper()
        qs = ProdutoModa.objects.filter(filial=self.filial, codigo=codigo)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError(
                f'Já existe um produto com o código {codigo} — ele é o prefixo do SKU.'
            )
        return codigo


class PedidoProducaoForm(_FilialFormMixin, forms.ModelForm):
    """Cabeçalho do pedido de produção — a parte de cima da ficha."""

    class Meta:
        model = PedidoProducao
        fields = [
            'cliente', 'contato_nome', 'contato_telefone',
            'data_pedido', 'data_prevista_entrega',
            'vendedor', 'prioridade', 'status', 'observacoes',
        ]
        widgets = {
            'data_pedido': forms.DateInput(attrs={'type': 'date'}),
            'data_prevista_entrega': forms.DateInput(attrs={'type': 'date'}),
            'observacoes': forms.Textarea(attrs={'rows': 3}),
            'contato_nome': forms.TextInput(attrs={'placeholder': 'Ex.: Anderson'}),
            'contato_telefone': forms.TextInput(attrs={'placeholder': '(84) 99210-8081'}),
        }

    def __init__(self, *args, filial=None, **kwargs):
        from apps.cadastros.models import Cliente
        from apps.core.models import Usuario

        super().__init__(*args, filial=filial, **kwargs)

        # Cliente e vendedor não passam pelo `campos_por_filial` porque são
        # de outros apps e têm regra própria de escopo.
        self.fields['cliente'].queryset = (
            Cliente.objects.filter(ativo=True).order_by('razao_social')
            if filial is None else
            Cliente.objects.for_filial(filial).filter(ativo=True).order_by('razao_social')
        )
        self.fields['vendedor'].queryset = Usuario.objects.filter(
            ativo=True, empresa=filial.empresa if filial else None,
        ).order_by('nome')
        self.fields['vendedor'].required = False

    def clean(self):
        dados = super().clean()
        pedido = dados.get('data_pedido')
        entrega = dados.get('data_prevista_entrega')
        # Entrega antes do pedido é erro de digitação, e passaria despercebido
        # até o PCP priorizar um pedido "atrasado" que nem começou.
        if pedido and entrega and entrega < pedido:
            self.add_error(
                'data_prevista_entrega',
                'A entrega não pode ser anterior à data do pedido.',
            )
        return dados
