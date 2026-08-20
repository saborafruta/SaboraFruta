"""Formulários do vertical Moda."""
from django import forms
from django.db import models

from .models import (
    CapacidadeSetor, Cor, Encaixe, FichaTecnica, Grade, ImagemFicha,
    ItemPedidoProducao, MaterialFicha, MockupVisual, Operacao, OperacaoRoteiro,
    PedidoProducao, Personalizacao, ProdutoModa, RegistroCorte, Roteiro,
    Tamanho, VisualItemPedido,
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

        # O select de produto passa a oferecer TAMBÉM o cadastro de
        # produtos do ERP -- ver `_opcoes_de_produto`.
        self._preparar_produto(filial)
        # Preço não é obrigatório aqui: o comercial monta a ficha antes de
        # fechar valor, e exigir agora travaria o cadastro do produto.
        self.fields['valor_unitario'].required = False
        # Em branco, o item herda do modelo na gravação — por isso não são
        # obrigatórios aqui.
        self.fields['gola'].required = False
        self.fields['manga'].required = False

    def _preparar_produto(self, filial):
        """
        Um select, dois catálogos.

        O PRODUTO DA CONFECÇÃO e o PRODUTO DO ERP são cadastros diferentes
        de propósito: o do ERP é o que se vende, se estoca e vai na nota; o
        da moda é o que se PRODUZ, com modelo, tecido e grade. Mas quem está
        montando o pedido não quer saber disso -- quer achar a camisa que já
        está cadastrada em Cadastros › Produtos.

        Então o campo lista os dois, em grupos separados. Escolher um do ERP
        TRAZ o produto para a confecção na hora (mesma importação da tela de
        produtos, com o vínculo `produto_erp` gravado) e usa o resultado. Não
        é cópia cega nem catálogo duplicado: é o mesmo produto, agora também
        conhecido pela produção.
        """
        from apps.produtos.models import Produto

        self.fields['produto'] = forms.ChoiceField(
            required=False, label='Produto',
            choices=self._opcoes_de_produto(filial),
            widget=forms.Select(attrs={'class': 'form-input'}),
            help_text=(
                'Do catálogo da confecção ou do cadastro de produtos do ERP. '
                'Sem produto, descreva o item abaixo.'
            ),
        )
        if self.instance.pk and self.instance.produto_id:
            self.initial['produto'] = f'moda:{self.instance.produto_id}'

    def _opcoes_de_produto(self, filial) -> list:
        from apps.moda.services.importar_produtos import ImportarProdutosService
        from .models import ProdutoModa

        da_moda = [
            (f'moda:{p.pk}', f'{p.codigo} — {p.nome}' if p.codigo else p.nome)
            for p in ProdutoModa.objects.for_filial(filial).filter(ativo=True)
            .order_by('nome')
        ] if filial else []

        # Os do ERP que ainda não vieram. O serviço já sabe descartar os
        # que têm vínculo ou código igual -- sem isso, o mesmo produto
        # apareceria duas vezes na mesma lista.
        do_erp = [
            (f'erp:{p.pk}', f'{getattr(p, "codigo", "") or ""} — {p.descricao}'.strip(' —'))
            for p in (ImportarProdutosService.disponiveis(filial) if filial else [])
        ]

        opcoes = [('', '--------- escolha ou descreva abaixo')]
        if da_moda:
            opcoes.append(('Catálogo da confecção', da_moda))
        if do_erp:
            opcoes.append(('Cadastro de produtos do ERP', do_erp))
        return opcoes

    def clean_produto(self):
        """A escolha vira um `ProdutoModa` de verdade — importando, se preciso."""
        from apps.core.services.exceptions import DomainError
        from apps.moda.services.importar_produtos import BuscaProdutos

        # A resolução mora no serviço, e não aqui: a ficha técnica usa o
        # MESMO campo, e duas cópias divergiriam -- uma delas passando a
        # aceitar produto de outra filial, ou a importar duas vezes.
        bruto = str(self.cleaned_data.get('produto') or '').strip()
        try:
            produto = BuscaProdutos.resolver(self.filial, bruto)
        except DomainError as erro:
            raise forms.ValidationError(str(erro))

        # Guardado para a tela avisar que o produto foi TRAZIDO do
        # cadastro do ERP -- senão ninguém entende por que ele passou a
        # aparecer no catálogo da confecção.
        self.produto_importado = produto if bruto.startswith('erp:') else None
        return produto

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

# ══════════════════════════════════════════════════════════════════════
# ENGENHARIA — FICHA TÉCNICA
# ══════════════════════════════════════════════════════════════════════

class FichaTecnicaForm(forms.ModelForm):
    """
    Cabeçalho da ficha.

    Não tem modelo, coleção, tecido nem grade: esses campos são do produto e
    repeti-los aqui daria duas verdades para a mesma informação. A tela lê
    do produto.
    """

    class Meta:
        model = FichaTecnica
        fields = ['produto', 'versao', 'status', 'descricao', 'desenho_tecnico', 'observacoes']
        # O `verbose_name` automático do Django tira o acento ("Versao",
        # "Descricao", "Observacoes"). Numa tela que o usuário lê, escrever
        # errado é ruído -- e ruído que ele acha que é defeito do sistema.
        labels = {
            'produto': 'Produto',
            'versao': 'Versão',
            'status': 'Situação da ficha',
            'descricao': 'Especificação técnica',
            'desenho_tecnico': 'Desenho técnico',
            'observacoes': 'Observações',
        }
        help_texts = {
            'versao': 'Suba a versão quando mudar consumo ou material — o custo muda junto.',
            'status': 'Só a ficha aprovada deveria descer para a fábrica.',
            'descricao': 'Modelagem, costura, acabamentos, tolerâncias.',
            'desenho_tecnico': 'PNG, JPG, PDF, CDR ou AI. Em branco, usa o desenho do produto.',
            'observacoes': 'Recado para quem for produzir.',
        }
        widgets = {
            'descricao': forms.Textarea(attrs={
                'rows': 4,
                'placeholder': 'Modelagem, tipo de costura, acabamentos, tolerâncias de medida.',
            }),
            'observacoes': forms.Textarea(attrs={'rows': 2}),
            'versao': forms.NumberInput(attrs={'min': 1}),
        }

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.filial = filial

        produtos = (
            ProdutoModa.objects.filter(filial=filial, ativo=True)
            .select_related('modelo', 'colecao', 'tecido', 'grade')
            .order_by('codigo')
        )
        # Produto que já tem ficha sai da lista -- é OneToOne, e deixá-lo
        # ali só renderia um erro de integridade depois de preencher tudo.
        if self.instance.pk:
            produtos = produtos.filter(
                models.Q(ficha__isnull=True) | models.Q(ficha=self.instance)
            )
        else:
            produtos = produtos.filter(ficha__isnull=True)

        # A lista ainda vai para a tela: é ela que conta quantos produtos
        # existem e quantos já têm ficha, e é o que explica a tela vazia.
        self.produtos_disponiveis = list(produtos)

        # O CAMPO É CAIXA DE BUSCA, e enxerga os DOIS catálogos: o da
        # confecção e Cadastros › Produtos. Um select só com os produtos
        # de moda fica VAZIO numa base recém-instalada -- a tela pedia um
        # produto que não existia em lugar nenhum.
        if not self.instance.pk:
            self.fields['produto'] = forms.CharField(
                # `required=False` de propósito: obrigatório aqui daria o
                # "Este campo é obrigatório" genérico do Django, e quem lê
                # não sabe qual campo é (ele é escondido). Quem cobra é o
                # `clean_produto`, que diz "escolha o produto desta ficha".
                required=False, label='Produto',
                # CharField, e não ChoiceField: as opções chegam por busca,
                # e um `choices` vazio recusaria a escolha ANTES de o
                # `clean_produto` ver o valor -- com a mensagem inútil
                # "faça uma escolha válida". Quem valida aqui é o clean.
                widget=forms.HiddenInput(),
            )

        for campo in self.fields.values():
            css = campo.widget.attrs.get('class', '')
            if 'form-input' not in css:
                campo.widget.attrs['class'] = (css + ' form-input').strip()

    def clean_produto(self):
        """A escolha da caixa de busca vira um `ProdutoModa` de verdade."""
        from apps.core.services.exceptions import DomainError
        from apps.moda.services.importar_produtos import BuscaProdutos

        if self.instance.pk:
            # Na edição o produto não muda: a ficha É dele.
            return self.instance.produto

        bruto = str(self.data.get('produto') or '').strip()
        if not bruto:
            raise forms.ValidationError('Escolha o produto desta ficha.')
        try:
            produto = BuscaProdutos.resolver(self.filial, bruto)
        except DomainError as erro:
            raise forms.ValidationError(str(erro))

        # Ficha é OneToOne. O produto pode ter ganhado ficha entre a
        # abertura da tela e a gravação -- e o erro de integridade cru
        # não diz nada a quem está preenchendo.
        if getattr(produto, 'ficha', None) is not None:
            raise forms.ValidationError(
                f'“{produto.nome}” já tem ficha técnica. Abra a ficha dele para editar.'
            )
        return produto


class MaterialFichaForm(forms.ModelForm):
    """Uma linha da lista de materiais."""

    class Meta:
        model = MaterialFicha
        fields = [
            'tipo', 'descricao', 'codigo', 'produto_estoque', 'unidade',
            'consumo', 'perda', 'custo_unitario', 'observacao',
        ]
        widgets = {
            'descricao': forms.TextInput(attrs={'placeholder': 'Ex.: Malha Dry Fit 100% poliéster 140g'}),
            'codigo': forms.TextInput(attrs={'placeholder': 'Código no estoque'}),
            'consumo': forms.NumberInput(attrs={'step': '0.0001', 'min': '0'}),
            'perda': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'max': '100'}),
            'custo_unitario': forms.NumberInput(attrs={'step': '0.0001', 'min': '0'}),
            'observacao': forms.TextInput(attrs={'placeholder': 'Opcional'}),
        }

    def __init__(self, *args, **kwargs):
        from apps.produtos.models import Produto

        super().__init__(*args, **kwargs)

        # Sem escopo de filial aqui: o produto de estoque e' do catalogo da
        # empresa, e o saldo e' que e' por filial.
        self.fields['produto_estoque'].queryset = Produto.objects.filter(
            ativo=True,
        ).order_by('descricao')
        self.fields['produto_estoque'].required = False
        self.fields['produto_estoque'].empty_label = 'sem ligacao com estoque'

        for campo in self.fields.values():
            css = campo.widget.attrs.get('class', '')
            if 'form-input' not in css:
                campo.widget.attrs['class'] = (css + ' form-input').strip()

    def clean_descricao(self):
        descricao = (self.cleaned_data['descricao'] or '').strip()
        if not descricao:
            raise forms.ValidationError('Descreva o material — sem isso a ficha não diz o que comprar.')
        return descricao


