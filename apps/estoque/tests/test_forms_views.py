import json
from decimal import Decimal

from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.models import Fornecedor, FornecedorFilial
from apps.compras.models import EntradaNF, ItemEntradaNF, PedidoCompra
from apps.compras.services.entrada_custo_service import EntradaCustoService
from apps.core.models import Empresa, Filial, PerfilAcesso, Permissao, RegistroAuditoria, Usuario
from apps.estoque.forms import MovimentacaoManualForm, TransferenciaForm
from apps.estoque.models import Estoque, Inventario, ItemInventario, LoteProduto, MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.estoque.views import (
    EntradaCustoEstoqueListView,
    AjusteEstoqueView,
    EstoqueListView,
    InventarioCancelView,
    InventarioCreateView,
    InventarioDetailView,
    InventarioDivergenciasView,
    InventarioListView,
    LoteBaixaValidadeView,
    LoteCreateView,
    LoteListView,
    LoteUpdateView,
    MovimentacaoManualView,
    MovimentacaoListView,
    RelatorioEstoqueView,
    ReposicaoListView,
    TransferenciaView,
)
from apps.estoque.views.inventario import _criar_itens_inventario
from apps.produtos.models import Produto, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial


class EstoqueFormsViewsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Empresa Teste LTDA',
            nome_fantasia='Empresa Teste',
            cnpj='22345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa,
            razao_social='Filial Teste',
            nome_fantasia='Matriz',
            cnpj='22345678000192',
            uf='RN',
        )
        cls.filial_destino = Filial.objects.create(
            empresa=cls.empresa,
            razao_social='Filial Destino',
            nome_fantasia='Destino',
            cnpj='22345678000193',
            uf='RN',
        )
        cls.perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa,
            nome='Estoque',
        )
        cls.usuario = Usuario.objects.create_user(
            email='forms-views@inoovated.com',
            nome='Usuario Estoque',
            password='teste1234',
            empresa=cls.empresa,
            filial=cls.filial,
            perfil=cls.perfil,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa,
            sigla='UN',
            descricao='Unidade',
            tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial_destino)
        cls.fornecedor = Fornecedor.objects.create(
            filial=cls.filial,
            tipo_pessoa='J',
            razao_social='Fornecedor Teste',
            cpf_cnpj='22345678000194',
            uf='RN',
        )
        FornecedorFilial.objects.create(fornecedor=cls.fornecedor, filial=cls.filial)

    def setUp(self):
        self.factory = RequestFactory()
        self.client.force_login(self.usuario)
        session = self.client.session
        session['filial_ativa_id'] = self.filial.pk
        session.save()

    def conceder(self, modulo='estoque', **permissoes):
        defaults = {
            'pode_ver': False,
            'pode_criar': False,
            'pode_editar': False,
            'pode_excluir': False,
            'pode_cancelar': False,
            'pode_aprovar': False,
            'pode_exportar': False,
        }
        defaults.update(permissoes)
        Permissao.objects.update_or_create(
            perfil=self.perfil,
            modulo=modulo,
            defaults=defaults,
        )

    def criar_produto(self, descricao='Produto Teste', controla_lote=False, fornecedor=None):
        produto = Produto.objects.create(
            filial=self.filial,
            unidade_medida=self.unidade,
            fornecedor=fornecedor,
            descricao=descricao,
            ncm='20089900',
            controla_lote=controla_lote,
            controla_validade=controla_lote,
            permite_venda_sem_estoque=False,
        )
        ProdutoFilial.objects.create(produto=produto, filial=self.filial)
        return produto

    def criar_lote(self, produto):
        return LoteProduto.objects.create(
            produto=produto,
            filial=self.filial,
            numero_lote='LT-FORM',
            quantidade_inicial=Decimal('0'),
            quantidade_atual=Decimal('0'),
        )

    def criar_lote_com_entrada(self, produto, numero, quantidade):
        lote = LoteProduto.objects.create(
            produto=produto,
            filial=self.filial,
            numero_lote=numero,
            quantidade_inicial=Decimal('0'),
            quantidade_atual=Decimal('0'),
            custo_unitario=Decimal('2.0000'),
        )
        MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk,
            filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal(quantidade),
            usuario_id=self.usuario.pk,
            lote_id=lote.pk,
            valor_unitario=Decimal('2.00'),
        )
        lote.refresh_from_db()
        return lote

    def test_movimentacao_manual_exige_lote_para_produto_controlado(self):
        self.conceder(pode_ver=True, pode_editar=True)
        produto = self.criar_produto(controla_lote=True)

        form = MovimentacaoManualForm(
            data={
                'produto': produto.pk,
                'tipo_operacao': MovimentacaoEstoque.TipoOperacao.ENTRADA,
                'quantidade': '1',
                'valor_unitario': '2',
            },
            filial=self.filial,
        )

        self.assertFalse(form.is_valid())
        self.assertIn('Informe o lote', str(form.errors))

    def test_transferencia_rejeita_lote_de_outro_produto_no_form(self):
        self.conceder(pode_ver=True, pode_editar=True)
        produto = self.criar_produto(descricao='Produto A', controla_lote=True)
        outro = self.criar_produto(descricao='Produto B', controla_lote=True)
        lote = self.criar_lote(outro)

        form = TransferenciaForm(
            data={
                'produto': produto.pk,
                'lote': lote.pk,
                'filial_destino': self.filial_destino.pk,
                'quantidade': '1',
            },
            filial=self.filial,
            empresa=self.empresa,
        )

        self.assertFalse(form.is_valid())
        self.assertIn('nao pertence ao produto', str(form.errors))

    def test_catalogo_visual_transferencia_retorna_produtos_e_saldo(self):
        self.conceder(pode_ver=True)
        produto = self.criar_produto(descricao='Polpa para transferencia')
        MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk,
            filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal('12.500'),
            usuario_id=self.usuario.pk,
            valor_unitario=Decimal('2.00'),
            documento_tipo=MovimentacaoEstoque.DocumentoTipo.OUTRAS,
        )

        response = self.client.get(
            reverse('estoque:produto-estoque-search-json'),
            {'scope': 'empresa', 'browse': '1'},
        )

        self.assertEqual(response.status_code, 200)
        resultado = next(
            item for item in response.json()['results'] if item['id'] == produto.pk
        )
        self.assertEqual(resultado['label'], 'Polpa para transferencia')
        self.assertEqual(resultado['estoque'], 12.5)

    def test_exportacao_estoque_exige_permissao_exportar(self):
        self.conceder(pode_ver=True)

        response = self.client.get(reverse('estoque:estoque-list'), {'export': 'csv'})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('estoque:estoque-list'))

    def test_exportacao_estoque_com_permissao_retorna_csv(self):
        self.conceder(pode_ver=True, pode_exportar=True)

        response = self.client.get(reverse('estoque:estoque-list'), {'export': 'csv'})

        self.assertEqual(response.status_code, 200)
        self.assertIn('text/csv', response['Content-Type'])

    def test_exportacao_estoque_com_permissao_retorna_pdf(self):
        self.conceder(pode_ver=True, pode_exportar=True)
        produto = self.criar_produto(descricao='Produto PDF')
        MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk,
            filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal('1'),
            usuario_id=self.usuario.pk,
            valor_unitario=Decimal('2.00'),
            documento_tipo=MovimentacaoEstoque.DocumentoTipo.OUTRAS,
        )

        response = self.client.get(reverse('estoque:estoque-list'), {'export': 'pdf'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')

    def test_exportacao_reposicao_com_permissao_retorna_pdf(self):
        self.conceder(pode_ver=True, pode_exportar=True)
        produto = self.criar_produto(descricao='Produto Reposicao PDF', fornecedor=self.fornecedor)
        produto.estoque_minimo = Decimal('5')
        produto.estoque_maximo = Decimal('10')
        produto.preco_custo = Decimal('3.50')
        produto.save(update_fields=['estoque_minimo', 'estoque_maximo', 'preco_custo', 'updated_at'])
        MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk,
            filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal('2'),
            usuario_id=self.usuario.pk,
            valor_unitario=Decimal('3.50'),
        )

        response = self.client.get(reverse('estoque:reposicao-list'), {'export': 'pdf'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn('reposicao_estoque.pdf', response['Content-Disposition'])

    def test_exportacao_lotes_com_permissao_retorna_pdf(self):
        self.conceder(pode_ver=True, pode_exportar=True)
        produto = self.criar_produto(descricao='Produto Lote PDF', controla_lote=True)
        self.criar_lote_com_entrada(produto, 'LT-PDF', '3')

        response = self.client.get(reverse('estoque:lote-list'), {'export': 'pdf'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn('lotes_estoque.pdf', response['Content-Disposition'])

    def test_lista_estoque_exibe_precos_e_filtros(self):
        self.conceder(pode_ver=True, pode_exportar=True)
        produto = self.criar_produto(descricao='Produto Preco Estoque', fornecedor=self.fornecedor)
        produto.preco_venda = Decimal('12.50')
        produto.preco_custo = Decimal('4.00')
        produto.foto_url = 'https://example.com/produto.png'
        produto.save(update_fields=['preco_venda', 'preco_custo', 'foto_url', 'updated_at'])
        MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk,
            filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal('2'),
            usuario_id=self.usuario.pk,
            documento_tipo=MovimentacaoEstoque.DocumentoTipo.OUTRAS,
        )

        request = self.factory.get(reverse('estoque:estoque-list'), {'q': 'Preco Estoque'})
        request.user = self.usuario
        request.filial_ativa = self.filial
        request.session = {'filial_ativa_id': self.filial.pk}

        response = EstoqueListView.as_view()(request)
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn('Categorias', content)
        self.assertIn('Fornecedores', content)
        self.assertIn('Exportar Excel', content)
        self.assertIn('Exportar PDF', content)
        self.assertIn('Valor total em estoque', content)
        self.assertIn('Preco de venda', content)
        self.assertIn('Preco de custo', content)
        self.assertIn('Custos de entrada', content)
        self.assertIn('Preco venda', content)
        self.assertIn('Custo unit.', content)
        self.assertIn('Custo total', content)
        self.assertIn('Extrato', content)
        self.assertIn('Extrato (Ficha Kardex)', content)
        self.assertIn('Movimentacoes do produto', content)
        self.assertIn('Voltar ao Kardex', content)
        self.assertIn('Data/hora', content)
        self.assertIn('estoque-kardex-movement-date', content)
        self.assertIn('Quantidade adicionada', content)
        self.assertIn('Quantidade retirada', content)
        self.assertIn('Saldo apos', content)
        self.assertIn('Custo medio', content)
        self.assertIn('Giro diario', content)
        self.assertIn('Giro/mês', content)
        self.assertIn('Cobertura', content)
        self.assertIn('estoque-kardex-card-alert', content)
        self.assertIn('is-critical', content)
        self.assertIn("card('Minimo', data.estoque.minimo, data.estoque.abaixo_minimo ? 'is-critical' : '', estoqueAlert)", content)
        self.assertIn("card('Disponivel', data.estoque.disponivel)", content)
        self.assertIn('Historico de preco e custo', content)
        self.assertIn('estoque-kardex-photo', content)
        self.assertIn('estoque-kardex-detail-body', content)
        self.assertIn('data-estoque-kardex-url', content)
        self.assertIn('data-kardex-more-url', content)
        self.assertIn('estoque-thumb', content)
        self.assertIn('https://example.com/produto.png', content)
        self.assertNotIn('>Reservado<', content)
        self.assertNotIn('>Disponivel<', content)
        self.assertNotIn('>Status<', content)
        self.assertNotIn('>Acoes<', content)
        self.assertNotIn('>Prontidao<', content)
        self.assertIn('R$ 12,50', content)
        self.assertIn('R$ 4,00', content)
        self.assertIn('R$ 8,00', content)

    def test_extrato_kardex_produto_retorna_resumo_operacional(self):
        self.conceder(pode_ver=True)
        produto = self.criar_produto(descricao='Produto Kardex', controla_lote=True, fornecedor=self.fornecedor)
        produto.preco_venda = Decimal('15.00')
        produto.preco_custo = Decimal('3.50')
        produto.estoque_minimo = Decimal('2')
        produto.save(update_fields=['preco_venda', 'preco_custo', 'estoque_minimo', 'updated_at'])
        self.criar_lote_com_entrada(produto, 'KDX-01', '5')

        response = self.client.get(reverse('estoque:estoque-kardex-produto', args=[produto.pk]))
        payload = json.loads(response.content.decode())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload['produto']['descricao'], 'Produto Kardex')
        self.assertEqual(payload['estoque']['atual'], '5')
        self.assertEqual(payload['estoque']['custo_unitario'], 'R$ 2,00')
        self.assertEqual(payload['estoque']['valor_custo_total'], 'R$ 10,00')
        self.assertEqual(payload['estoque']['valor_venda_total'], 'R$ 75,00')
        self.assertFalse(payload['estoque']['abaixo_minimo'])
        self.assertIn('giro', payload)
        self.assertEqual(payload['giro']['cobertura_label'], 'Sem consumo recente')
        self.assertEqual(payload['giro']['giro_mensal_label'], '0/mês')
        self.assertIn('foto_url', payload['produto'])
        self.assertEqual(payload['movimentacoes'][0]['tipo'], 'Entrada')
        self.assertEqual(payload['movimentacoes'][0]['saldo_apos'], '5')
        self.assertIn('custo_medio_posterior', payload['movimentacoes'][0])
        self.assertIn('historico_precos', payload)
        self.assertEqual(payload['lotes'][0]['numero'], 'KDX-01')
        self.assertIn('/estoque/movimentacoes/', payload['links']['movimentacoes'])

    def test_extrato_kardex_calcula_giro_mensal_e_cobertura_arredondada(self):
        self.conceder(pode_ver=True)
        produto = self.criar_produto(descricao='Produto Giro Kardex')
        MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk,
            filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal('22'),
            usuario_id=self.usuario.pk,
            valor_unitario=Decimal('2.00'),
        )
        MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk,
            filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.AJUSTE_MENOS,
            quantidade=Decimal('12'),
            usuario_id=self.usuario.pk,
        )

        response = self.client.get(reverse('estoque:estoque-kardex-produto', args=[produto.pk]))
        payload = json.loads(response.content.decode())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload['estoque']['disponivel'], '10')
        self.assertEqual(payload['giro']['giro_label'], '0,4/dia')
        self.assertEqual(payload['giro']['giro_mensal_label'], '12/mês')
        self.assertEqual(payload['giro']['cobertura_label'], '25 dias')
        self.assertEqual(payload['giro']['cobertura_dias'], '25')

    def test_atalhos_estoque_respeitam_permissoes_granulares(self):
        self.conceder(pode_ver=True, pode_criar=True, pode_editar=False, pode_aprovar=False)

        request = self.factory.get(reverse('estoque:estoque-list'))
        request.user = self.usuario
        request.filial_ativa = self.filial
        request.session = {'filial_ativa_id': self.filial.pk}
        response = EstoqueListView.as_view()(request)
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn('Movimentar', content)
        self.assertNotIn('Ajuste manual', content)
        self.assertNotIn('Transferir', content)

        self.conceder(pode_ver=True, pode_criar=False, pode_editar=True, pode_aprovar=True)
        response = EstoqueListView.as_view()(request)
        content = response.content.decode()

        self.assertNotIn('Movimentar', content)
        self.assertIn('Ajuste manual', content)
        self.assertIn('Transferir', content)

    def test_views_criticas_estoque_exigem_acao_correta(self):
        self.conceder(pode_ver=True, pode_editar=True)

        self.usuario.perfil = PerfilAcesso.objects.get(pk=self.perfil.pk)
        self.assertFalse(self.usuario.tem_permissao('estoque', 'criar'))
        self.assertTrue(self.usuario.tem_permissao('estoque', 'editar'))
        self.assertFalse(self.usuario.tem_permissao('estoque', 'aprovar'))

        self.assertEqual(MovimentacaoManualView.permissao_acao, 'criar')
        self.assertEqual(AjusteEstoqueView.permissao_acao, 'editar')
        self.assertEqual(TransferenciaView.permissao_acao, 'aprovar')
        self.assertEqual(InventarioCreateView.permissao_acao, 'criar')
        self.assertEqual(LoteCreateView.permissao_acao, 'criar')
        self.assertEqual(LoteUpdateView.permissao_acao, 'editar')
        self.assertEqual(InventarioCancelView.permissao_acao, 'cancelar')
        self.assertEqual(LoteBaixaValidadeView.permissao_acao, 'cancelar')

    def test_usuario_sem_permissao_bloqueia_urls_criticas_estoque(self):
        self.conceder(pode_ver=True)
        produto = self.criar_produto(controla_lote=True)
        lote = self.criar_lote(produto)
        inventario = Inventario.objects.create(
            filial=self.filial,
            descricao='Inventario bloqueado',
            status=Inventario.Status.ABERTO,
            usuario_inicio=self.usuario,
            data_inicio=timezone.now(),
        )

        rotas_bloqueadas = [
            (MovimentacaoManualView.as_view(), 'get', reverse('estoque:movimentacao-create'), {}),
            (AjusteEstoqueView.as_view(), 'get', reverse('estoque:ajuste'), {}),
            (TransferenciaView.as_view(), 'get', reverse('estoque:transferencia'), {}),
            (InventarioCreateView.as_view(), 'get', reverse('estoque:inventario-create'), {}),
            (LoteCreateView.as_view(), 'get', reverse('estoque:lote-create'), {}),
            (LoteUpdateView.as_view(), 'get', reverse('estoque:lote-update', args=[lote.pk]), {'pk': lote.pk}),
            (InventarioCancelView.as_view(), 'post', reverse('estoque:inventario-cancel', args=[inventario.pk]), {'pk': inventario.pk}),
            (LoteBaixaValidadeView.as_view(), 'post', reverse('estoque:lote-baixa-validade', args=[lote.pk]), {'pk': lote.pk}),
        ]

        for view, method, path, kwargs in rotas_bloqueadas:
            request = self.factory.post(path, {}) if method == 'post' else self.factory.get(path)
            request.user = self.usuario
            request.filial_ativa = self.filial
            request.session = {'filial_ativa_id': self.filial.pk}
            from django.contrib.messages.storage.fallback import FallbackStorage
            request._messages = FallbackStorage(request)

            response = view(request, **kwargs)

            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.url, reverse('core:dashboard'))
            self.assertIn('Você não tem permissão para esta ação.', [str(m) for m in request._messages])

    def test_exportacoes_estoque_exigem_permissao_padrao(self):
        self.conceder(pode_ver=True, pode_exportar=False)
        response = self.client.get(reverse('estoque:movimentacao-list'), {'export': 'csv'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('estoque:movimentacao-list'))
        mensagens = [str(message) for message in response.wsgi_request._messages]
        self.assertIn('Você não tem permissão para esta ação.', mensagens)

        response = self.client.get(reverse('estoque:movimentacao-list'), {'export': 'pdf'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('estoque:movimentacao-list'))

    def test_exportacoes_movimentacao_com_permissao_retornam_csv_e_pdf(self):
        self.conceder(pode_ver=True, pode_exportar=True)
        produto = self.criar_produto(descricao='Produto Movimento Export')
        MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk,
            filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal('5'),
            usuario_id=self.usuario.pk,
            valor_unitario=Decimal('2.50'),
            documento_numero='DOC-EXP',
        )

        csv_response = self.client.get(reverse('estoque:movimentacao-list'), {'export': 'csv'})
        self.assertEqual(csv_response.status_code, 200)
        self.assertIn('text/csv', csv_response['Content-Type'])
        conteudo = csv_response.content.decode('utf-8-sig')
        self.assertIn('Saldo apos', conteudo)
        self.assertIn('Custo medio apos', conteudo)

        pdf_response = self.client.get(reverse('estoque:movimentacao-list'), {'export': 'pdf'})
        self.assertEqual(pdf_response.status_code, 200)
        self.assertEqual(pdf_response['Content-Type'], 'application/pdf')
        self.assertIn('movimentacoes_estoque.pdf', pdf_response['Content-Disposition'])

    def test_ajuste_manual_cria_auditoria_e_aparece_no_extrato_produto(self):
        self.conceder(pode_ver=True, pode_editar=True)
        produto = self.criar_produto(descricao='Produto auditado estoque')
        MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk,
            filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal('3'),
            usuario_id=self.usuario.pk,
            valor_unitario=Decimal('2.00'),
        )
        request = self.factory.post(reverse('estoque:ajuste'), {
            'produto': produto.pk,
            'quantidade_nova': '5',
            'justificativa': 'Conferencia fisica do freezer',
        })
        request.user = self.usuario
        request.filial_ativa = self.filial
        request.session = {'filial_ativa_id': self.filial.pk}
        from django.contrib.messages.storage.fallback import FallbackStorage
        request._messages = FallbackStorage(request)

        response = AjusteEstoqueView.as_view()(request)

        self.assertEqual(response.status_code, 302)
        log = RegistroAuditoria.objects.get(modulo='estoque', acao='ajustar')
        self.assertEqual(log.relacionado_tipo, 'produtos.produto')
        self.assertEqual(log.relacionado_id, produto.pk)
        self.assertEqual(log.justificativa, 'Conferencia fisica do freezer')

        request_get = self.factory.get(reverse('estoque:movimentacao-list'), {'produto': str(produto.pk)})
        request_get.user = self.usuario
        request_get.filial_ativa = self.filial
        request_get.session = {'filial_ativa_id': self.filial.pk}
        response = MovimentacaoListView.as_view()(request_get)
        self.assertContains(response, 'Auditoria do produto')
        self.assertContains(response, 'Conferencia fisica do freezer')

    def test_cancelamento_inventario_exige_justificativa_e_cria_auditoria(self):
        self.conceder(pode_ver=True, pode_cancelar=True)
        inventario = Inventario.objects.create(
            filial=self.filial,
            descricao='Inventario auditoria',
            status=Inventario.Status.ABERTO,
            usuario_inicio=self.usuario,
            data_inicio=timezone.now(),
        )
        request_sem_motivo = self.factory.post(reverse('estoque:inventario-cancel', args=[inventario.pk]), {})
        request_sem_motivo.user = self.usuario
        request_sem_motivo.filial_ativa = self.filial
        request_sem_motivo.session = {'filial_ativa_id': self.filial.pk}
        from django.contrib.messages.storage.fallback import FallbackStorage
        request_sem_motivo._messages = FallbackStorage(request_sem_motivo)

        response = InventarioCancelView.as_view()(request_sem_motivo, pk=inventario.pk)

        self.assertEqual(response.url, reverse('estoque:inventario-detail', args=[inventario.pk]))
        self.assertFalse(RegistroAuditoria.objects.filter(objeto_id=inventario.pk, acao='cancelar').exists())

        request = self.factory.post(reverse('estoque:inventario-cancel', args=[inventario.pk]), {
            'motivo': 'Inventario aberto em duplicidade',
        })
        request.user = self.usuario
        request.filial_ativa = self.filial
        request.session = {'filial_ativa_id': self.filial.pk}
        request._messages = FallbackStorage(request)

        response = InventarioCancelView.as_view()(request, pk=inventario.pk)

        self.assertEqual(response.url, reverse('estoque:inventario-list'))
        log = RegistroAuditoria.objects.get(
            modulo='estoque',
            acao='cancelar',
            objeto_tipo='estoque.inventario',
            objeto_id=inventario.pk,
        )
        self.assertEqual(log.justificativa, 'Inventario aberto em duplicidade')

    def test_painel_estoque_custos_entrada_exibe_notas_para_revisao(self):
        self.conceder(pode_ver=True)
        self.conceder('compras', pode_ver=True, pode_editar=True)
        entrada = EntradaNF.objects.create(
            filial=self.filial,
            fornecedor=self.fornecedor,
            numero_nf='9001',
            serie_nf='1',
            origem_entrada=EntradaNF.OrigemEntrada.XML,
            data_emissao_nf=timezone.localdate(),
            data_entrada=timezone.now(),
            status=EntradaNF.Status.CONFERIDA,
            usuario=self.usuario,
            valor_produtos=Decimal('100.00'),
            valor_frete=Decimal('10.00'),
            valor_icms_st=Decimal('7.00'),
            valor_total=Decimal('117.00'),
        )

        request = self.factory.get(reverse('estoque:entrada-custos-list'), {'custo': 'pendente'})
        request.user = self.usuario
        request.filial_ativa = self.filial
        request.session = {'filial_ativa_id': self.filial.pk}

        response = EntradaCustoEstoqueListView.as_view()(request)
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn('Custos de entrada', content)
        self.assertIn('NF 9001/1', content)
        self.assertIn('Revisar', content)
        self.assertIn('Frete', content)
        self.assertIn('Composicao acumulada do filtro', content)
        self.assertIn('R$ 10,00', content)
        self.assertIn('R$ 7,00', content)
        self.assertIn('ICMS ST', content)
        self.assertIn(reverse('compras:entrada-custos', args=[entrada.pk]), content)

    def test_composicao_custo_alerta_variacao_referencia(self):
        produto = self.criar_produto(descricao='Produto Custo Alerta')
        produto.preco_custo = Decimal('10.00')
        produto.save(update_fields=['preco_custo', 'updated_at'])
        entrada = EntradaNF.objects.create(
            filial=self.filial,
            fornecedor=self.fornecedor,
            numero_nf='9002',
            serie_nf='1',
            origem_entrada=EntradaNF.OrigemEntrada.MANUAL,
            data_emissao_nf=timezone.localdate(),
            data_entrada=timezone.now(),
            status=EntradaNF.Status.CONFERIDA,
            usuario=self.usuario,
            valor_produtos=Decimal('200.00'),
            valor_total=Decimal('200.00'),
        )
        item = ItemEntradaNF.objects.create(
            entrada=entrada,
            produto=produto,
            numero_item=1,
            quantidade=Decimal('10'),
            quantidade_xml=Decimal('10'),
            quantidade_estoque=Decimal('10'),
            quantidade_recebida=Decimal('10'),
            unidade_xml='UN',
            unidade_estoque='UN',
            valor_unitario=Decimal('20.00'),
            valor_bruto=Decimal('200.00'),
            valor_total=Decimal('200.00'),
        )

        composicao = EntradaCustoService.compor(entrada)
        linha = composicao['linhas'][0]

        self.assertEqual(linha.item, item)
        self.assertEqual(linha.custo_referencia, Decimal('10.0000'))
        self.assertEqual(linha.alerta_custo_nivel, 'critico')
        self.assertEqual(composicao['resumo']['alertas_custo_criticos'], 1)

    def test_reposicao_gera_pedido_compra_em_rascunho(self):
        self.conceder(pode_ver=True, pode_editar=True)
        self.conceder('compras', pode_ver=True, pode_criar=True)
        produto = self.criar_produto(fornecedor=self.fornecedor)
        produto.estoque_minimo = Decimal('5')
        produto.estoque_maximo = Decimal('10')
        produto.preco_custo = Decimal('3.50')
        produto.save(update_fields=['estoque_minimo', 'estoque_maximo', 'preco_custo', 'updated_at'])
        MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk,
            filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal('2'),
            usuario_id=self.usuario.pk,
            valor_unitario=Decimal('3.50'),
        )

        response = self.client.post(
            reverse('estoque:reposicao-list'),
            {'produto': [str(produto.pk)]},
        )

        self.assertEqual(response.status_code, 302)
        pedido = PedidoCompra.objects.get(filial=self.filial, fornecedor=self.fornecedor)
        self.assertEqual(pedido.status, PedidoCompra.Status.RASCUNHO)
        item = pedido.itens.get(produto=produto)
        self.assertEqual(item.quantidade, Decimal('8.000'))

    def test_reposicao_usa_quantidade_ajustada_no_pedido(self):
        self.conceder(pode_ver=True, pode_editar=True)
        self.conceder('compras', pode_ver=True, pode_criar=True)
        produto = self.criar_produto(descricao='Produto Repor Ajustado', fornecedor=self.fornecedor)
        produto.estoque_minimo = Decimal('5')
        produto.estoque_maximo = Decimal('10')
        produto.preco_custo = Decimal('3.50')
        produto.save(update_fields=['estoque_minimo', 'estoque_maximo', 'preco_custo', 'updated_at'])
        MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk,
            filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal('2'),
            usuario_id=self.usuario.pk,
            valor_unitario=Decimal('3.50'),
        )

        response = self.client.post(
            reverse('estoque:reposicao-list'),
            {
                'produto': [str(produto.pk)],
                f'quantidade_desktop_{produto.pk}': '12,5',
            },
        )

        self.assertEqual(response.status_code, 302)
        item = PedidoCompra.objects.get(filial=self.filial, fornecedor=self.fornecedor).itens.get(
            produto=produto,
        )
        self.assertEqual(item.quantidade, Decimal('12.500'))

    def test_reposicao_reaproveita_rascunho_e_nao_duplica_item(self):
        self.conceder(pode_ver=True, pode_editar=True)
        self.conceder('compras', pode_ver=True, pode_criar=True)
        produto = self.criar_produto(descricao='Produto Repor Idempotente', fornecedor=self.fornecedor)
        produto.estoque_minimo = Decimal('5')
        produto.estoque_maximo = Decimal('10')
        produto.preco_custo = Decimal('3.50')
        produto.save(update_fields=['estoque_minimo', 'estoque_maximo', 'preco_custo', 'updated_at'])

        self.client.post(reverse('estoque:reposicao-list'), {'produto': [str(produto.pk)]})
        response = self.client.post(
            reverse('estoque:reposicao-list'),
            {
                'produto': [str(produto.pk)],
                f'quantidade_desktop_{produto.pk}': '7,5',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(PedidoCompra.objects.filter(filial=self.filial, fornecedor=self.fornecedor).count(), 1)
        pedido = PedidoCompra.objects.get(filial=self.filial, fornecedor=self.fornecedor)
        self.assertEqual(pedido.itens.filter(produto=produto).count(), 1)
        self.assertEqual(pedido.itens.get(produto=produto).quantidade, Decimal('7.500'))
        self.assertIn('Reposicao estoque:', pedido.itens.get(produto=produto).observacao)

    def test_reposicao_separa_pedidos_por_fornecedor_e_audita(self):
        self.conceder(pode_ver=True, pode_editar=True)
        self.conceder('compras', pode_ver=True, pode_criar=True)
        fornecedor_2 = Fornecedor.objects.create(
            filial=self.filial,
            tipo_pessoa='J',
            razao_social='Fornecedor Dois',
            cpf_cnpj='22345678000195',
            uf='RN',
        )
        FornecedorFilial.objects.create(fornecedor=fornecedor_2, filial=self.filial)
        produto_a = self.criar_produto(descricao='Produto Repor A', fornecedor=self.fornecedor)
        produto_b = self.criar_produto(descricao='Produto Repor B', fornecedor=fornecedor_2)
        for produto in (produto_a, produto_b):
            produto.estoque_minimo = Decimal('5')
            produto.estoque_maximo = Decimal('10')
            produto.preco_custo = Decimal('2.50')
            produto.save(update_fields=['estoque_minimo', 'estoque_maximo', 'preco_custo', 'updated_at'])

        response = self.client.post(
            reverse('estoque:reposicao-list'),
            {
                'produto': [str(produto_a.pk), str(produto_b.pk)],
                f'quantidade_desktop_{produto_a.pk}': '6',
                f'quantidade_desktop_{produto_b.pk}': '8',
            },
        )

        self.assertEqual(response.status_code, 302)
        pedidos = PedidoCompra.objects.filter(filial=self.filial).order_by('fornecedor__razao_social')
        self.assertEqual(pedidos.count(), 2)
        self.assertTrue(all('reposicao_estoque' in pedido.observacao for pedido in pedidos))
        self.assertTrue(RegistroAuditoria.objects.filter(modulo='estoque', objeto_id=str(pedidos[0].pk)).exists())

    def test_tela_reposicao_mostra_fluxo_prontidao_e_pedido_aberto(self):
        self.conceder(pode_ver=True, pode_editar=True)
        self.conceder('compras', pode_ver=True, pode_criar=True)
        produto = self.criar_produto(descricao='Produto Repor Fluxo', fornecedor=self.fornecedor)
        produto.estoque_minimo = Decimal('5')
        produto.estoque_maximo = Decimal('10')
        produto.rascunho_comercial = True
        produto.save(update_fields=['estoque_minimo', 'estoque_maximo', 'rascunho_comercial', 'updated_at'])
        pedido = PedidoCompra.objects.create(
            filial=self.filial,
            fornecedor=self.fornecedor,
            usuario=self.usuario,
            numero_pedido='PC-FLUXO',
            status=PedidoCompra.Status.APROVADO,
            data_emissao=timezone.now(),
            observacao='Gerado pelo plano de reposicao de estoque. Origem: reposicao_estoque.',
        )
        pedido.itens.create(
            produto=produto,
            numero_item=1,
            quantidade=Decimal('5'),
            valor_unitario=Decimal('2'),
            valor_bruto=Decimal('10'),
            valor_total=Decimal('10'),
        )

        request = self.factory.get(reverse('estoque:reposicao-list'))
        request.user = self.usuario
        request.filial_ativa = self.filial
        response = ReposicaoListView.as_view()(request)
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn('Aguardando entrada', content)
        self.assertIn('PC-FLUXO', content)
        self.assertIn('Revisar produto', content)
        self.assertIn('Selecionar todos', content)

    def test_reposicao_rejeita_quantidade_zerada(self):
        self.conceder(pode_ver=True, pode_editar=True)
        self.conceder('compras', pode_ver=True, pode_criar=True)
        produto = self.criar_produto(descricao='Produto Repor Zerado', fornecedor=self.fornecedor)
        produto.estoque_minimo = Decimal('5')
        produto.estoque_maximo = Decimal('10')
        produto.save(update_fields=['estoque_minimo', 'estoque_maximo', 'updated_at'])

        response = self.client.post(
            reverse('estoque:reposicao-list'),
            {
                'produto': [str(produto.pk)],
                f'quantidade_desktop_{produto.pk}': '0',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(PedidoCompra.objects.filter(filial=self.filial).exists())

    def test_tela_reposicao_renderiza_sugestoes(self):
        self.conceder(pode_ver=True)
        produto = self.criar_produto(descricao='Produto Repor', fornecedor=self.fornecedor)
        produto.estoque_minimo = Decimal('5')
        produto.estoque_maximo = Decimal('10')
        produto.preco_custo = Decimal('3.00')
        produto.lead_time_reposicao_dias = 4
        produto.save(update_fields=[
            'estoque_minimo',
            'estoque_maximo',
            'preco_custo',
            'lead_time_reposicao_dias',
            'updated_at',
        ])

        path = reverse('estoque:reposicao-list')
        request = self.factory.get(path)
        request.user = self.usuario
        request.filial_ativa = self.filial
        response = ReposicaoListView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Produto Repor', response.content)
        self.assertIn(b'Comprar', response.content)
        self.assertIn(b'Abaixo do minimo', response.content)
        self.assertIn(b'4 dias', response.content)
        self.assertIn(b'Valor estimado', response.content)
        self.assertIn(b'Revisar necessidade', response.content)

    def test_tela_lotes_renderiza_cards_mobile(self):
        self.conceder(pode_ver=True, pode_editar=True)
        produto = self.criar_produto(descricao='Produto Lote Mobile', controla_lote=True)
        lote = self.criar_lote(produto)
        lote.numero_lote = 'LT-MOBILE'
        lote.data_validade = timezone.now().date()
        lote.quantidade_atual = Decimal('3')
        lote.save(update_fields=['numero_lote', 'data_validade', 'quantidade_atual', 'updated_at'])

        path = reverse('estoque:lote-list')
        request = self.factory.get(path)
        request.user = self.usuario
        request.filial_ativa = self.filial
        response = LoteListView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'md:hidden', response.content)
        self.assertIn(b'LT-MOBILE', response.content)
        self.assertIn(b'Produto Lote Mobile', response.content)
        self.assertIn(b'Validade', response.content)
        self.assertIn(b'Valor lote', response.content)
        self.assertIn(b'Movimentos', response.content)

    def test_tela_lotes_exibe_nf_origem_de_entrada(self):
        self.conceder(pode_ver=True)
        produto = self.criar_produto(descricao='Produto Lote Origem', controla_lote=True)
        lote = self.criar_lote_com_entrada(produto, 'LT-ORIGEM', '3')
        entrada = EntradaNF.objects.create(
            filial=self.filial,
            fornecedor=self.fornecedor,
            numero_nf='9100',
            serie_nf='1',
            origem_entrada=EntradaNF.OrigemEntrada.MANUAL,
            data_emissao_nf=timezone.localdate(),
            data_entrada=timezone.now(),
            status=EntradaNF.Status.EFETIVADA,
            usuario=self.usuario,
            valor_produtos=Decimal('6.00'),
            valor_total=Decimal('6.00'),
        )
        ItemEntradaNF.objects.create(
            entrada=entrada,
            produto=produto,
            lote_gerado=lote,
            numero_item=1,
            quantidade=Decimal('3'),
            quantidade_xml=Decimal('3'),
            quantidade_estoque=Decimal('3'),
            quantidade_recebida=Decimal('3'),
            unidade_xml='UN',
            unidade_estoque='UN',
            valor_unitario=Decimal('2.00'),
            custo_unitario_total=Decimal('2.0000'),
            valor_bruto=Decimal('6.00'),
            valor_total=Decimal('6.00'),
        )

        path = reverse('estoque:lote-list')
        request = self.factory.get(path, {'q': 'LT-ORIGEM'})
        request.user = self.usuario
        request.filial_ativa = self.filial
        response = LoteListView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'NF 9100/1', response.content)
        self.assertIn(b'1 mov.', response.content)

        request = self.factory.get(path, {'q': '9100'})
        request.user = self.usuario
        request.filial_ativa = self.filial
        response = LoteListView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'LT-ORIGEM', response.content)
        self.assertIn(b'NF 9100/1', response.content)

    def test_tela_movimentacoes_renderiza_cards_mobile(self):
        self.conceder(pode_ver=True, pode_editar=True, pode_exportar=True)
        produto = self.criar_produto(descricao='Produto Movimento Mobile')
        MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk,
            filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal('5'),
            usuario_id=self.usuario.pk,
            valor_unitario=Decimal('2.50'),
            documento_numero='DOC-MOBILE',
        )

        path = reverse('estoque:movimentacao-list')
        request = self.factory.get(path)
        request.user = self.usuario
        request.filial_ativa = self.filial
        response = MovimentacaoListView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'md:hidden', response.content)
        self.assertIn(b'Produto Movimento Mobile', response.content)
        self.assertIn(b'Saldo apos', response.content)
        self.assertIn(b'Custo medio', response.content)
        self.assertIn(b'Exportar Excel', response.content)
        self.assertIn(b'Exportar PDF', response.content)
        self.assertIn(b'DOC-MOBILE', response.content)

    def test_tela_movimentacoes_linka_movimento_de_nfe_para_entrada(self):
        self.conceder(pode_ver=True, pode_editar=True)
        produto = self.criar_produto(descricao='Produto Movimento NFE')
        entrada = EntradaNF.objects.create(
            filial=self.filial,
            fornecedor=self.fornecedor,
            numero_nf='9101',
            serie_nf='1',
            data_entrada=timezone.now(),
            data_emissao_nf=timezone.localdate(),
            status=EntradaNF.Status.EFETIVADA,
            usuario=self.usuario,
        )
        MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk,
            filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal('5'),
            usuario_id=self.usuario.pk,
            valor_unitario=Decimal('2.50'),
            documento_tipo=MovimentacaoEstoque.DocumentoTipo.NFE,
            documento_id=entrada.pk,
            documento_numero='NF 9101/1',
        )

        path = reverse('estoque:movimentacao-list')
        request = self.factory.get(path)
        request.user = self.usuario
        request.filial_ativa = self.filial
        response = MovimentacaoListView.as_view()(request)
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn('Entrada NF 9101/1', content)
        self.assertIn(reverse('compras:entrada-detail', args=[entrada.pk]), content)

    def test_tela_inventario_renderiza_cards_mobile_editaveis(self):
        self.conceder(pode_ver=True, pode_editar=True, pode_aprovar=True)
        produto = self.criar_produto(descricao='Produto Inventario Mobile', controla_lote=True)
        lote = self.criar_lote_com_entrada(produto, 'INV-MOBILE', '4')
        inventario = Inventario.objects.create(
            filial=self.filial,
            descricao='Inventario mobile',
            status=Inventario.Status.ABERTO,
            data_inicio=timezone.now(),
            usuario_inicio=self.usuario,
        )
        _criar_itens_inventario(inventario, self.filial)
        item = inventario.itens.get(lote=lote)

        path = reverse('estoque:inventario-detail', args=[inventario.pk])
        request = self.factory.get(path)
        request.user = self.usuario
        request.filial_ativa = self.filial
        response = InventarioDetailView.as_view()(request, pk=inventario.pk)
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn('md:hidden', content)
        self.assertIn('Produto Inventario Mobile', content)
        self.assertIn('INV-MOBILE', content)
        self.assertIn(f'name="quantidade_contada_{item.pk}"', content)
        self.assertIn('Salvar contagem', content)

    def test_inventario_de_produto_controlado_cria_itens_por_lote(self):
        produto = self.criar_produto(descricao='Produto Inventario Lote', controla_lote=True)
        lote_a = self.criar_lote_com_entrada(produto, 'INV-A', '5')
        lote_b = self.criar_lote_com_entrada(produto, 'INV-B', '3')
        inventario = Inventario.objects.create(
            filial=self.filial,
            descricao='Inventario por lote',
            status=Inventario.Status.ABERTO,
            data_inicio=timezone.now(),
            usuario_inicio=self.usuario,
        )

        _criar_itens_inventario(inventario, self.filial)

        itens = inventario.itens.order_by('lote__numero_lote')
        self.assertEqual(itens.count(), 2)
        self.assertEqual([item.lote for item in itens], [lote_a, lote_b])
        self.assertEqual([item.quantidade_sistema for item in itens], [Decimal('5.000'), Decimal('3.000')])

    def test_fechamento_inventario_ajusta_lote_sem_zerar_outros_lotes(self):
        self.conceder(pode_ver=True, pode_editar=True, pode_aprovar=True)
        produto = self.criar_produto(descricao='Produto Inventario Ajuste Lote', controla_lote=True)
        lote_a = self.criar_lote_com_entrada(produto, 'INV-AJUSTE-A', '5')
        lote_b = self.criar_lote_com_entrada(produto, 'INV-AJUSTE-B', '3')
        inventario = Inventario.objects.create(
            filial=self.filial,
            descricao='Fechamento por lote',
            status=Inventario.Status.ABERTO,
            data_inicio=timezone.now(),
            usuario_inicio=self.usuario,
        )
        _criar_itens_inventario(inventario, self.filial)
        item_a = inventario.itens.get(lote=lote_a)
        item_b = inventario.itens.get(lote=lote_b)

        response = self.client.post(
            reverse('estoque:inventario-detail', args=[inventario.pk]),
            {
                f'quantidade_contada_{item_a.pk}': '4',
                f'justificativa_{item_a.pk}': 'Perda fisica',
                f'quantidade_contada_{item_b.pk}': '3',
                f'justificativa_{item_b.pk}': '',
                'acao': 'fechar',
            },
        )

        estoque = Estoque.objects.get(produto=produto, filial=self.filial)
        lote_a.refresh_from_db()
        lote_b.refresh_from_db()
        inventario.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(inventario.status, Inventario.Status.FECHADO)
        self.assertEqual(lote_a.quantidade_atual, Decimal('4.000'))
        self.assertEqual(lote_b.quantidade_atual, Decimal('3.000'))
        self.assertEqual(estoque.quantidade_atual, Decimal('7.000'))
        self.assertTrue(
            MovimentacaoEstoque.objects.filter(
                tipo_operacao=MovimentacaoEstoque.TipoOperacao.AJUSTE_MENOS,
                lote=lote_a,
                documento_tipo=MovimentacaoEstoque.DocumentoTipo.INVENTARIO,
            ).exists()
        )

    def test_relatorio_divergencias_inventario_abre_com_permissao_ver(self):
        self.conceder(pode_ver=True)
        produto = self.criar_produto()
        inventario = Inventario.objects.create(
            filial=self.filial,
            descricao='Inventario fechado',
            status=Inventario.Status.FECHADO,
            data_inicio=timezone.now(),
            usuario_inicio=self.usuario,
        )
        ItemInventario.objects.create(
            inventario=inventario,
            produto=produto,
            quantidade_sistema=Decimal('10'),
            quantidade_contada=Decimal('8'),
            diferenca=Decimal('-2'),
            valor_unitario=Decimal('3.50'),
            valor_diferenca=Decimal('-7.00'),
        )

        path = reverse('estoque:inventario-divergencias', args=[inventario.pk])
        request = self.factory.get(path)
        request.user = self.usuario
        request.filial_ativa = self.filial
        response = InventarioDivergenciasView.as_view()(request, pk=inventario.pk)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Produto Teste', response.content)
        self.assertIn(b'Falta', response.content)

    def test_relatorio_divergencias_exporta_csv_proprio(self):
        self.conceder(pode_ver=True, pode_exportar=True)
        produto_divergente = self.criar_produto(descricao='Produto Divergente')
        produto_ok = self.criar_produto(descricao='Produto OK')
        inventario = Inventario.objects.create(
            filial=self.filial,
            descricao='Inventario fechado',
            status=Inventario.Status.FECHADO,
            data_inicio=timezone.now(),
            usuario_inicio=self.usuario,
        )
        ItemInventario.objects.create(
            inventario=inventario,
            produto=produto_divergente,
            quantidade_sistema=Decimal('10'),
            quantidade_contada=Decimal('8'),
            diferenca=Decimal('-2'),
            valor_unitario=Decimal('3.50'),
            valor_diferenca=Decimal('-7.00'),
            justificativa='Quebra encontrada',
        )
        ItemInventario.objects.create(
            inventario=inventario,
            produto=produto_ok,
            quantidade_sistema=Decimal('4'),
            quantidade_contada=Decimal('4'),
            diferenca=Decimal('0'),
            valor_unitario=Decimal('2.00'),
            valor_diferenca=Decimal('0.00'),
        )

        response = self.client.get(
            reverse('estoque:inventario-divergencias', args=[inventario.pk]),
            {'export': 'csv'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('text/csv', response['Content-Type'])
        content = response.content.decode('utf-8-sig')
        self.assertIn('Tipo divergencia', content)
        self.assertIn('Produto Divergente', content)
        self.assertIn('Falta', content)
        self.assertNotIn('Produto OK', content)

    def test_relatorio_divergencias_exporta_pdf_proprio(self):
        self.conceder(pode_ver=True, pode_exportar=True)
        produto = self.criar_produto(descricao='Produto Divergente PDF')
        inventario = Inventario.objects.create(
            filial=self.filial,
            descricao='Inventario fechado PDF',
            status=Inventario.Status.FECHADO,
            data_inicio=timezone.now(),
            usuario_inicio=self.usuario,
        )
        ItemInventario.objects.create(
            inventario=inventario,
            produto=produto,
            quantidade_sistema=Decimal('10'),
            quantidade_contada=Decimal('8'),
            diferenca=Decimal('-2'),
            valor_unitario=Decimal('3.50'),
            valor_diferenca=Decimal('-7.00'),
            justificativa='Quebra encontrada',
        )

        response = self.client.get(
            reverse('estoque:inventario-divergencias', args=[inventario.pk]),
            {'export': 'pdf'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn(
            f'inventario_{inventario.pk}_divergencias.pdf',
            response['Content-Disposition'],
        )

    def test_lista_inventario_mostra_atalho_de_divergencia_fechada(self):
        self.conceder(pode_ver=True)
        produto = self.criar_produto()
        inventario = Inventario.objects.create(
            filial=self.filial,
            descricao='Inventario fechado com falta',
            status=Inventario.Status.FECHADO,
            data_inicio=timezone.now(),
            usuario_inicio=self.usuario,
        )
        ItemInventario.objects.create(
            inventario=inventario,
            produto=produto,
            quantidade_sistema=Decimal('10'),
            quantidade_contada=Decimal('8'),
            diferenca=Decimal('-2'),
            valor_unitario=Decimal('3.50'),
            valor_diferenca=Decimal('-7.00'),
        )

        path = reverse('estoque:inventario-list')
        request = self.factory.get(path)
        request.user = self.usuario
        request.filial_ativa = self.filial
        response = InventarioListView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'1 divergencia', response.content)
        self.assertIn(reverse('estoque:inventario-divergencias', args=[inventario.pk]).encode(), response.content)

    def test_relatorio_estoque_renderiza_indicadores_operacionais(self):
        self.conceder(pode_ver=True, pode_exportar=True, pode_editar=True, pode_cancelar=True, pode_aprovar=True)
        self.conceder('compras', pode_ver=True, pode_editar=True)
        produto = self.criar_produto(descricao='Produto Relatorio Estoque')
        produto.estoque_minimo = Decimal('5')
        produto.preco_venda = Decimal('12.00')
        produto.preco_custo = Decimal('4.00')
        produto.save(update_fields=[
            'estoque_minimo',
            'preco_venda',
            'preco_custo',
            'updated_at',
        ])
        MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk,
            filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal('2'),
            usuario_id=self.usuario.pk,
            valor_unitario=Decimal('4.00'),
        )
        inventario = Inventario.objects.create(
            filial=self.filial,
            descricao='Inventario relatorio',
            status=Inventario.Status.FECHADO,
            data_inicio=timezone.now(),
            data_fim=timezone.now(),
            usuario_inicio=self.usuario,
            usuario_fechamento=self.usuario,
        )
        ItemInventario.objects.create(
            inventario=inventario,
            produto=produto,
            quantidade_sistema=Decimal('2'),
            quantidade_contada=Decimal('1'),
            diferenca=Decimal('-1'),
            valor_unitario=Decimal('4.00'),
            valor_diferenca=Decimal('-4.00'),
        )

        path = reverse('estoque:relatorio-list')
        request = self.factory.get(path)
        request.user = self.usuario
        request.filial_ativa = self.filial
        response = RelatorioEstoqueView.as_view()(request)
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn('Relatorios operacionais', content)
        self.assertIn('Valor em custo', content)
        self.assertIn('Estoque por filial', content)
        self.assertIn('Permissoes criticas', content)
        self.assertIn('Inventario relatorio', content)

    def test_fechar_inventario_exige_permissao_aprovar(self):
        self.conceder(pode_ver=True, pode_editar=True)
        produto = self.criar_produto()
        inventario = Inventario.objects.create(
            filial=self.filial,
            descricao='Inventario aberto',
            status=Inventario.Status.ABERTO,
            data_inicio=timezone.now(),
            usuario_inicio=self.usuario,
        )
        item = ItemInventario.objects.create(
            inventario=inventario,
            produto=produto,
            quantidade_sistema=Decimal('10'),
            valor_unitario=Decimal('3.50'),
        )

        response = self.client.post(reverse('estoque:inventario-detail', args=[inventario.pk]), {
            f'quantidade_contada_{item.pk}': '8',
            f'justificativa_{item.pk}': 'Quebra',
            'acao': 'fechar',
        })

        self.assertEqual(response.status_code, 302)
        inventario.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(inventario.status, Inventario.Status.ABERTO)
        self.assertIsNone(item.quantidade_contada)

    def test_detalhe_inventario_sem_aprovar_oculta_fechamento(self):
        self.conceder(pode_ver=True, pode_editar=True)
        produto = self.criar_produto()
        inventario = Inventario.objects.create(
            filial=self.filial,
            descricao='Inventario em contagem',
            status=Inventario.Status.EM_CONTAGEM,
            data_inicio=timezone.now(),
            usuario_inicio=self.usuario,
        )
        ItemInventario.objects.create(
            inventario=inventario,
            produto=produto,
            quantidade_sistema=Decimal('10'),
            valor_unitario=Decimal('3.50'),
        )

        request = self.factory.get(reverse('estoque:inventario-detail', args=[inventario.pk]))
        request.user = self.usuario
        request.filial_ativa = self.filial
        response = InventarioDetailView.as_view()(request, pk=inventario.pk)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Salvar contagem', response.content)
        self.assertNotIn(b'Fechar e ajustar estoque', response.content)
