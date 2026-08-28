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

    def test_a_lista_sobe_quando_nao_cabe_embaixo(self):
        """
        Este formulário fica no pé da página. Abrindo sempre para baixo, o
        rodapé cortava os resultados — e quem via um só concluía que só existia
        um produto.
        """
        html = self.client.get(self.url).content.decode()

        self.assertIn("paraCima ? 'bottom-full mb-1' : 'top-full mt-1'", html)

    def test_a_lista_diz_quantos_produtos_vieram(self):
        """Lista rolável sem contagem parece lista curta."""
        html = self.client.get(self.url).content.decode()

        self.assertIn("resultados.length + (resultados.length === 1", html)

    def test_a_busca_comeca_na_primeira_letra(self):
        html = self.client.get(self.url).content.decode()

        self.assertNotIn('q.length < 2', html, 'a tela voltou a esperar duas letras')

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

    def test_uma_letra_sozinha_o_servidor_ignora(self):
        """Sem pedir, o endpoint só responde a partir de duas letras."""
        dados = self.client.get(self.busca, {'q': 'p'}).json()

        self.assertEqual(dados['results'], [])

    def test_com_prefixo_uma_letra_ja_traz_produto(self):
        """
        A TELA DEPENDE DISSO. Quem digita "p" e não vê nada acontecer conclui
        que a busca não existe; `prefixo=1` é o que destrava a primeira letra.
        """
        dados = self.client.get(self.busca, {'q': 'p', 'prefixo': '1'}).json()

        achados = [r['label'] for r in dados['results']]
        self.assertIn('Polpa de Manga 1kg', achados)

    def test_a_tela_pede_a_busca_de_uma_letra(self):
        html = self.client.get(self.url).content.decode()

        # A URL MONTADA, e nao a palavra solta: "prefixo=1" tambem aparece no
        # comentario do script, e procura-la no HTML passaria sem a busca usar.
        self.assertIn("'?prefixo=1&q=' + encodeURIComponent(q)", html,
                      'a tela voltou a exigir duas letras')

    def test_a_letra_filtra_e_nao_traz_o_catalogo_inteiro(self):
        """Uma letra abre a busca; não desliga o filtro."""
        outro = Produto.objects.create(
            filial=self.filial, unidade_medida=self.unidade,
            descricao='Caixa de papelao', codigo='CX1', ncm='48191000',
        )
        ProdutoFilial.objects.create(produto=outro, filial=self.filial)

        dados = self.client.get(self.busca, {'q': 'p', 'prefixo': '1'}).json()

        achados = [r['label'] for r in dados['results']]
        self.assertIn('Polpa de Manga 1kg', achados)
        self.assertIn('Caixa de papelao', achados)  # "papelao" tem p
        dados = self.client.get(self.busca, {'q': 'pol', 'browse': '1'}).json()
        self.assertEqual([r['label'] for r in dados['results']], ['Polpa de Manga 1kg'])

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
        CAMPO EXIGIDO QUE NINGUÉM DESENHA não tem como ser preenchido — foi
        assim que volumes, obrigatório e invisível, fez todo "Adicionar Item"
        voltar com "Revise os dados do item".
        """
        html = self.client.get(self.url).content.decode()
        obrigatorios = [
            nome for nome, campo in ItemPedidoExpedicaoForm().fields.items()
            if campo.required
        ]

        self.assertTrue(obrigatorios, 'premissa mudou: o formulario nao exige nada')
        for nome in obrigatorios:
            self.assertIn(f'id="id_{nome}"', html, f'{nome} e exigido mas nao aparece na tela')

    def test_volume_e_peso_aparecem_mesmo_sendo_opcionais(self):
        """
        DEIXARAM DE SER EXIGIDOS, E NEM POR ISSO SOMEM: o volume multiplica o
        preço para dar o valor da linha, e o peso é o que a balança da doca e
        o MDF-e vão cobrar. Escondê-los porque o formulário aceita vazio
        empurraria a conta para depois, quando o caminhão já está carregado.
        """
        html = self.client.get(self.url).content.decode()

        for nome in ('volumes', 'peso_kg', 'valor_unitario'):
            self.assertFalse(ItemPedidoExpedicaoForm().fields[nome].required)
            self.assertIn(f'id="id_{nome}"', html)

    def test_o_erro_diz_qual_campo_faltou(self):
        """"Revise os dados" sem dizer o quê manda a pessoa procurar sozinha."""
        resposta = self.client.post(
            reverse('logistica:pedido-expedicao-item-create', args=[self.pedido.pk]),
            {'produto_nome': 'Sem quantidade', 'unidade': 'UN', 'volumes': '1'},
            follow=True,
        )

        self.assertEqual(self.pedido.itens.count(), 0)
        # Olhar a MENSAGEM, e nao a pagina: o rotulo do campo tambem esta' no
        # HTML, e procura-lo la' passaria mesmo sem aviso nenhum.
        avisos = [str(m) for m in resposta.context['messages']]
        self.assertTrue(
            any('Quantidade' in a for a in avisos),
            f'o aviso nao disse qual campo faltou: {avisos}',
        )

    def test_item_sem_volume_entra_como_carga_avulsa(self):
        """
        VOLUME OPCIONAL É DECISÃO: granel e devolução de vasilhame não se
        contam em caixas, e exigir o campo faria a pessoa inventar um número
        para o item entrar.
        """
        self.client.post(
            reverse('logistica:pedido-expedicao-item-create', args=[self.pedido.pk]),
            {'produto_nome': 'Polpa a granel', 'quantidade': '12', 'unidade': 'KG',
             'valor_unitario': '2,50'},
            follow=True,
        )

        item = self.pedido.itens.latest('id')
        self.assertEqual(item.volumes, Decimal('0'))
        # Sem volume, a quantidade manda na conta do valor.
        self.assertEqual(item.valor_total, Decimal('30.00'))