class ImagemFichaForm(forms.ModelForm):
    class Meta:
        model = ImagemFicha
        fields = ['imagem', 'legenda']
        widgets = {
            'legenda': forms.TextInput(attrs={'placeholder': 'Ex.: detalhe da gola'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['legenda'].required = False
        for campo in self.fields.values():
            css = campo.widget.attrs.get('class', '')
            if 'form-input' not in css:
                campo.widget.attrs['class'] = (css + ' form-input').strip()

# ══════════════════════════════════════════════════════════════════════
# ENGENHARIA — OPERAÇÕES E ROTEIRO
# ══════════════════════════════════════════════════════════════════════

class OperacaoForm(_FilialFormMixin, forms.ModelForm):
    """Uma operação do catálogo da fábrica."""

    class Meta:
        model = Operacao
        fields = [
            'nome', 'sequencia', 'setor', 'maquina', 'responsavel',
            'tempo_padrao', 'tipo_custo', 'custo', 'capacidade',
            'observacao', 'ativo',
        ]
        widgets = {
            'nome': forms.TextInput(attrs={'placeholder': 'Ex.: Costura'}),
            'maquina': forms.TextInput(attrs={'placeholder': 'Ex.: Overloque'}),
            'responsavel': forms.TextInput(attrs={'placeholder': 'Pessoa ou equipe'}),
            'tempo_padrao': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
            'custo': forms.NumberInput(attrs={'step': '0.0001', 'min': '0'}),
            'capacidade': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
            'sequencia': forms.NumberInput(attrs={'min': 0}),
            'observacao': forms.Textarea(attrs={'rows': 2}),
        }

    def clean_nome(self):
        nome = (self.cleaned_data['nome'] or '').strip()
        qs = Operacao.objects.filter(filial=self.filial, nome__iexact=nome)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError('Já existe uma operação com esse nome nesta filial.')
        return nome


class RoteiroForm(forms.ModelForm):
    """Cabeçalho do roteiro — o produto a que ele pertence."""

    class Meta:
        model = Roteiro
        fields = ['produto', 'versao', 'observacoes']
        widgets = {
            'versao': forms.NumberInput(attrs={'min': 1}),
            'observacoes': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.filial = filial

        produtos = (
            ProdutoModa.objects.filter(filial=filial, ativo=True)
            .select_related('modelo', 'colecao', 'tecido', 'grade')
            .order_by('codigo')
        )
        # Mesmo motivo da ficha: é OneToOne, e oferecer um produto que já tem
        # roteiro só renderia erro de integridade depois de preencher tudo.
        if self.instance.pk:
            produtos = produtos.filter(
                models.Q(roteiro__isnull=True) | models.Q(roteiro=self.instance)
            )
            self.fields['produto'].disabled = True
        else:
            produtos = produtos.filter(roteiro__isnull=True)
        self.fields['produto'].queryset = produtos
        self.fields['produto'].empty_label = 'Escolha o produto'

        for campo in self.fields.values():
            css = campo.widget.attrs.get('class', '')
            if 'form-input' not in css:
                campo.widget.attrs['class'] = (css + ' form-input').strip()


class OperacaoRoteiroForm(forms.ModelForm):
    """
    Uma etapa do roteiro.

    Tempo, custo e capacidade ficam vazios por padrão de propósito: vazio
    significa "usa o padrão da operação", que é o caso comum. Pré-preencher
    com o valor do catálogo transformaria toda etapa numa exceção fixa, e
    corrigir o catálogo depois não corrigiria mais nada.
    """

    class Meta:
        model = OperacaoRoteiro
        fields = [
            'operacao', 'sequencia', 'tempo_padrao', 'custo',
            'capacidade', 'maquina', 'responsavel', 'observacao',
        ]
        widgets = {
            'sequencia': forms.NumberInput(attrs={'min': 0}),
            'tempo_padrao': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'placeholder': 'padrão'}),
            'custo': forms.NumberInput(attrs={'step': '0.0001', 'min': '0', 'placeholder': 'padrão'}),
            'capacidade': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'placeholder': 'padrão'}),
            'maquina': forms.TextInput(attrs={'placeholder': 'padrão'}),
            'responsavel': forms.TextInput(attrs={'placeholder': 'padrão'}),
        }

    def __init__(self, *args, filial=None, roteiro=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.roteiro = roteiro

        qs = Operacao.objects.filter(filial=filial, ativo=True).order_by('sequencia', 'nome')
        # Operação já no roteiro sai da lista: `unique_together` a recusaria
        # de qualquer forma, e ver a opção ali sugere que dá para repetir.
        if roteiro is not None:
            qs = qs.exclude(etapas__roteiro=roteiro)
        self.fields['operacao'].queryset = qs
        self.fields['operacao'].empty_label = 'Escolha a operação'

        for campo in self.fields.values():
            css = campo.widget.attrs.get('class', '')
            if 'form-input' not in css:
                campo.widget.attrs['class'] = (css + ' form-input').strip()

class CapacidadeSetorForm(forms.ModelForm):
    """Quanto um setor entrega por semana."""

    class Meta:
        model = CapacidadeSetor
        fields = ['setor', 'postos', 'horas_dia', 'dias_semana', 'eficiencia', 'observacao']
        widgets = {
            'postos': forms.NumberInput(attrs={'min': 1}),
            'horas_dia': forms.NumberInput(attrs={'step': '0.5', 'min': '0', 'max': '24'}),
            'dias_semana': forms.NumberInput(attrs={'min': 1, 'max': 7}),
            'eficiencia': forms.NumberInput(attrs={'step': '1', 'min': '1', 'max': '100'}),
            'observacao': forms.TextInput(attrs={'placeholder': 'Opcional'}),
        }

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.filial = filial
        for campo in self.fields.values():
            css = campo.widget.attrs.get('class', '')
            if 'form-input' not in css:
                campo.widget.attrs['class'] = (css + ' form-input').strip()

    def clean_setor(self):
        setor = self.cleaned_data['setor']
        # `unique_together` recusaria de qualquer forma, mas com erro de
        # banco. Aqui a mensagem diz o que fazer: editar a linha que existe.
        qs = CapacidadeSetor.objects.filter(filial=self.filial, setor=setor)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError(
                'Este setor já tem capacidade cadastrada. Edite a linha existente.'
            )
        return setor

class RegistroCorteForm(_FilialFormMixin, forms.ModelForm):
    """Um enfesto. A grade vem em campos à parte, na própria tela."""

    campos_por_filial = {}  # preenchido em __init__, para evitar import circular

    class Meta:
        model = RegistroCorte
        fields = [
            'ordem', 'tecido', 'cor', 'lote', 'data', 'responsavel', 'status',
            'encaixe', 'largura_tecido', 'comprimento_encaixe', 'folhas', 'aproveitamento',
            'consumo_planejado', 'consumo_real', 'observacao',
        ]
        widgets = {
            'data': forms.DateInput(attrs={'type': 'date'}),
            'lote': forms.TextInput(attrs={'placeholder': 'Ex.: RL-4471'}),
            'largura_tecido': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
            'comprimento_encaixe': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
            'folhas': forms.NumberInput(attrs={'min': 1}),
            'aproveitamento': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'max': '100'}),
            'consumo_planejado': forms.NumberInput(attrs={'step': '0.0001', 'min': '0', 'placeholder': 'da ficha'}),
            'consumo_real': forms.NumberInput(attrs={'step': '0.0001', 'min': '0'}),
            'observacao': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, filial=None, **kwargs):
        from .models import Cor as CorModel, OrdemProducao, Tecido

        self.campos_por_filial = {'cor': CorModel, 'tecido': Tecido}
        super().__init__(*args, filial=filial, **kwargs)

        # Ordens encerradas ficam fora: não se corta para uma OP concluída
        # ou cancelada, e oferecer a opção só produz registro órfão.
        ordens = OrdemProducao.objects.filter(filial=filial).exclude(
            status__in=OrdemProducao.STATUS_ENCERRADOS,
        ).select_related('pedido__cliente').order_by('-ano', '-sequencial')
        if self.instance.pk:
            ordens = ordens | OrdemProducao.objects.filter(pk=self.instance.ordem_id)
            self.fields['ordem'].disabled = True
        self.fields['ordem'].queryset = ordens
        self.fields['ordem'].empty_label = 'Escolha a ordem'

        for nome in ('tecido', 'cor'):
            self.fields[nome].required = False
            self.fields[nome].empty_label = 'do item da ordem'

        from .models import Encaixe as EncaixeModel

        self.fields['encaixe'].queryset = EncaixeModel.objects.filter(
            filial=filial, ativo=True,
        ).order_by('nome')
        self.fields['encaixe'].required = False
        self.fields['encaixe'].empty_label = 'sem encaixe - aproveitamento a mao'

