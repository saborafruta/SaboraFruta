"""
Formulários do vertical Polpa de Frutas.

DUAS DECISÕES QUE VALEM PARA TODOS ELES:

  · CAMPO NUMÉRICO DE MEDIÇÃO NASCE VAZIO, não zerado. Brix 0 é uma medição
    que aconteceu e deu zero — e reprovaria a carga sozinho. Vazio é "ainda
    não mediram", que é a verdade enquanto o técnico não chegou na balança.

  · O QUE A TELA MOSTRA É O QUE A FILIAL TEM. Fruta e produtor são filtrados
    pela filial ativa no `__init__`: sem isso, o select de uma unidade
    ofereceria o produtor de outra, e o romaneio nasceria com um vínculo que
    o resto do ERP recusa.
"""
from django import forms

from apps.cadastros.models import Fornecedor
from apps.produtos.models import Produto

from .models import Fruta, Recebimento

ENTRADA = {'class': 'form-input w-full'}
SELECT = {'class': 'form-select w-full'}


class FrutaRapidaForm(forms.ModelForm):
    """
    O minimo para destravar o romaneio: nome e variedade.

    QUEM ABRE ISTO TEM CAMINHAO NA BALANCA. A ficha completa da fruta -- brix,
    pH, impureza, rendimento, safra -- e' trabalho de quem cuida da formulacao,
    com a tabela do laboratorio na mao, e nao de quem esta' pesando carga. Pedir
    aqueles campos agora e' garantir que alguem invente numero para o formulario
    deixar salvar, e numero inventado de brix vira criterio de aceite errado la'
    na classificacao.

    Entao a fruta nasce so' com identidade, e a ficha se completa depois na tela
    de formulacao. Os limites ficam nulos ate' la', que e' honesto: significa
    "ninguem mediu ainda", e nao "o limite e' zero".
    """

    class Meta:
        model = Fruta
        fields = ('nome', 'variedade')
        widgets = {
            'nome': forms.TextInput(attrs={**ENTRADA, 'placeholder': 'Manga'}),
            'variedade': forms.TextInput(attrs={**ENTRADA, 'placeholder': 'Tommy (opcional)'}),
        }

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.filial = filial
        self.fields['variedade'].required = False

    def clean_nome(self):
        nome = (self.cleaned_data.get('nome') or '').strip()
        if not nome:
            raise forms.ValidationError('Informe o nome da fruta.')
        return nome

    def clean(self):
        # MESMO NOME E MESMA VARIEDADE JA' CADASTRADOS. Sem isto, quem nao
        # encontrou "Manga" na lista por causa de um acento cria a segunda
        # "Manga" -- e o historico de rendimento da fruta nasce partido em dois.
        limpos = super().clean()
        nome = (limpos.get('nome') or '').strip()
        variedade = (limpos.get('variedade') or '').strip()
        if nome and self.filial:
            existe = (
                Fruta.objects.for_filial(self.filial)
                .filter(nome__iexact=nome, variedade__iexact=variedade)
                .exists()
            )
            if existe:
                raise forms.ValidationError(
                    'Esta fruta ja esta cadastrada. Procure na lista.'
                )
        return limpos


class FrutaForm(forms.ModelForm):
    class Meta:
        model = Fruta
        fields = (
            'nome', 'variedade', 'produto',
            'brix_minimo', 'ph_maximo', 'impureza_maxima',
            'rendimento_esperado', 'safra_inicio', 'safra_fim',
            'ativo', 'observacao',
        )
        # OS ROTULOS SAO DITOS AQUI, e nao no modelo: mudar `verbose_name`
        # gera migration so' para trocar texto. O padrao do Django vinha do
        # nome do campo -- "Ph maximo", "Brix minimo", sem acento e com caixa
        # errada num termo tecnico que a etiqueta do laboratorio escreve "pH".
        labels = {
            'nome': 'Nome',
            'variedade': 'Variedade',
            'produto': 'Produto no catálogo',
            'brix_minimo': 'Brix mínimo',
            'ph_maximo': 'pH máximo',
            'impureza_maxima': 'Impureza máxima (%)',
            'rendimento_esperado': 'Rendimento esperado (%)',
            'safra_inicio': 'Safra começa em',
            'safra_fim': 'Safra termina em',
            'ativo': 'Fruta ativa',
            'observacao': 'Observação',
        }
        widgets = {
            'nome': forms.TextInput(attrs={**ENTRADA, 'placeholder': 'Manga'}),
            'variedade': forms.TextInput(attrs={**ENTRADA, 'placeholder': 'Tommy'}),
            'produto': forms.Select(attrs=SELECT),
            'brix_minimo': forms.NumberInput(attrs={**ENTRADA, 'step': '0.01'}),
            'ph_maximo': forms.NumberInput(attrs={**ENTRADA, 'step': '0.01'}),
            'impureza_maxima': forms.NumberInput(attrs={**ENTRADA, 'step': '0.01'}),
            'rendimento_esperado': forms.NumberInput(attrs={**ENTRADA, 'step': '0.01'}),
            'safra_inicio': forms.Select(attrs=SELECT),
            'safra_fim': forms.Select(attrs=SELECT),
            'observacao': forms.Textarea(attrs={**ENTRADA, 'rows': 2}),
        }

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.filial = filial
        # SÓ MATÉRIA-PRIMA DA FILIAL. Oferecer o catálogo inteiro faria
        # alguém ligar a manga ao produto "Polpa de Manga 1kg" -- e o lote
        # de fruta in natura entraria como se fosse produto acabado.
        self.fields['produto'].queryset = (
            Produto.objects.for_filial(filial) if filial else Produto.objects.none()
        )
        self.fields['produto'].required = False
        self.fields['produto'].empty_label = 'Sem vínculo com o catálogo'

    def save(self, commit=True):
        fruta = super().save(commit=False)
        if self.filial and not fruta.filial_id:
            fruta.filial = self.filial
        if commit:
            fruta.save()
        return fruta


