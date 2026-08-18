"""Formulários do vertical Moda."""
from django import forms

from .models import (
    Cor, Grade, ItemPedidoProducao, MockupVisual, PedidoProducao,
    Personalizacao, ProdutoModa, Tamanho, VisualItemPedido,
)


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



class ValoresPedidoForm(forms.ModelForm):
    """
    A seção financeira do pedido.

    Não usa `_FilialFormMixin` porque forma e condição de pagamento são do
    app financeiro e têm escopo próprio: a forma é por empresa E filial
    (com filial nula valendo para todas), a condição é só por empresa.
    Passar isso pelo mixin daria um filtro errado nos dois casos.
    """

    class Meta:
        model = PedidoProducao
        fields = [
            'desconto', 'acrescimo', 'frete', 'entrada',
            'forma_pagamento', 'condicao_pagamento',
        ]
        # `x-model.number` liga cada campo ao Alpine da tela, para o total
        # fechar enquanto o usuário digita em vez de só depois de salvar.
        widgets = {
            campo: forms.NumberInput(attrs={
                'step': '0.01', 'min': '0', 'x-model.number': f'd.{campo}',
            })
            for campo in ('desconto', 'acrescimo', 'frete', 'entrada')
        }

    def __init__(self, *args, filial=None, **kwargs):
        from django.db.models import Q

        from apps.financeiro.models.formas_pagamento import (
            CondicaoPagamento, FormaPagamento,
        )

        super().__init__(*args, **kwargs)
        self.filial = filial
        empresa = filial.empresa if filial else None

        self.fields['forma_pagamento'].queryset = FormaPagamento.objects.filter(
            Q(filial=filial) | Q(filial__isnull=True), empresa=empresa, ativo=True,
        ).order_by('descricao')
        self.fields['condicao_pagamento'].queryset = CondicaoPagamento.objects.filter(
            empresa=empresa, ativo=True,
        ).order_by('descricao')

        self.fields['forma_pagamento'].empty_label = 'Não informada'
        self.fields['condicao_pagamento'].empty_label = 'À vista'

        for campo in self.fields.values():
            css = campo.widget.attrs.get('class', '')
            if 'form-input' not in css:
                campo.widget.attrs['class'] = (css + ' form-input').strip()

    def clean(self):
        dados = super().clean()

        # Valor negativo em desconto/frete inverteria o sinal do total sem
        # ninguém perceber -- o campo aceita, a conta a receber sai errada.
        for campo in ('desconto', 'acrescimo', 'frete', 'entrada'):
            valor = dados.get(campo)
            if valor is not None and valor < 0:
                self.add_error(campo, 'Não pode ser negativo.')

        return dados


class ItemPedidoProducaoForm(_FilialFormMixin, forms.ModelForm):
    """Um produto dentro do pedido — o miolo da ficha."""

    campos_por_filial = {}  # preenchido em __init__, para evitar import circular

    class Meta:
        model = ItemPedidoProducao
        fields = [
            'produto', 'descricao', 'referencia',
            'modelo', 'cor', 'tecido', 'gola', 'manga',
            'acabamento', 'quantidade', 'valor_unitario', 'observacoes',
        ]
        widgets = {
            'descricao': forms.TextInput(attrs={'placeholder': 'Ex.: Conjunto — Camisa + Calção'}),
            'acabamento': forms.TextInput(attrs={'placeholder': 'Ex.: escudo em patch aplicado'}),
            'observacoes': forms.Textarea(attrs={'rows': 2}),
            'quantidade': forms.NumberInput(attrs={'min': 1}),
            'valor_unitario': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
        }

    def __init__(self, *args, filial=None, **kwargs):
        from .models import Cor, Modelo, ProdutoModa, Tecido
        self.campos_por_filial = {
            'produto': ProdutoModa, 'modelo': Modelo, 'cor': Cor, 'tecido': Tecido,
        }
        super().__init__(*args, filial=filial, **kwargs)
        for nome in ('produto', 'modelo', 'cor', 'tecido'):
            self.fields[nome].required = False
        # Preço não é obrigatório aqui: o comercial monta a ficha antes de
        # fechar valor, e exigir agora travaria o cadastro do produto.
        self.fields['valor_unitario'].required = False
        # Em branco, o item herda do modelo na gravação — por isso não são
        # obrigatórios aqui.
        self.fields['gola'].required = False
        self.fields['manga'].required = False

    def clean(self):
        dados = super().clean()
        # Sem produto de catálogo nem descrição, o item apareceria na ficha
        # como uma linha em branco — e ninguém no corte saberia o que cortar.
        if not dados.get('produto') and not (dados.get('descricao') or '').strip():
            self.add_error(
                'descricao',
                'Escolha um produto do catálogo ou descreva o item.',
            )
        if (dados.get('quantidade') or 0) < 1:
            self.add_error('quantidade', 'A quantidade precisa ser pelo menos 1.')
        return dados


