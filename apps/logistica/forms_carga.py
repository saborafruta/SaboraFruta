"""
Os formulários que compõem a carga.

TRÊS BOTÕES, TRÊS FORMULÁRIOS — e não um só com um seletor de natureza. Não é
enfeite: cada operação pede coisas diferentes. Venda tem comprador e pode vir
de um pedido; venda fora do estabelecimento sai justamente porque ainda não
tem comprador; bonificação tem destinatário mas não cobra dele.

Um formulário genérico obrigaria quem monta a carga a escolher a natureza numa
lista — ou seja, a pensar em CFOP no meio do carregamento. O botão já sabe o
que está sendo carregado, e o formulário só pergunta o que aquela operação
precisa.
"""
from decimal import Decimal

from django import forms

from apps.cadastros.models import Cliente
from apps.fiscal.models import NaturezaOperacao
from apps.logistica.models import ItemCarga
from apps.produtos.models import Produto
from apps.vendas.models.pedido import PedidoVenda

BASE_INPUT_CLASS = 'form-input w-full'

# O que cada botão precisa perguntar. É a tradução da espécie fiscal para a
# pergunta que se faz a quem está carregando o caminhão.
PERFIS = {
    NaturezaOperacao.Especie.VENDA: {
        'titulo': 'Adicionar venda',
        'ajuda': 'Mercadoria que já tem comprador e pedido.',
        'exige_cliente': True,
        'mostra_pedido': True,
        'rotulo_valor': 'Preço de venda',
    },
    NaturezaOperacao.Especie.REMESSA_VENDA_FORA: {
        'titulo': 'Adicionar venda fora do estabelecimento',
        'ajuda': (
            'Mercadoria sem comprador, para vender durante a rota. Sai por '
            'nota de remessa e precisa voltar ou ser vendida.'
        ),
        'exige_cliente': False,
        'mostra_pedido': False,
        # O valor existe mesmo sem venda: e' o que a nota de remessa declara.
        'rotulo_valor': 'Valor para a remessa',
        # LOTE IMPORTA AQUI. A mercadoria vai passar dias na rua e voltar em
        # parte; sem saber qual lote saiu, o que retorna nao tem como voltar
        # para o lote certo, e a rastreabilidade se perde na estrada.
        'mostra_lote': True,
        'etiqueta': 'Mercadoria em venda fora do estabelecimento',
    },
    NaturezaOperacao.Especie.BONIFICACAO: {
        'titulo': 'Adicionar bonificação',
        'ajuda': 'Mercadoria entregue sem cobrança, com destinatário definido.',
        'exige_cliente': True,
        'mostra_pedido': False,
        'rotulo_valor': 'Valor para a nota',
    },
}