class RecebimentoForm(forms.ModelForm):
    """O romaneio: quem trouxe, o que trouxe e quanto a balança marcou."""

    class Meta:
        model = Recebimento
        fields = (
            'fruta', 'produtor', 'data', 'hora_chegada',
            'placa', 'motorista', 'nota_fiscal',
            'peso_bruto', 'tara', 'desconto_kg', 'preco_kg', 'observacao',
        )
        widgets = {
            'fruta': forms.Select(attrs=SELECT),
            'produtor': forms.Select(attrs=SELECT),
            'data': forms.DateInput(attrs={**ENTRADA, 'type': 'date'}),
            'hora_chegada': forms.TimeInput(attrs={**ENTRADA, 'type': 'time'}),
            'placa': forms.TextInput(attrs={**ENTRADA, 'placeholder': 'ABC1D23'}),
            'motorista': forms.TextInput(attrs=ENTRADA),
            'nota_fiscal': forms.TextInput(attrs=ENTRADA),
            'peso_bruto': forms.NumberInput(attrs={**ENTRADA, 'step': '0.001'}),
            'tara': forms.NumberInput(attrs={**ENTRADA, 'step': '0.001'}),
            'desconto_kg': forms.NumberInput(attrs={**ENTRADA, 'step': '0.001'}),
            'preco_kg': forms.NumberInput(attrs={**ENTRADA, 'step': '0.0001'}),
            'observacao': forms.Textarea(attrs={**ENTRADA, 'rows': 2}),
        }

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.filial = filial
        if filial:
            self.fields['fruta'].queryset = Fruta.objects.for_filial(filial).filter(ativo=True)
            self.fields['produtor'].queryset = Fornecedor.objects.for_filial(filial)
        else:
            self.fields['fruta'].queryset = Fruta.objects.none()
            self.fields['produtor'].queryset = Fornecedor.objects.none()

        # SÓ FRUTA E PRODUTOR SÃO OBRIGATÓRIOS no romaneio. O caminhão entra
        # na balança antes de alguém ter a nota em mãos, e exigir tudo de uma
        # vez faria a pesagem ser anotada num papel para ser digitada depois
        # -- que é exatamente o registro que se perde.
        for campo in ('placa', 'motorista', 'nota_fiscal', 'hora_chegada'):
            self.fields[campo].required = False

        # `---------` e' o rotulo padrao do Django e nao diz nada. Num select
        # obrigatorio e vazio -- filial nova, sem fruta cadastrada -- ele fica
        # ainda pior: parece campo carregando, e nao campo sem opcao.
        self.fields['fruta'].empty_label = 'Selecione a fruta'
        self.fields['produtor'].empty_label = 'Selecione o produtor'

    def clean(self):
        dados = super().clean()
        bruto = dados.get('peso_bruto') or 0
        tara = dados.get('tara') or 0
        desconto = dados.get('desconto_kg') or 0

        # TARA MAIOR QUE O BRUTO é erro de digitação, não uma carga
        # negativa. Deixar passar daria peso líquido zero e um romaneio que
        # parece pesado mas não pesa nada.
        if bruto and tara and tara >= bruto:
            self.add_error(
                'tara', 'A tara ficou maior que o peso bruto — confira a pesagem.',
            )
        liquido = max(bruto - tara, 0)
        if desconto and liquido and desconto > liquido:
            self.add_error(
                'desconto_kg',
                'O desconto passou do peso líquido — sobraria carga negativa.',
            )
        return dados

    def save(self, commit=True):
        recebimento = super().save(commit=False)
        if self.filial and not recebimento.filial_id:
            recebimento.filial = self.filial
        if commit:
            recebimento.save()
        return recebimento


class ClassificacaoForm(forms.ModelForm):
    """A análise da carga — o que decide aceitar ou devolver."""

    class Meta:
        model = Recebimento
        fields = ('temperatura_chegada', 'brix', 'ph', 'acidez', 'impureza', 'danificada')
        widgets = {
            'temperatura_chegada': forms.NumberInput(attrs={**ENTRADA, 'step': '0.01'}),
            'brix': forms.NumberInput(attrs={**ENTRADA, 'step': '0.01'}),
            'ph': forms.NumberInput(attrs={**ENTRADA, 'step': '0.01'}),
            'acidez': forms.NumberInput(attrs={**ENTRADA, 'step': '0.001'}),
            'impureza': forms.NumberInput(attrs={**ENTRADA, 'step': '0.01'}),
            'danificada': forms.NumberInput(attrs={**ENTRADA, 'step': '0.01'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # NENHUM CAMPO OBRIGATÓRIO. Nem toda fábrica mede acidez em toda
        # carga, e exigir os seis faria o técnico inventar número para o
        # formulário fechar -- registro inventado é pior que campo vazio.
        for campo in self.fields.values():
            campo.required = False