class PersonalizacaoForm(_FilialFormMixin, forms.ModelForm):
    """Uma aplicação de arte no item — técnica, local e arquivo."""

    class Meta:
        model = Personalizacao
        fields = [
            'tipo', 'tecnica', 'local',
            'nome_personalizado', 'numero_personalizado',
            'patrocinios', 'quantidade_patrocinadores',
            'arquivo', 'observacoes',
        ]
        widgets = {
            'local': forms.TextInput(attrs={'placeholder': 'Ex.: peito esquerdo'}),
            'nome_personalizado': forms.TextInput(attrs={'placeholder': 'Ex.: SILVA'}),
            'numero_personalizado': forms.TextInput(attrs={'placeholder': 'Ex.: 25'}),
            'patrocinios': forms.Textarea(attrs={'rows': 2}),
            'observacoes': forms.Textarea(attrs={'rows': 2}),
            'quantidade_patrocinadores': forms.NumberInput(attrs={'min': 0}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # O input aceita só o que o model valida — sem isto o seletor de
        # arquivos ofereceria qualquer coisa e o erro só apareceria depois
        # do upload inteiro subir.
        from .models.personalizacao import EXTENSOES_ARTE
        self.fields['arquivo'].widget.attrs['accept'] = ','.join(
            f'.{ext}' for ext in EXTENSOES_ARTE
        )

    def clean(self):
        dados = super().clean()
        tecnica = dados.get('tecnica')
        # "Sem impressão" com arquivo anexado é contradição: alguém marcou
        # errado, e a produção seguiria a etiqueta em vez da arte.
        if tecnica == Personalizacao.Tecnica.SEM_IMPRESSAO and dados.get('arquivo'):
            self.add_error(
                'tecnica',
                'Você anexou uma arte, então a técnica não pode ser "Sem impressão".',
            )
        qtd = dados.get('quantidade_patrocinadores') or 0
        if qtd and not (dados.get('patrocinios') or '').strip():
            self.add_error(
                'patrocinios',
                'Informe quais são os patrocinadores, ou deixe a quantidade em zero.',
            )
        return dados


class VisualItemPedidoForm(_FilialFormMixin, forms.ModelForm):
    """Uma das quatro vistas do item (frente/costas de camisa e calção)."""

    class Meta:
        model = VisualItemPedido
        fields = ['posicao', 'imagem', 'mockup', 'nome', 'numero', 'observacoes']
        widgets = {
            'nome': forms.TextInput(attrs={'placeholder': 'Ex.: SILVA'}),
            'numero': forms.TextInput(attrs={'placeholder': 'Ex.: 25'}),
            'observacoes': forms.TextInput(attrs={'placeholder': 'Detalhe desta vista'}),
        }

    def __init__(self, *args, filial=None, item=None, **kwargs):
        super().__init__(*args, filial=filial, **kwargs)
        self.item = item
        self.fields['mockup'].queryset = MockupVisual.objects.filter(
            filial=filial, ativo=True,
        )
        self.fields['mockup'].required = False
        self.fields['imagem'].required = False

    def clean(self):
        from .models.visual import POSICOES_COSTAS

        dados = super().clean()
        posicao = dados.get('posicao')

        # Sem imagem própria nem mockup a vista não mostraria nada — seria um
        # quadro vazio no painel, indistinguível de erro de carregamento.
        if not dados.get('imagem') and not dados.get('mockup'):
            self.add_error('imagem', 'Envie uma imagem ou escolha um mockup cadastrado.')

        # Nome e número vão nas costas da peça. Aceitá-los numa vista de
        # frente gravaria um dado que a produção nunca veria.
        if posicao and posicao not in POSICOES_COSTAS:
            if (dados.get('nome') or '').strip() or (dados.get('numero') or '').strip():
                self.add_error(
                    'posicao',
                    'Nome e número só se aplicam às costas — troque a posição ou limpe os campos.',
                )

        # Uma vista por posição: duas "frentes da camisa" no mesmo item
        # seriam contraditórias na hora de produzir.
        if posicao and self.item is not None:
            existente = self.item.visuais.filter(posicao=posicao)
            if self.instance.pk:
                existente = existente.exclude(pk=self.instance.pk)
            if existente.exists():
                self.add_error(
                    'posicao',
                    'Esta posição já tem imagem neste item. Remova a atual para substituir.',
                )
        return dados


# ══════════════════════════════════════════════════════════════════════
# Cadastros de apoio
# ══════════════════════════════════════════════════════════════════════

class _NomeUnicoMixin:
    """
    Impede nome repetido na filial, com mensagem que diz o que houve.

    O model sai do próprio Meta — repeti-lo num atributo abriria a chance
    de os dois discordarem numa edição futura.
    """

    def clean_nome(self):
        nome = (self.cleaned_data['nome'] or '').strip()
        qs = self._meta.model.objects.filter(filial=self.filial, nome__iexact=nome)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError(f'Já existe "{nome}" cadastrado nesta filial.')
        return nome


class MarcaForm(_NomeUnicoMixin, _FilialFormMixin, forms.ModelForm):

    class Meta:
        from .models import Marca
        model = Marca
        fields = ['nome', 'observacao', 'ativo']
        widgets = {'observacao': forms.Textarea(attrs={'rows': 2})}


class LinhaForm(_NomeUnicoMixin, _FilialFormMixin, forms.ModelForm):
    class Meta:
        from .models import Linha
        model = Linha
        fields = ['nome', 'observacao', 'ativo']
        widgets = {'observacao': forms.Textarea(attrs={'rows': 2})}


class ColecaoForm(_NomeUnicoMixin, _FilialFormMixin, forms.ModelForm):
    class Meta:
        from .models import Colecao
        model = Colecao
        fields = ['nome', 'ano', 'estacao', 'data_inicio', 'data_fim', 'observacao', 'ativo']
        widgets = {
            'data_inicio': forms.DateInput(attrs={'type': 'date'}),
            'data_fim': forms.DateInput(attrs={'type': 'date'}),
            'estacao': forms.TextInput(attrs={'placeholder': 'Ex.: Verão'}),
            'observacao': forms.Textarea(attrs={'rows': 2}),
        }

    def clean(self):
        dados = super().clean()
        inicio, fim = dados.get('data_inicio'), dados.get('data_fim')
        if inicio and fim and fim < inicio:
            self.add_error('data_fim', 'O fim da coleção não pode ser antes do início.')
        return dados


class ModeloForm(_NomeUnicoMixin, _FilialFormMixin, forms.ModelForm):
    class Meta:
        from .models import Modelo
        model = Modelo
        fields = ['nome', 'gola', 'manga', 'observacao', 'ativo']
        widgets = {
            'nome': forms.TextInput(attrs={'placeholder': 'Ex.: Camisa gola frade manga com punho'}),
            'observacao': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Gola e manga são o default que o item do pedido herda — mas o
        # item pode sobrescrever, então aqui não são obrigatórias.
        self.fields['gola'].required = False
        self.fields['manga'].required = False


class TecidoForm(_NomeUnicoMixin, _FilialFormMixin, forms.ModelForm):
    class Meta:
        from .models import Tecido
        model = Tecido
        fields = [
            'nome', 'composicao', 'gramatura', 'largura_cm',
            'fornecedor', 'observacao', 'ativo',
        ]
        widgets = {
            'nome': forms.TextInput(attrs={'placeholder': 'Ex.: Dry'}),
            'composicao': forms.TextInput(attrs={'placeholder': 'Ex.: 100% Poliéster'}),
            'observacao': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, filial=None, **kwargs):
        from apps.cadastros.models import Fornecedor
        super().__init__(*args, filial=filial, **kwargs)
        self.fields['fornecedor'].required = False
        self.fields['fornecedor'].queryset = (
            Fornecedor.objects.for_filial(filial).filter(ativo=True).order_by('razao_social')
            if filial else Fornecedor.objects.none()
        )


class CategoriaForm(_FilialFormMixin, forms.ModelForm):
    class Meta:
        from .models import Categoria
        model = Categoria
        fields = ['nome', 'pai', 'observacao', 'ativo']
        widgets = {'observacao': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, filial=None, **kwargs):
        from .models import Categoria
        super().__init__(*args, filial=filial, **kwargs)
        self.fields['pai'].required = False
        # Só categorias raiz podem ser pai: um terceiro nível existe no
        # model, mas expor isso na tela confundiria mais do que ajudaria.
        qs = Categoria.objects.filter(filial=filial, ativo=True, pai__isnull=True)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)  # não pode ser pai de si mesma
        self.fields['pai'].queryset = qs

    def clean(self):
        from .models import Categoria
        dados = super().clean()
        nome = (dados.get('nome') or '').strip()
        pai = dados.get('pai')
        qs = Categoria.objects.filter(filial=self.filial, pai=pai, nome__iexact=nome)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            onde = f'dentro de {pai.nome}' if pai else 'como categoria raiz'
            self.add_error('nome', f'Já existe "{nome}" {onde}.')
        return dados


class PersonalizacaoIndividualForm(_FilialFormMixin, forms.ModelForm):
    """Uma pessoa da lista (jogador, aluno, funcionário)."""

    class Meta:
        from .models import PersonalizacaoIndividual
        model = PersonalizacaoIndividual
        fields = ['item', 'tamanho', 'nome', 'numero', 'observacoes']
        widgets = {
            'nome': forms.TextInput(attrs={'placeholder': 'Ex.: SILVA'}),
            'numero': forms.TextInput(attrs={'placeholder': 'Ex.: 10'}),
            'observacoes': forms.TextInput(attrs={'placeholder': 'Detalhe desta peça'}),
        }

    def __init__(self, *args, filial=None, pedido=None, **kwargs):
        from .models import Tamanho
        super().__init__(*args, filial=filial, **kwargs)
        self.pedido = pedido
        if pedido is not None:
            self.fields['item'].queryset = pedido.itens.all()
        self.fields['tamanho'].queryset = Tamanho.objects.filter(filial=filial, ativo=True)

    def clean(self):
        dados = super().clean()
        # Sem nome nem número a peça não é identificável na produção — é uma
        # linha que ninguém sabe de quem é.
        if not (dados.get('nome') or '').strip() and not (dados.get('numero') or '').strip():
            self.add_error('nome', 'Informe ao menos o nome ou o número.')
        return dados