class ItemCargaForm(forms.ModelForm):
    """Uma linha da carga, com o que a natureza dela exige."""

    class Meta:
        model = ItemCarga
        fields = [
            'natureza', 'produto', 'lote', 'cliente', 'pedido_venda',
            'quantidade', 'valor_unitario', 'peso_kg', 'observacao',
        ]
        widgets = {'observacao': forms.TextInput()}

    def __init__(self, *args, viagem=None, especie=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.viagem = viagem
        self.especie = especie
        self.perfil = PERFIS.get(especie, {})
        filial = getattr(viagem, 'filial', None)

        naturezas = NaturezaOperacao.objects.for_filial(filial).filter(
            especie=especie, ativo=True,
        )
        self.fields['natureza'].queryset = naturezas
        self.fields['natureza'].empty_label = None
        # QUANDO SO' HA' UMA, NAO SE PERGUNTA. Escolher entre uma opcao so' e'
        # um clique que nao decide nada. Com duas ou mais a escolha e' real --
        # sao CFOPs diferentes -- e ai' o campo aparece.
        self.natureza_unica = naturezas.first() if naturezas.count() == 1 else None
        if self.natureza_unica:
            self.fields['natureza'].initial = self.natureza_unica
            self.fields['natureza'].widget = forms.HiddenInput()
            # NAO E' OBRIGATORIO NO CAMPO: o servidor ja' sabe a resposta, e o
            # `clean` usa a unica natureza. Exigir aqui faria o formulario
            # depender de um input oculto chegar de volta -- e quando ele nao
            # chega, o erro que aparece e' "natureza obrigatoria", que nao diz
            # nada a quem esta' carregando o caminhao.
            self.fields['natureza'].required = False

        self.fields['produto'].queryset = Produto.objects.for_filial(filial).filter(ativo=True)
        # "---------" nao diz o que fazer. O resto da tela ja' usa "escolher".
        self.fields['produto'].empty_label = '— escolher produto —'
        self.fields['lote'].empty_label = '— sem lote —'
        # SO' LOTE COM SALDO: oferecer lote zerado convida a carregar o que nao
        # existe, e o erro so' aparece na baixa de estoque, ja' no fechamento.
        from apps.estoque.models import LoteProduto
        self.fields['lote'].queryset = (
            LoteProduto.objects.filter(filial=filial, quantidade_atual__gt=0)
            .select_related('produto').order_by('data_validade', 'numero_lote')
        )
        self.fields['cliente'].queryset = Cliente.objects.for_filial(filial).filter(ativo=True)
        self.fields['pedido_venda'].queryset = PedidoVenda.objects.filter(filial=filial)

        self.fields['cliente'].required = bool(self.perfil.get('exige_cliente'))
        # "Cliente" e' o nome do campo; "Destinatario" e' o que a operacao
        # pergunta -- na bonificacao quem recebe nao esta' comprando nada.
        self.fields['cliente'].label = 'Destinatário'
        self.fields['produto'].label = 'Produto'
        self.fields['quantidade'].label = 'Quantidade'
        self.fields['cliente'].empty_label = (
            '— escolher cliente —' if self.fields['cliente'].required
            else '— sem comprador ainda —'
        )
        self.fields['pedido_venda'].required = False
        self.fields['pedido_venda'].empty_label = '— sem pedido vinculado —'
        for nome in ('lote', 'peso_kg', 'observacao', 'valor_unitario'):
            self.fields[nome].required = False

        for campo in self.fields.values():
            campo.widget.attrs['class'] = BASE_INPUT_CLASS
        self.fields['quantidade'].widget.attrs.update({'step': '0.001', 'min': '0.001'})
        self.fields['valor_unitario'].widget.attrs['step'] = '0.0001'
        self.fields['observacao'].widget.attrs['placeholder'] = 'Opcional'

    def clean_quantidade(self):
        quantidade = self.cleaned_data.get('quantidade')
        if quantidade is None or quantidade <= Decimal('0'):
            raise forms.ValidationError('A quantidade precisa ser maior que zero.')
        return quantidade

    def clean(self):
        dados = super().clean()
        natureza = dados.get('natureza') or self.natureza_unica
        dados['natureza'] = natureza
        if natureza is None:
            raise forms.ValidationError(
                'Nenhuma natureza de operação cadastrada para esta carga. '
                'Cadastre em Fiscal › Naturezas de operação.'
            )

        # A MERCADORIA SEM COMPRADOR NAO ACEITA CLIENTE. Preencher um faria a
        # nota de remessa sair contra alguem que nao comprou nada.
        if not self.perfil.get('exige_cliente') and dados.get('cliente'):
            self.add_error(
                'cliente',
                'Esta mercadoria sai sem comprador — para venda com destinatário, '
                'use "Adicionar venda".',
            )
        if not self.perfil.get('mostra_pedido'):
            dados['pedido_venda'] = None

        # O pedido tem que ser do mesmo cliente, senao a carga promete a um a
        # mercadoria que outro comprou.
        pedido, cliente = dados.get('pedido_venda'), dados.get('cliente')
        if pedido and cliente and pedido.cliente_id != cliente.pk:
            self.add_error(
                'pedido_venda',
                f'Este pedido é de {pedido.cliente}, e não de {cliente}.',
            )
        return dados