class EncaixeForm(_FilialFormMixin, forms.ModelForm):
    """
    Um risco.

    Aproveitamento e perda nao estao aqui: sao calculados de comprimento,
    largura e area util. Oferecer o campo faria a tela aceitar um numero que
    contradiz a propria conta ao lado.
    """

    campos_por_filial = {}  # preenchido em __init__, para evitar import circular

    class Meta:
        model = Encaixe
        fields = [
            'nome', 'produto', 'modelo', 'tecido',
            'comprimento', 'largura_tecido', 'quantidade_pecas', 'area_util',
            'arquivo', 'observacao', 'ativo',
        ]
        widgets = {
            'nome': forms.TextInput(attrs={'placeholder': 'Ex.: Camisa gola redonda - P ao GG, 1,60 m'}),
            'comprimento': forms.NumberInput(attrs={'step': '0.001', 'min': '0'}),
            'largura_tecido': forms.NumberInput(attrs={'step': '0.001', 'min': '0'}),
            'quantidade_pecas': forms.NumberInput(attrs={'min': 0}),
            'area_util': forms.NumberInput(attrs={'step': '0.0001', 'min': '0'}),
            'observacao': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, filial=None, **kwargs):
        from .models import Modelo, ProdutoModa as Produto, Tecido

        self.campos_por_filial = {'modelo': Modelo, 'tecido': Tecido}
        super().__init__(*args, filial=filial, **kwargs)

        self.fields['produto'].queryset = Produto.objects.filter(
            filial=filial, ativo=True,
        ).order_by('codigo')
        for nome in ('produto', 'modelo', 'tecido'):
            self.fields[nome].required = False
            self.fields[nome].empty_label = '-'

    def clean_nome(self):
        nome = (self.cleaned_data['nome'] or '').strip()
        qs = Encaixe.objects.filter(filial=self.filial, nome__iexact=nome)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError('Ja existe um encaixe com esse nome nesta filial.')
        return nome

    def clean(self):
        dados = super().clean()
        util = dados.get('area_util') or 0
        comprimento = dados.get('comprimento') or 0
        largura = dados.get('largura_tecido') or 0
        utilizada = comprimento * largura

        # Moldes que nao cabem no risco significam medida errada em algum dos
        # tres campos, e passariam como aproveitamento acima de 100%.
        if util and utilizada and util > utilizada:
            self.add_error(
                'area_util',
                f'A area util ({util} m2) nao cabe na area utilizada '
                f'({utilizada} m2). Confira comprimento, largura e a area do CAD.',
            )
        return dados
