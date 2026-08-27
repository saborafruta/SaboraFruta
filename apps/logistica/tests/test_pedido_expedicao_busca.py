"""
A busca de produto no item do pedido de expedição.

QUEM MONTA O PEDIDO NÃO DECORA O CATÁLOGO. Digitar o nome à mão erra o
produto, erra a unidade e erra o código — e o erro só aparece na doca, com o
caminhão encostado.

O ITEM GUARDA TEXTO, e não uma chave para o produto. A busca é conveniência,
não trava: quem precisar lançar algo que não está no cadastro continua
podendo, que é o que faz o pedido de expedição funcionar para carga avulsa.
"""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.cadastros.models import Cliente, ClienteFilial
from apps.logistica.forms import ItemPedidoExpedicaoForm
from apps.logistica.models import ItemPedidoExpedicao, PedidoExpedicao
from apps.produtos.models import Produto, ProdutoFilial
from apps.produtos.models.unidade import UnidadeMedida, UnidadeMedidaFilial


class BuscaDeProdutoNoItemTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Expedicao LTDA', nome_fantasia='Expedicao',
            cnpj='63345678000191', segmento='polpa_frutas',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='63345678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='KG', descricao='Quilograma',
            tipo=UnidadeMedida.Tipo.PESO,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Mercado Central',
            cpf_cnpj='12345678901',
        )
        ClienteFilial.objects.create(cliente=cls.cliente, filial=cls.filial)
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='exp@carga.local', nome='Expedicao', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.pedido = PedidoExpedicao.objects.create(
            filial=cls.filial, numero=1, cliente=cls.cliente,
        )
        cls.produto = Produto.objects.create(
            filial=cls.filial, unidade_medida=cls.unidade,
            descricao='Polpa de Manga 1kg', codigo='PM1', ncm='20079900',
        )
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.filial)

    def setUp(self):
        self.client.force_login(self.usuario)
        self.url = reverse('logistica:pedido-expedicao-detail', args=[self.pedido.pk])
        self.busca = reverse('estoque:produto-estoque-search-json')

    # ── A tela ───────────────────────────────────────────────────────────

    def test_a_caixa_de_busca_aponta_para_o_catalogo(self):
        html = self.client.get(self.url).content.decode()

        self.assertIn(self.busca, html)

    def test_os_campos_que_a_busca_preenche_existem_na_tela(self):
        """
        O script escreve nos campos POR ID. Renomear um campo do formulário ou
        dar-lhe um prefixo não quebra nada visível -- a busca simplesmente para
        de preencher, e ninguém percebe até o pedido sair com a unidade errada.
        """
        html = self.client.get(self.url).content.decode()

        for campo in ('produto_nome', 'produto_codigo', 'unidade', 'peso_kg'):
            self.assertIn(f'id="id_{campo}"', html, f'o campo {campo} mudou de id')

    def test_a_tela_nao_vaza_sintaxe_de_template(self):
        html = self.client.get(self.url).content.decode()

        for resto in ('{#', '#}', '{%', '%}'):
            self.assertNotIn(resto, html, 'vazou sintaxe de template no HTML')

    # ── O catálogo ───────────────────────────────────────────────────────

    def test_o_catalogo_responde_ao_termo_digitado(self):
        dados = self.client.get(self.busca, {'q': 'manga'}).json()

        achados = [r['label'] for r in dados['results']]
        self.assertIn('Polpa de Manga 1kg', achados)

    def test_a_resposta_traz_o_que_a_tela_preenche(self):
        r = self.client.get(self.busca, {'q': 'manga'}).json()['results'][0]

        self.assertEqual(r['label'], 'Polpa de Manga 1kg')
        self.assertEqual(r['detalhe'], 'PM1')
        self.assertIn('KG', r['unidade'])

    def test_produto_de_outra_filial_nao_aparece(self):
        outra = Filial.objects.create(
            empresa=self.empresa, razao_social='Segunda',
            cnpj='31345678000677', uf='RN', cidade='Mossoro',
        )
        alheio = Produto.objects.create(
            filial=outra, unidade_medida=self.unidade,
            descricao='Polpa de Manga alheia', codigo='PM9', ncm='20079900',
        )
        ProdutoFilial.objects.create(produto=alheio, filial=outra)

        dados = self.client.get(self.busca, {'q': 'manga'}).json()

        self.assertEqual(len(dados['results']), 1)

    def test_uma_letra_nao_varre_o_catalogo(self):
        """Buscar com uma letra devolveria o catálogo inteiro a cada tecla."""
        dados = self.client.get(self.busca, {'q': 'm'}).json()

        self.assertEqual(dados['results'], [])

    # ── O que a tela precisa ajustar ─────────────────────────────────────

    def test_a_unidade_do_catalogo_nao_cabe_no_campo_do_item(self):
        """
        O catálogo diz "KG (Quilograma)"; o item guarda 10 caracteres. Por isso
        a tela manda só a sigla -- copiar o rótulo inteiro faria o formulário
        recusar o item por tamanho, e o autocomplete quebraria justamente o que
        veio facilitar.

        Se um dia o rótulo couber, este teste cai e a gambiarra pode sair.
        """
        rotulo = self.client.get(self.busca, {'q': 'manga'}).json()['results'][0]['unidade']
        limite = ItemPedidoExpedicao._meta.get_field('unidade').max_length

        self.assertGreater(len(rotulo), limite, 'o rotulo agora cabe: revise o corte na tela')
        self.assertLessEqual(len(rotulo.split(' ')[0]), limite)

    def test_o_item_aceita_produto_que_nao_esta_no_catalogo(self):
        """
        A busca é conveniência, não trava. Carga avulsa lança item que não tem
        cadastro, e exigir catálogo aqui pararia o caminhão.
        """
        self.client.post(
            reverse('logistica:pedido-expedicao-item-create', args=[self.pedido.pk]),
            {
                'produto_nome': 'Pallet vazio devolvido',
                'quantidade': '2', 'unidade': 'UN', 'volumes': '1',
                'peso_kg': '0', 'valor_unitario': '0',
            },
        )

        item = self.pedido.itens.latest('id')
        self.assertEqual(item.produto_nome, 'Pallet vazio devolvido')
        self.assertEqual(item.quantidade, Decimal('2'))

    def test_a_tela_desenha_todo_campo_que_o_formulario_exige(self):
        """
        VOLUMES ERA OBRIGATÓRIO E NÃO APARECIA na tela: todo "Adicionar Item"
        voltava com "Revise os dados do item" e o item não entrava no pedido.
        Campo exigido que ninguém desenha não tem como ser preenchido.
        """
        html = self.client.get(self.url).content.decode()
        obrigatorios = [
            nome for nome, campo in ItemPedidoExpedicaoForm().fields.items()
            if campo.required
        ]

        self.assertIn('volumes', obrigatorios, 'premissa mudou: volumes deixou de ser exigido')
        for nome in obrigatorios:
            self.assertIn(f'id="id_{nome}"', html, f'{nome} e exigido mas nao aparece na tela')

    def test_o_erro_diz_qual_campo_faltou(self):
        """"Revise os dados" sem dizer o quê é o que escondeu o volumes."""
        resposta = self.client.post(
            reverse('logistica:pedido-expedicao-item-create', args=[self.pedido.pk]),
            {'produto_nome': 'Sem volumes', 'quantidade': '1', 'unidade': 'UN',
             'peso_kg': '0', 'valor_unitario': '0'},
            follow=True,
        )

        self.assertEqual(self.pedido.itens.count(), 0)
        # Olhar a MENSAGEM, e nao a pagina: "Volumes" tambem e o rotulo do
        # campo, e procura-lo no HTML passaria mesmo sem aviso nenhum.
        avisos = [str(m) for m in resposta.context['messages']]
        self.assertTrue(
            any('Volumes' in a for a in avisos),
            f'o aviso nao disse qual campo faltou: {avisos}',
        )
