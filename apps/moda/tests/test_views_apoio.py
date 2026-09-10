"""
Ativar/inativar e excluir nos cadastros de apoio (Tamanhos, Tecidos,
Marcas...) — as duas ações que a tela genérica de cadastro só mostrava
(o selo Ativo/Inativo), sem dar jeito de mudar.

O QUE ESTES TESTES CERCAM:

  · INATIVAR/ATIVAR é toggle, nunca apaga — a saída pra tirar de
    circulação um cadastro já usado sem quebrar quem aponta pra ele;

  · EXCLUIR APAGA DE VERDADE, mas só quando dá: todo FK que aponta pra um
    cadastro de apoio é PROTECT, então excluir um tecido em uso (produto,
    corte, pedido) tem que recusar com uma mensagem, não estourar 500;

  · `next` volta pra onde o botão foi clicado (a tela de Estoque › Tecidos,
    por exemplo), não sempre pro cadastro genérico -- e só um `next`
    seguro (mesmo host) é aceito, senão cai no padrão.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.moda.models import Tamanho, Tecido
from apps.produtos.models import Produto, UnidadeMedida


class ApoioBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao Apoio LTDA', nome_fantasia='Apoio',
            cnpj='63345678000191', segmento='moda_confeccao',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Confeccao Apoio LTDA',
            cnpj='63345678000272', uf='RN', cidade='Natal', is_matriz=True,
        )
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='apoio@teste.local', nome='Apoio', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )

    def setUp(self):
        self.client.force_login(self.usuario)


class ToggleAtivoTests(ApoioBase):

    def test_inativa_um_cadastro_ativo(self):
        tamanho = Tamanho.objects.create(filial=self.filial, sigla='M', nome='Médio', ativo=True)

        self.client.post(
            reverse('moda:apoio-toggle-ativo', args=['produtos', 'tamanhos', tamanho.pk])
        )

        tamanho.refresh_from_db()
        self.assertFalse(tamanho.ativo)

    def test_ativa_um_cadastro_inativo(self):
        tamanho = Tamanho.objects.create(filial=self.filial, sigla='M', nome='Médio', ativo=False)

        self.client.post(
            reverse('moda:apoio-toggle-ativo', args=['produtos', 'tamanhos', tamanho.pk])
        )

        tamanho.refresh_from_db()
        self.assertTrue(tamanho.ativo)

    def test_redireciona_para_o_next_quando_seguro(self):
        tamanho = Tamanho.objects.create(filial=self.filial, sigla='M', nome='Médio')
        destino = reverse('moda:estoque-tecidos')

        resposta = self.client.post(
            reverse('moda:apoio-toggle-ativo', args=['produtos', 'tamanhos', tamanho.pk]),
            {'next': destino},
        )

        self.assertRedirects(resposta, destino)

    def test_ignora_next_de_outro_site(self):
        tamanho = Tamanho.objects.create(filial=self.filial, sigla='M', nome='Médio')

        resposta = self.client.post(
            reverse('moda:apoio-toggle-ativo', args=['produtos', 'tamanhos', tamanho.pk]),
            {'next': 'https://evil.example.com/roubado/'},
        )

        self.assertRedirects(resposta, reverse('moda:item', args=['produtos', 'tamanhos']))

    def test_nao_mexe_em_cadastro_de_outra_filial(self):
        outra_filial = Filial.objects.create(
            empresa=self.empresa, razao_social='Outra', cnpj='63345678000273',
            uf='RN', cidade='Mossoró',
        )
        tamanho = Tamanho.objects.create(filial=outra_filial, sigla='M', nome='Médio', ativo=True)

        resposta = self.client.post(
            reverse('moda:apoio-toggle-ativo', args=['produtos', 'tamanhos', tamanho.pk])
        )

        self.assertEqual(resposta.status_code, 404)
        tamanho.refresh_from_db()
        self.assertTrue(tamanho.ativo)


class ExcluirTests(ApoioBase):

    def test_exclui_cadastro_sem_uso(self):
        tamanho = Tamanho.objects.create(filial=self.filial, sigla='M', nome='Médio')

        self.client.post(
            reverse('moda:apoio-delete', args=['produtos', 'tamanhos', tamanho.pk])
        )

        self.assertFalse(Tamanho.objects.filter(pk=tamanho.pk).exists())

    def test_recusa_excluir_tecido_em_uso_por_produto(self):
        from apps.moda.models import ProdutoModa

        tecido = Tecido.objects.create(filial=self.filial, nome='Malha Dry')
        ProdutoModa.objects.create(
            filial=self.filial, codigo='PM001', nome='Camisa Dry', tecido=tecido,
        )

        resposta = self.client.post(
            reverse('moda:apoio-delete', args=['engenharia', 'materiais', tecido.pk]),
            follow=True,
        )

        self.assertTrue(Tecido.objects.filter(pk=tecido.pk).exists())
        avisos = [str(m) for m in resposta.context['messages']]
        self.assertTrue(any('está em uso' in a for a in avisos), avisos)

    def test_redireciona_para_o_next_quando_seguro(self):
        tamanho = Tamanho.objects.create(filial=self.filial, sigla='M', nome='Médio')
        destino = reverse('moda:estoque-tecidos')

        resposta = self.client.post(
            reverse('moda:apoio-delete', args=['produtos', 'tamanhos', tamanho.pk]),
            {'next': destino},
        )

        self.assertRedirects(resposta, destino)


class AcoesNaTelaDeCadastroTests(ApoioBase):

    def test_lista_mostra_os_botoes_de_acao(self):
        tamanho = Tamanho.objects.create(filial=self.filial, sigla='M', nome='Médio')

        html = self.client.get(
            reverse('moda:item', args=['produtos', 'tamanhos'])
        ).content.decode()

        self.assertIn(
            reverse('moda:apoio-toggle-ativo', args=['produtos', 'tamanhos', tamanho.pk]), html,
        )
        self.assertIn(
            reverse('moda:apoio-delete', args=['produtos', 'tamanhos', tamanho.pk]), html,
        )


class AcoesNaTelaDeEstoqueTecidosTests(ApoioBase):

    def test_lista_mostra_editar_inativar_excluir(self):
        tecido = Tecido.objects.create(filial=self.filial, nome='Malha Dry')

        html = self.client.get(reverse('moda:estoque-tecidos')).content.decode()

        self.assertIn(
            reverse('moda:apoio-update', args=['engenharia', 'materiais', tecido.pk]), html,
        )
        self.assertIn(
            reverse('moda:apoio-toggle-ativo', args=['engenharia', 'materiais', tecido.pk]), html,
        )
        self.assertIn(
            reverse('moda:apoio-delete', args=['engenharia', 'materiais', tecido.pk]), html,
        )

    def test_mostra_adicionar_estoque_so_quando_ligado(self):
        metro = UnidadeMedida.objects.create(
            empresa=self.empresa, sigla='M', descricao='Metro',
            tipo=UnidadeMedida.Tipo.COMPRIMENTO,
        )
        produto = Produto.objects.create(
            filial=self.filial, codigo='TEC001', descricao='Malha Dry 1,60',
            unidade_medida=metro,
        )
        ligado = Tecido.objects.create(
            filial=self.filial, nome='Malha Ligada', produto_estoque=produto,
        )
        Tecido.objects.create(filial=self.filial, nome='Malha Solta')

        html = self.client.get(reverse('moda:estoque-tecidos')).content.decode()

        self.assertIn(f'movimentacoes/nova/?produto={produto.pk}', html)

    def test_inativar_a_partir_da_tela_de_estoque_volta_pra_ela(self):
        tecido = Tecido.objects.create(filial=self.filial, nome='Malha Dry')
        destino = reverse('moda:estoque-tecidos')

        resposta = self.client.post(
            reverse('moda:apoio-toggle-ativo', args=['engenharia', 'materiais', tecido.pk]),
            {'next': destino},
        )

        self.assertRedirects(resposta, destino)
        tecido.refresh_from_db()
        self.assertFalse(tecido.ativo)


class ColunaDeEstoqueNoCadastroDeMateriaisTests(ApoioBase):
    """
    A lista de Tecidos e Malhas (Engenharia › Materiais) ganhou uma coluna
    de saldo -- pra não precisar abrir a tela de Estoque › Tecidos só pra
    ver quanto ainda tem de um rolo.
    """

    def test_mostra_o_saldo_do_tecido_ligado_ao_estoque(self):
        from apps.estoque.models.estoque import Estoque

        metro = UnidadeMedida.objects.create(
            empresa=self.empresa, sigla='M', descricao='Metro',
            tipo=UnidadeMedida.Tipo.COMPRIMENTO,
        )
        produto = Produto.objects.create(
            filial=self.filial, codigo='TEC002', descricao='Malha Dry 1,60',
            unidade_medida=metro,
        )
        Estoque.objects.create(
            produto=produto, filial=self.filial, quantidade_atual=Decimal('150.5'),
        )
        Tecido.objects.create(
            filial=self.filial, nome='Malha Ligada', produto_estoque=produto,
        )

        html = self.client.get(
            reverse('moda:item', args=['engenharia', 'materiais'])
        ).content.decode()

        self.assertIn('150,50', html)

    def test_sem_vinculo_mostra_atalho_pra_lancar_em_vez_de_zero(self):
        """
        Sem ligação com o estoque o saldo é desconhecido, não zero --
        mostrar 0 sugeriria "acabou" quando na verdade ninguém cadastrou
        o vínculo ainda. Em vez de um "—" mudo, a coluna já oferece o
        atalho pra criar o produto e lançar a quantidade.
        """
        tecido = Tecido.objects.create(filial=self.filial, nome='Malha Solta')

        html = self.client.get(
            reverse('moda:item', args=['engenharia', 'materiais'])
        ).content.decode()

        self.assertIn('Malha Solta', html)
        self.assertIn('+ Lançar', html)
        self.assertIn(
            reverse('moda:apoio-novo-produto-estoque', args=['engenharia', 'materiais', tecido.pk]),
            html,
        )


class AtalhoDeCriarProdutoNoFormularioDeTecidoTests(ApoioBase):
    """
    "Ao clicar para editar, não consigo inserir a quantidade de estoque":
    o formulário do tecido nunca grava saldo direto (só a movimentação
    grava, senão viraria um segundo lugar guardando o mesmo número) -- o
    que faltava era um jeito de sair daqui, criar o produto de estoque e
    voltar com o vínculo pronto sem escolher de novo numa lista.
    """

    def test_form_mostra_novo_produto_quando_nao_ha_vinculo(self):
        tecido = Tecido.objects.create(filial=self.filial, nome='Active Air')

        html = self.client.get(
            reverse('moda:apoio-update', args=['engenharia', 'materiais', tecido.pk])
        ).content.decode()

        self.assertIn('+ Novo produto', html)
        self.assertIn(
            reverse('moda:apoio-novo-produto-estoque', args=['engenharia', 'materiais', tecido.pk]),
            html,
        )

    def test_form_mostra_adicionar_estoque_quando_ja_ha_vinculo(self):
        metro = UnidadeMedida.objects.create(
            empresa=self.empresa, sigla='M', descricao='Metro',
            tipo=UnidadeMedida.Tipo.COMPRIMENTO,
        )
        produto = Produto.objects.create(
            filial=self.filial, codigo='TEC003', descricao='Malha Dry',
            unidade_medida=metro,
        )
        tecido = Tecido.objects.create(
            filial=self.filial, nome='Malha Ligada', produto_estoque=produto,
        )

        html = self.client.get(
            reverse('moda:apoio-update', args=['engenharia', 'materiais', tecido.pk])
        ).content.decode()

        self.assertIn('+ Adicionar estoque', html)
        self.assertIn(f'movimentacoes/nova/?produto={produto.pk}', html)
        self.assertNotIn('+ Novo produto', html)

    def test_form_mostra_saldo_atual_quando_ja_ha_vinculo(self):
        from apps.estoque.models.estoque import Estoque

        metro = UnidadeMedida.objects.create(
            empresa=self.empresa, sigla='M', descricao='Metro',
            tipo=UnidadeMedida.Tipo.COMPRIMENTO,
        )
        produto = Produto.objects.create(
            filial=self.filial, codigo='TEC006', descricao='Malha Dry',
            unidade_medida=metro,
        )
        Estoque.objects.create(
            produto=produto, filial=self.filial, quantidade_atual=Decimal('87.5'),
        )
        tecido = Tecido.objects.create(
            filial=self.filial, nome='Malha Ligada', produto_estoque=produto,
        )

        html = self.client.get(
            reverse('moda:apoio-update', args=['engenharia', 'materiais', tecido.pk])
        ).content.decode()

        self.assertIn('Saldo atual', html)
        self.assertIn('87,50', html)

    def test_form_sem_vinculo_nao_mostra_saldo_atual(self):
        tecido = Tecido.objects.create(filial=self.filial, nome='Active Air')

        html = self.client.get(
            reverse('moda:apoio-update', args=['engenharia', 'materiais', tecido.pk])
        ).content.decode()

        self.assertNotIn('Saldo atual', html)


class NovoProdutoEstoqueViewTests(ApoioBase):
    """
    O cadastro completo de Produto (CFOP, preço de venda, NCM...) é feito
    pra quem vende ao cliente final -- matéria-prima nunca sai numa nota
    de venda. Este é o cadastro enxuto que essa tela usa em vez dele: só
    nome, código, unidade e quantidade inicial, e o produto que nasce daqui
    já sai marcado como rascunho comercial, pra nunca aparecer pronto pra
    venda no PDV por engano.
    """

    def setUp(self):
        super().setUp()
        self.unidade = UnidadeMedida.objects.create(
            empresa=self.empresa, sigla='M', descricao='Metro',
            tipo=UnidadeMedida.Tipo.COMPRIMENTO,
        )
        self.tecido = Tecido.objects.create(filial=self.filial, nome='Active Air')
        self.url = reverse(
            'moda:apoio-novo-produto-estoque', args=['engenharia', 'materiais', self.tecido.pk],
        )

    def test_formulario_vem_com_nome_do_tecido_sugerido(self):
        resposta = self.client.get(self.url)

        self.assertEqual(resposta.context['form'].initial.get('nome'), 'Active Air')
        campos = set(resposta.context['form'].fields)
        self.assertEqual(campos, {
            'nome', 'codigo', 'unidade_medida', 'quantidade_inicial',
            'estoque_minimo', 'estoque_maximo', 'ponto_reposicao',
            'estoque_seguranca', 'lead_time_reposicao_dias',
            'localizacao_estoque', 'metodo_saida',
        })
        # Nada de CFOP, preço de venda, NCM ou categoria fiscal -- os campos
        # continuam só identificação + saldo/reposição.
        self.assertNotIn('ncm', campos)
        self.assertNotIn('preco_venda', campos)
        self.assertNotIn('cfop_venda_interna', campos)

    def test_form_mostra_atalho_pra_criar_unidade_sem_sair_da_tela(self):
        html = self.client.get(self.url).content.decode()

        self.assertIn('+ Nova unidade', html)
        self.assertIn(reverse('produtos:unidade-inline-create'), html)

    def test_cria_produto_enxuto_e_vincula_ao_tecido(self):
        resposta = self.client.post(self.url, {
            'nome': 'Active Air', 'codigo': 'ACT-01',
            'unidade_medida': self.unidade.pk, 'quantidade_inicial': '150.5',
        })

        self.tecido.refresh_from_db()
        produto = self.tecido.produto_estoque
        self.assertRedirects(
            resposta,
            reverse('moda:apoio-update', args=['engenharia', 'materiais', self.tecido.pk]),
        )
        self.assertIsNotNone(produto)
        self.assertEqual(produto.descricao, 'Active Air')
        self.assertEqual(produto.codigo, 'ACT-01')
        self.assertTrue(produto.rascunho_comercial)
        self.assertFalse(produto.permite_venda_sem_estoque)

        from apps.estoque.models.estoque import Estoque
        estoque = Estoque.objects.get(produto=produto, filial=self.filial)
        self.assertEqual(estoque.quantidade_atual, Decimal('150.500'))

    def test_grava_minimo_maximo_reposicao_e_metodo_de_saida(self):
        from apps.produtos.models import Produto

        self.client.post(self.url, {
            'nome': 'Active Air', 'codigo': '', 'unidade_medida': self.unidade.pk,
            'quantidade_inicial': '', 'estoque_minimo': '10', 'estoque_maximo': '200',
            'ponto_reposicao': '30', 'estoque_seguranca': '5',
            'lead_time_reposicao_dias': '7', 'localizacao_estoque': 'A1-03',
            'metodo_saida': Produto.MetodoSaida.FIFO,
        })

        self.tecido.refresh_from_db()
        produto = self.tecido.produto_estoque
        self.assertEqual(produto.estoque_minimo, Decimal('10.000'))
        self.assertEqual(produto.estoque_maximo, Decimal('200.000'))
        self.assertEqual(produto.ponto_reposicao, Decimal('30.000'))
        self.assertEqual(produto.estoque_seguranca, Decimal('5.000'))
        self.assertEqual(produto.lead_time_reposicao_dias, 7)
        self.assertEqual(produto.localizacao_estoque, 'A1-03')
        self.assertEqual(produto.metodo_saida, Produto.MetodoSaida.FIFO)

    def test_sem_preencher_nada_alem_do_obrigatorio_usa_padroes_neutros(self):
        from apps.produtos.models import Produto

        self.client.post(self.url, {
            'nome': 'Active Air', 'codigo': '', 'unidade_medida': self.unidade.pk,
            'quantidade_inicial': '',
        })

        self.tecido.refresh_from_db()
        produto = self.tecido.produto_estoque
        self.assertEqual(produto.estoque_minimo, Decimal('0'))
        self.assertEqual(produto.metodo_saida, Produto.MetodoSaida.FEFO)

    def test_sem_quantidade_inicial_nasce_com_saldo_zero(self):
        self.client.post(self.url, {
            'nome': 'Active Air', 'codigo': '',
            'unidade_medida': self.unidade.pk, 'quantidade_inicial': '',
        })

        self.tecido.refresh_from_db()
        produto = self.tecido.produto_estoque
        self.assertIsNotNone(produto)

        from apps.estoque.models.estoque import Estoque
        self.assertFalse(Estoque.objects.filter(produto=produto, filial=self.filial).exists())

    def test_sem_unidade_recusa_e_nao_cria_produto(self):
        from apps.produtos.models import Produto

        resposta = self.client.post(self.url, {
            'nome': 'Active Air', 'codigo': '', 'unidade_medida': '', 'quantidade_inicial': '',
        })

        self.assertEqual(resposta.status_code, 200)
        self.assertFalse(Produto.objects.filter(descricao='Active Air').exists())


class ListaDeMateriaisCentralizadaTests(ApoioBase):
    """
    Composição, Gramatura, Fornecedor e Estoque ganharam alinhamento
    centralizado -- só a primeira coluna (Nome, que é o link de edição)
    continua alinhada à esquerda.
    """

    def test_cabecalho_das_colunas_alem_da_primeira_fica_centralizado(self):
        Tecido.objects.create(filial=self.filial, nome='Active Air')

        html = self.client.get(
            reverse('moda:item', args=['engenharia', 'materiais'])
        ).content.decode()

        cabecalho = html[html.index('<thead>'):html.index('</thead>')]
        corpo = html[html.index('<tbody>'):html.index('</tbody>')]

        # 5 colunas do cadastro (Nome, Composição, Gramatura, Fornecedor,
        # Estoque (m)); só a primeira (Nome, o link de edição) fica à
        # esquerda -- as outras 4 centralizadas, no cabeçalho e na linha.
        self.assertEqual(cabecalho.count('text-center'), 4)
        self.assertEqual(corpo.count('text-center'), 4)
        self.assertIn('text-left', cabecalho)


class EdicaoRapidaDeEstoqueNaListaTests(ApoioBase):
    """
    "Quero conseguir digitar a quantidade de estoque": duplo clique na
    célula de Estoque abre um campo de texto que grava via o mesmo
    endpoint de ajuste manual que a tela de Estoque usa -- o saldo nunca
    é escrito cru, sempre passa por uma movimentação de verdade.
    """

    def test_tecido_ligado_ganha_celula_editavel_apontando_pro_produto(self):
        metro = UnidadeMedida.objects.create(
            empresa=self.empresa, sigla='M', descricao='Metro',
            tipo=UnidadeMedida.Tipo.COMPRIMENTO,
        )
        produto = Produto.objects.create(
            filial=self.filial, codigo='TEC006', descricao='Malha Dry',
            unidade_medida=metro,
        )
        Tecido.objects.create(filial=self.filial, nome='Malha Ligada', produto_estoque=produto)

        html = self.client.get(
            reverse('moda:item', args=['engenharia', 'materiais'])
        ).content.decode()

        self.assertIn('data-field="estoque_atual"', html)
        self.assertIn(
            reverse('estoque:estoque-inline-edit', args=[produto.pk]), html,
        )

    def test_tecido_sem_vinculo_nao_ganha_celula_editavel(self):
        Tecido.objects.create(filial=self.filial, nome='Malha Solta')

        html = self.client.get(
            reverse('moda:item', args=['engenharia', 'materiais'])
        ).content.decode()

        self.assertNotIn('data-field="estoque_atual"', html)

    def test_comentarios_do_template_nao_vazam_pra_tela(self):
        """
        `{# ... #}` de mais de uma linha não é comentário pro Django --
        ele renderiza o texto cru na tela em vez de sumir. Guarda contra
        essa armadilha especificamente nas duas colunas de estoque
        (ligada e sem vínculo), onde ela já mordeu uma vez.
        """
        metro = UnidadeMedida.objects.create(
            empresa=self.empresa, sigla='M', descricao='Metro',
            tipo=UnidadeMedida.Tipo.COMPRIMENTO,
        )
        produto = Produto.objects.create(
            filial=self.filial, codigo='TEC007', descricao='Malha Dry',
            unidade_medida=metro,
        )
        Tecido.objects.create(filial=self.filial, nome='Malha Ligada', produto_estoque=produto)
        Tecido.objects.create(filial=self.filial, nome='Malha Solta')

        html = self.client.get(
            reverse('moda:item', args=['engenharia', 'materiais'])
        ).content.decode()

        self.assertNotIn('{#', html)
        self.assertNotIn('#}', html)
