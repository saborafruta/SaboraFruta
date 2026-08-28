from decimal import Decimal

from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase

from apps.core.models import Empresa, Filial, PerfilAcesso, PoliticaReplicacaoFilial, Usuario
from apps.estoque.models import Estoque, MovimentacaoEstoque
from apps.produtos.models import Produto, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial
from apps.produtos.views.produto import ProdutoListView, ProdutoToggleAtivoView, _produto_queryset_filtrado


class ProdutoToggleEstoqueTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(
            razao_social='Empresa Produto LTDA',
            nome_fantasia='Empresa Produto',
            cnpj='52345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        self.filial = Filial.objects.create(
            empresa=self.empresa,
            razao_social='Filial Produto',
            nome_fantasia='Filial Produto',
            cnpj='52345678000192',
            uf='RN',
        )
        self.outra_filial = Filial.objects.create(
            empresa=self.empresa,
            razao_social='Outra Filial Produto',
            nome_fantasia='Outra Filial',
            cnpj='52345678000193',
            uf='RN',
        )
        perfil = PerfilAcesso.objects.create(
            empresa=self.empresa,
            nome='Administrador',
            is_admin=True,
        )
        self.usuario = Usuario.objects.create_user(
            email='produto-toggle@inoovated.com',
            nome='Usuario Produto',
            password='teste1234',
            empresa=self.empresa,
            filial=self.filial,
            perfil=perfil,
        )
        self.unidade = UnidadeMedida.objects.create(
            empresa=self.empresa,
            sigla='UN',
            descricao='Unidade',
        )
        UnidadeMedidaFilial.objects.create(unidade=self.unidade, filial=self.filial)
        UnidadeMedidaFilial.objects.create(unidade=self.unidade, filial=self.outra_filial)
        self.factory = RequestFactory()

    def criar_produto(self, ativo=True, ativo_filial=None):
        produto = Produto.objects.create(
            filial=self.filial,
            unidade_medida=self.unidade,
            descricao='Produto com estoque',
            ncm='20089900',
            preco_venda=Decimal('10.00'),
            preco_custo=Decimal('4.00'),
            ativo=ativo,
        )
        if ativo_filial is None:
            ativo_filial = ativo
        ProdutoFilial.objects.create(produto=produto, filial=self.filial, ativo=ativo_filial)
        Estoque.objects.create(
            produto=produto,
            filial=self.filial,
            quantidade_atual=Decimal('5.000'),
            quantidade_disponivel=Decimal('5.000'),
        )
        return produto

    def request(self, data):
        request = self.factory.post('/produtos/toggle/', data)
        request.user = self.usuario
        request.filial_ativa = self.filial
        request.session = self.client.session
        request._messages = FallbackStorage(request)
        return request

    def habilitar_replicacao_produtos(self):
        for filial in (self.filial, self.outra_filial):
            filial.participa_replicacao = True
            filial.save(update_fields=['participa_replicacao', 'updated_at'])
            PoliticaReplicacaoFilial.objects.update_or_create(
                filial=filial,
                defaults={
                    'ativo': True,
                    'replicar_produtos_basicos': True,
                },
            )

    def list_request(self, params):
        request = self.factory.get('/produtos/', params)
        request.user = self.usuario
        request.filial_ativa = self.filial
        request.session = {}
        return request

    def test_ver_todos_mantem_filtros_ordenacao_e_escopo(self):
        from unittest.mock import patch
        for index in range(53):
            produto = self.criar_produto()
            produto.descricao = f'Camisa {index:03d}'
            produto.save(update_fields=['descricao'])
        inativo = self.criar_produto(ativo_filial=False)
        zerado = self.criar_produto()
        Estoque.objects.filter(produto=zerado).update(quantidade_atual=0, quantidade_disponivel=0)
        estrangeiro = Produto.objects.create(filial=self.outra_filial, unidade_medida=self.unidade, descricao='Camisa externa')
        ProdutoFilial.objects.create(produto=estrangeiro, filial=self.outra_filial)
        params = {'q': 'Camisa', 'status': 'ativo', 'com_estoque': '1', 'ordem': 'za'}
        with patch('apps.produtos.views.produto.render') as render_mock:
            ProdutoListView.as_view()(self.list_request(params))
            normal = render_mock.call_args.args[2]
            self.assertEqual(len(normal['produtos']), 50)
            self.assertFalse(normal['ver_todos'])
            ProdutoListView.as_view()(self.list_request({**params, 'ver': 'todos', 'page': '2'}))
            all_context = render_mock.call_args.args[2]
        self.assertEqual(len(all_context['produtos']), 53)
        self.assertTrue(all_context['ver_todos'])
        self.assertEqual(all_context['page_obj'].number, 1)
        self.assertEqual(all_context['produtos'][0].descricao, 'Camisa 052')
        self.assertEqual(all_context['produtos'][-1].descricao, 'Camisa 000')
        self.assertFalse({inativo.pk, zerado.pk, estrangeiro.pk} & {p.pk for p in all_context['produtos']})

    def test_ver_todos_vazio_renderiza_sem_erro(self):
        response = ProdutoListView.as_view()(self.list_request({'ver': 'todos', 'q': 'inexistente'}))
        self.assertContains(response, 'Todos os 0 produtos')
        self.assertContains(response, 'Nenhum produto encontrado')
        self.assertNotContains(response, 'id="produto-ver-todos"')

    def test_ver_todos_so_aparece_quando_existe_outra_pagina(self):
        for _ in range(51):
            self.criar_produto()
        response = ProdutoListView.as_view()(self.list_request({}))
        self.assertContains(response, 'id="produto-ver-todos"')
        self.assertContains(response, 'produtos-lista.js')
        response = ProdutoListView.as_view()(self.list_request({'ver': 'todos'}))
        self.assertContains(response, 'Todos os 51 produtos')
        self.assertContains(response, 'name="ver" value="todos"')
        self.assertNotContains(response, 'id="produto-ver-todos"')

    def test_inativar_produto_zera_estoque_quando_solicitado(self):
        produto = self.criar_produto()

        response = ProdutoToggleAtivoView.as_view()(self.request({'zerar_estoque': '1'}), pk=produto.pk)

        produto.refresh_from_db()
        vinculo = ProdutoFilial.objects.get(produto=produto, filial=self.filial)
        estoque = Estoque.objects.get(produto=produto, filial=self.filial)
        movimento = MovimentacaoEstoque.objects.get(produto=produto, filial=self.filial)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(produto.ativo)
        self.assertFalse(vinculo.ativo)
        self.assertEqual(estoque.quantidade_atual, Decimal('0.000'))
        self.assertEqual(movimento.tipo_operacao, MovimentacaoEstoque.TipoOperacao.AJUSTE_MENOS)
        self.assertEqual(movimento.quantidade, Decimal('5.000'))

    def test_filtro_com_estoque_exclui_saldos_zerados_e_negativos(self):
        produto_positivo = self.criar_produto()
        produto_zerado = self.criar_produto()
        produto_negativo = self.criar_produto()
        Estoque.objects.filter(produto=produto_zerado).update(
            quantidade_atual=Decimal('0'),
            quantidade_disponivel=Decimal('0'),
        )
        Estoque.objects.filter(produto=produto_negativo).update(
            quantidade_atual=Decimal('-2'),
            quantidade_disponivel=Decimal('-2'),
        )
        request = self.factory.get('/produtos/', {'com_estoque': '1', 'status': 'todos'})
        request.user = self.usuario
        request.filial_ativa = self.filial

        produtos = list(_produto_queryset_filtrado(request, incluir_inativos_por_padrao=True))

        self.assertEqual([produto.pk for produto in produtos], [produto_positivo.pk])

    def test_inativar_produto_sem_confirmar_mantem_estoque(self):
        produto = self.criar_produto()

        ProdutoToggleAtivoView.as_view()(self.request({'zerar_estoque': '0'}), pk=produto.pk)

        produto.refresh_from_db()
        vinculo = ProdutoFilial.objects.get(produto=produto, filial=self.filial)
        estoque = Estoque.objects.get(produto=produto, filial=self.filial)
        self.assertTrue(produto.ativo)
        self.assertFalse(vinculo.ativo)
        self.assertEqual(estoque.quantidade_atual, Decimal('5.000'))
        self.assertFalse(MovimentacaoEstoque.objects.filter(produto=produto).exists())

    def test_ativar_produto_nao_zera_estoque_mesmo_com_flag(self):
        produto = self.criar_produto(ativo=False)

        ProdutoToggleAtivoView.as_view()(self.request({'zerar_estoque': '1'}), pk=produto.pk)

        produto.refresh_from_db()
        vinculo = ProdutoFilial.objects.get(produto=produto, filial=self.filial)
        estoque = Estoque.objects.get(produto=produto, filial=self.filial)
        self.assertTrue(produto.ativo)
        self.assertTrue(vinculo.ativo)
        self.assertEqual(estoque.quantidade_atual, Decimal('5.000'))
        self.assertFalse(MovimentacaoEstoque.objects.filter(produto=produto).exists())

    def test_inativar_produto_nao_altera_outra_filial_por_padrao(self):
        produto = self.criar_produto()
        ProdutoFilial.objects.create(produto=produto, filial=self.outra_filial, ativo=True)

        ProdutoToggleAtivoView.as_view()(self.request({'zerar_estoque': '0'}), pk=produto.pk)

        self.assertFalse(ProdutoFilial.objects.get(produto=produto, filial=self.filial).ativo)
        self.assertTrue(ProdutoFilial.objects.get(produto=produto, filial=self.outra_filial).ativo)

    def test_inativar_produto_em_filial_selecionada(self):
        self.habilitar_replicacao_produtos()
        produto = self.criar_produto()
        ProdutoFilial.objects.create(produto=produto, filial=self.outra_filial, ativo=True)

        ProdutoToggleAtivoView.as_view()(
            self.request({
                'zerar_estoque': '0',
                'filiais_inativar': [str(self.outra_filial.pk)],
            }),
            pk=produto.pk,
        )

        self.assertFalse(ProdutoFilial.objects.get(produto=produto, filial=self.filial).ativo)
        self.assertFalse(ProdutoFilial.objects.get(produto=produto, filial=self.outra_filial).ativo)

    def test_inativar_produto_em_outra_filial_exige_replicacao_produtos(self):
        produto = self.criar_produto()
        ProdutoFilial.objects.create(produto=produto, filial=self.outra_filial, ativo=True)

        ProdutoToggleAtivoView.as_view()(
            self.request({
                'zerar_estoque': '0',
                'filiais_inativar': [str(self.outra_filial.pk)],
            }),
            pk=produto.pk,
        )

        self.assertFalse(ProdutoFilial.objects.get(produto=produto, filial=self.filial).ativo)
        self.assertTrue(ProdutoFilial.objects.get(produto=produto, filial=self.outra_filial).ativo)

    def test_ativar_produto_em_filial_selecionada(self):
        self.habilitar_replicacao_produtos()
        produto = self.criar_produto(ativo=True, ativo_filial=False)
        ProdutoFilial.objects.create(produto=produto, filial=self.outra_filial, ativo=False)

        ProdutoToggleAtivoView.as_view()(
            self.request({
                'zerar_estoque': '0',
                'filiais_ativar': [str(self.outra_filial.pk)],
            }),
            pk=produto.pk,
        )

        self.assertTrue(ProdutoFilial.objects.get(produto=produto, filial=self.filial).ativo)
        self.assertTrue(ProdutoFilial.objects.get(produto=produto, filial=self.outra_filial).ativo)

    def test_ativar_produto_em_outra_filial_exige_replicacao_produtos(self):
        produto = self.criar_produto(ativo=True, ativo_filial=False)
        ProdutoFilial.objects.create(produto=produto, filial=self.outra_filial, ativo=False)

        ProdutoToggleAtivoView.as_view()(
            self.request({
                'zerar_estoque': '0',
                'filiais_ativar': [str(self.outra_filial.pk)],
            }),
            pk=produto.pk,
        )

        self.assertTrue(ProdutoFilial.objects.get(produto=produto, filial=self.filial).ativo)
        self.assertFalse(ProdutoFilial.objects.get(produto=produto, filial=self.outra_filial).ativo)

    def test_listagem_todos_mantem_produto_inativo_da_filial(self):
        produto = self.criar_produto()
        ProdutoToggleAtivoView.as_view()(self.request({'zerar_estoque': '0'}), pk=produto.pk)

        request = self.factory.get('/produtos/', {'status': 'todos'})
        request.user = self.usuario
        request.filial_ativa = self.filial

        produtos = list(_produto_queryset_filtrado(request, incluir_inativos_por_padrao=True))

        produto_listado = next(item for item in produtos if item.pk == produto.pk)
        self.assertFalse(produto_listado.ativo_filial)

    def test_listagem_usa_largura_total_sem_remover_colunas(self):
        self.criar_produto()
        request = self.factory.get('/produtos/')
        request.user = self.usuario
        request.filial_ativa = self.filial
        request.session = {}

        response = ProdutoListView.as_view()(request)

        self.assertContains(response, 'produto-list-page w-full max-w-none mx-auto')
        for coluna in ('Cod. de Barras', 'Categoria', 'Sub categoria', 'Estoque', 'Custo', 'Preco venda', 'Markup', 'Margem', 'Acoes'):
            with self.subTest(coluna=coluna):
                self.assertContains(response, coluna)
