from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch

from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase
from django.utils import timezone

from apps.cadastros.models import Fornecedor, FornecedorFilial
from apps.compras.models import (
    CalculoTributarioCompra, CotacaoCompraFornecedor, EntradaNF, ItemEntradaNF,
    RegraTributariaCompra,
)
from apps.compras.services.cotacao_compra_service import CotacaoCompraService
from apps.compras.services.purchase_tax_service import PurchaseTaxService
from apps.compras.views.cotacao import CotacaoCompraDetailView, CotacaoCompraNovaView
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.produtos.models import Produto, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial


class CotacaoCompraTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Compradora LTDA',
            nome_fantasia='Compradora',
            cnpj='12345678000190',
            regime_tributario=Empresa.RegimeTributario.LUCRO_REAL,
            regime_ibs_cbs='regular',
            codigo_regime_tributario=3,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa,
            razao_social='Compradora Matriz',
            cnpj='12345678000191',
            uf='RN',
        )
        perfil = PerfilAcesso.objects.create(empresa=cls.empresa, nome='Admin', is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email='cotacao@inoovated.com', nome='Comprador', password='teste1234',
            empresa=cls.empresa, filial=cls.filial, perfil=perfil,
        )
        unidade = UnidadeMedida.objects.create(empresa=cls.empresa, sigla='UN', descricao='Unidade')
        UnidadeMedidaFilial.objects.create(unidade=unidade, filial=cls.filial)
        cls.produto = Produto.objects.create(
            filial=cls.filial, unidade_medida=unidade, descricao='Tecido Dry Fit',
            codigo='TEC-01', ncm='60063200', preco_custo=0, preco_venda=20,
        )
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.filial)

        cls.simples = cls._fornecedor('Fornecedor Simples', '11111111000191', 'simples_nacional', 'simples')
        cls.regular = cls._fornecedor('Fornecedor Regular', '22222222000191', 'lucro_real', 'regular')

    @classmethod
    def _fornecedor(cls, nome, cnpj, regime, ibs):
        fornecedor = Fornecedor.objects.create(
            filial=cls.filial, tipo_pessoa='J', razao_social=nome, cpf_cnpj=cnpj,
            regime_tributario=regime, regime_ibs_cbs=ibs,
        )
        FornecedorFilial.objects.create(fornecedor=fornecedor, filial=cls.filial)
        return fornecedor

    def setUp(self):
        self.factory = RequestFactory()
        RegraTributariaCompra.objects.create(
            empresa=self.empresa,
            nome='Credito regular parametrizado',
            data_inicial=date(2026, 1, 1),
            regime_fornecedor='lucro_real',
            regime_ibs_cbs_comprador='regular',
            regime_ibs_cbs_fornecedor='regular',
            percentual_credito_ibs=Decimal('12'),
            percentual_credito_cbs=Decimal('8'),
        )

    def _request(self, path, query=None):
        request = self.factory.get(path, query or {})
        request.user = self.usuario
        request.filial_ativa = self.filial
        request.session = {}
        return request

    def _payload(self):
        return {
            'buyer_regime': 'lucro_real',
            'buyer_ibs_cbs': 'regular',
            'reference_date': '2026-09-13',
            'products': [{'id': self.produto.pk, 'quantity': '10'}],
            'suppliers': [
                {'key': 's1', 'type': 'registered', 'id': self.simples.pk, 'regime': 'simples_nacional', 'ibs_cbs': 'simples'},
                {'key': 's2', 'type': 'registered', 'id': self.regular.pk, 'regime': 'lucro_real', 'ibs_cbs': 'regular'},
            ],
            'prices': [
                {'product_id': self.produto.pk, 'supplier_key': 's1', 'unit_price': '10'},
                {'product_id': self.produto.pk, 'supplier_key': 's2', 'unit_price': '11'},
            ],
        }

    def test_motor_nao_inventa_credito_sem_regra(self):
        resultado = PurchaseTaxService.calcular(
            empresa=self.empresa, data_referencia=date(2025, 1, 1), produto=self.produto,
            quantidade=Decimal('2'), valor_unitario=Decimal('10'),
            frete_nao_recuperavel=0, desconto=0,
            regime_comprador='lucro_real', regime_ibs_cbs_comprador='regular',
            regime_fornecedor='lucro_real', regime_ibs_cbs_fornecedor='regular',
        )
        self.assertIsNone(resultado.regra)
        self.assertEqual(resultado.credito_total, Decimal('0'))
        self.assertEqual(resultado.custo_efetivo, Decimal('20.0000'))

    def test_tela_nova_reutiliza_produtos_e_fornecedores_da_filial(self):
        response = CotacaoCompraNovaView.as_view()(self._request('/compras/cotacoes/nova/'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Tecido Dry Fit')
        self.assertContains(response, 'Fornecedor Simples')
        self.assertContains(response, 'Adicionar fornecedor manual')
        self.assertContains(response, 'Simples Nacional Híbrido')
        self.assertContains(response, '.tema-escuro #cotacao-app .cotacao-manual')
        self.assertContains(response, 'cotacao-manual-registration')
        self.assertContains(response, '.tema-escuro #cotacao-app .cotacao-matrix td:first-child')

    def test_salvar_cotacao_resolve_usuario_do_banco_operacional(self):
        request = self.factory.post(
            '/compras/cotacoes/nova/',
            {'payload': '{}'},
        )
        request.user = self.usuario
        request.filial_ativa = self.filial
        request.session = {}
        request._messages = FallbackStorage(request)

        with (
            patch(
                'apps.compras.views.cotacao.usuario_operacional',
                return_value=self.usuario,
            ) as resolver,
            patch.object(
                CotacaoCompraService,
                'criar_e_analisar',
                return_value=type('CotacaoSalva', (), {'pk': 123})(),
            ) as criar,
        ):
            response = CotacaoCompraNovaView.as_view()(request)

        self.assertEqual(response.status_code, 302)
        resolver.assert_called_once_with(request, obrigatorio=True)
        self.assertIs(criar.call_args.kwargs['usuario'], self.usuario)

    def test_regime_hibrido_normaliza_simples_com_ibs_cbs_regular(self):
        dados = self._payload()
        dados['buyer_regime'] = 'simples_nacional_hibrido'
        dados['buyer_ibs_cbs'] = 'simples'
        dados['suppliers'][0]['regime'] = 'simples_nacional_hibrido'
        dados['suppliers'][0]['ibs_cbs'] = 'simples'
        dados['suppliers'][0]['update_registration'] = True

        cotacao = CotacaoCompraService.criar_e_analisar(
            filial=self.filial,
            usuario=self.usuario,
            dados=dados,
            pode_editar_fornecedor=True,
        )

        self.assertEqual(cotacao.regime_comprador, 'simples_nacional')
        self.assertEqual(cotacao.regime_ibs_cbs_comprador, 'regular')
        self.assertEqual(
            cotacao.empresa_snapshot['regime_informado'],
            'simples_nacional_hibrido',
        )
        participante = cotacao.fornecedores.get(fornecedor=self.simples)
        self.assertEqual(participante.supplier_tax_regime, 'simples_nacional')
        self.assertEqual(participante.supplier_tax_ibs_cbs, 'regular')
        self.assertEqual(
            participante.supplier_snapshot['supplierTaxRegimeInformado'],
            'simples_nacional_hibrido',
        )
        self.simples.refresh_from_db()
        self.assertTrue(self.simples.optante_simples)
        self.assertEqual(self.simples.regime_tributario, 'simples_nacional')
        self.assertEqual(self.simples.regime_ibs_cbs, 'regular')

    def test_recomenda_por_custo_efetivo_e_guarda_snapshot(self):
        cotacao = CotacaoCompraService.criar_e_analisar(
            filial=self.filial, usuario=self.usuario, dados=self._payload(),
        )
        vencedor = CalculoTributarioCompra.objects.get(preco__item__cotacao=cotacao, vencedor=True)
        self.assertEqual(vencedor.preco.fornecedor.fornecedor, self.regular)
        self.assertEqual(vencedor.valor_bruto, Decimal('110.0000'))
        self.assertEqual(vencedor.credito_total, Decimal('22.0000'))
        self.assertEqual(vencedor.custo_efetivo, Decimal('88.0000'))
        self.assertEqual(cotacao.custo_efetivo_total, Decimal('88.0000'))
        self.assertEqual(cotacao.economia_estimada, Decimal('0.0000'))
        self.assertEqual(cotacao.fornecedor_base_nome, 'Fornecedor Regular')
        self.assertEqual(cotacao.empresa_snapshot['regime_tributario'], 'lucro_real')
        self.assertEqual(vencedor.regra_snapshot['percentual_credito_ibs'], '12.0000')
        response = CotacaoCompraDetailView.as_view()(
            self._request(f'/compras/cotacoes/{cotacao.pk}/'), pk=cotacao.pk,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Compra otimizada por fornecedor')
        self.assertContains(response, 'Fornecedor Regular')
        self.assertContains(response, 'id="cotacao-detail"')
        self.assertContains(response, '.tema-escuro #cotacao-detail .bg-emerald-50')
        self.assertContains(response, '.tema-escuro #cotacao-detail .bg-gray-50')
        self.assertContains(response, '.tema-escuro #cotacao-detail .bg-white')

    def test_exibe_ultima_compra_efetivada_e_variacao_sem_mudar_historico(self):
        entrada_antiga = EntradaNF.objects.create(
            filial=self.filial,
            fornecedor=self.simples,
            numero_nf='100',
            serie_nf='1',
            data_emissao_nf=date(2026, 7, 10),
            data_entrada=timezone.make_aware(datetime(2026, 7, 10, 10, 0)),
            status=EntradaNF.Status.EFETIVADA,
            usuario=self.usuario,
        )
        ItemEntradaNF.objects.create(
            entrada=entrada_antiga,
            produto=self.produto,
            quantidade=Decimal('10'),
            valor_unitario=Decimal('9.00'),
            custo_unitario_total=Decimal('9.20'),
            valor_bruto=Decimal('90.00'),
            valor_total=Decimal('90.00'),
        )
        entrada_ultima = EntradaNF.objects.create(
            filial=self.filial,
            fornecedor=self.regular,
            numero_nf='200',
            serie_nf='2',
            data_emissao_nf=date(2026, 8, 20),
            data_entrada=timezone.make_aware(datetime(2026, 8, 20, 14, 0)),
            status=EntradaNF.Status.EFETIVADA,
            usuario=self.usuario,
        )
        ItemEntradaNF.objects.create(
            entrada=entrada_ultima,
            produto=self.produto,
            quantidade=Decimal('10'),
            valor_unitario=Decimal('9.10'),
            custo_unitario_total=Decimal('9.40'),
            valor_bruto=Decimal('91.00'),
            valor_total=Decimal('91.00'),
        )
        entrada_rascunho = EntradaNF.objects.create(
            filial=self.filial,
            fornecedor=self.simples,
            numero_nf='300',
            serie_nf='1',
            data_emissao_nf=date(2026, 9, 1),
            data_entrada=timezone.make_aware(datetime(2026, 9, 1, 9, 0)),
            status=EntradaNF.Status.RASCUNHO,
            usuario=self.usuario,
        )
        ItemEntradaNF.objects.create(
            entrada=entrada_rascunho,
            produto=self.produto,
            quantidade=Decimal('10'),
            valor_unitario=Decimal('15.00'),
            custo_unitario_total=Decimal('15.00'),
            valor_bruto=Decimal('150.00'),
            valor_total=Decimal('150.00'),
        )

        cotacao = CotacaoCompraService.criar_e_analisar(
            filial=self.filial, usuario=self.usuario, dados=self._payload(),
        )
        item = cotacao.itens.get()
        self.assertEqual(item.ultima_compra_snapshot['numero_nf'], '200')
        self.assertEqual(item.ultima_compra_snapshot['serie_nf'], '2')
        self.assertEqual(item.ultima_compra_snapshot['fornecedor_nome'], 'Fornecedor Regular')
        self.assertEqual(item.ultima_compra_snapshot['custo_unitario'], '9.4000')

        entrada_nova = EntradaNF.objects.create(
            filial=self.filial,
            fornecedor=self.simples,
            numero_nf='400',
            serie_nf='1',
            data_emissao_nf=date(2026, 9, 12),
            data_entrada=timezone.make_aware(datetime(2026, 9, 12, 9, 0)),
            status=EntradaNF.Status.EFETIVADA,
            usuario=self.usuario,
        )
        ItemEntradaNF.objects.create(
            entrada=entrada_nova,
            produto=self.produto,
            quantidade=Decimal('10'),
            valor_unitario=Decimal('20.00'),
            custo_unitario_total=Decimal('20.00'),
            valor_bruto=Decimal('200.00'),
            valor_total=Decimal('200.00'),
        )
        response = CotacaoCompraDetailView.as_view()(
            self._request(f'/compras/cotacoes/{cotacao.pk}/'), pk=cotacao.pk,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Última compra efetivada')
        self.assertContains(response, 'NF 200/2')
        self.assertContains(response, 'Custo anterior')
        self.assertNotContains(response, 'NF 400/1')

    def test_fornecedor_manual_pode_ficar_apenas_na_cotacao(self):
        dados = self._payload()
        dados['suppliers'][1] = {
            'key': 'manual-1', 'type': 'manual', 'name': 'Joao Tecidos',
            'cnpj': '', 'regime': 'lucro_real', 'ibs_cbs': 'regular',
            'save_registration': False,
        }
        dados['prices'][1]['supplier_key'] = 'manual-1'
        cotacao = CotacaoCompraService.criar_e_analisar(
            filial=self.filial, usuario=self.usuario, dados=dados,
        )
        manual = CotacaoCompraFornecedor.objects.get(cotacao=cotacao, manual_supplier=True)
        self.assertIsNone(manual.fornecedor)
        self.assertEqual(manual.supplier_name, 'Joao Tecidos')
        self.assertFalse(Fornecedor.objects.filter(razao_social='Joao Tecidos').exists())
        self.assertTrue(manual.supplier_snapshot['manualSupplier'])

    def test_economia_compara_com_melhor_fornecedor_unico(self):
        produto_2 = Produto.objects.create(
            filial=self.filial, unidade_medida=self.produto.unidade_medida,
            descricao='Linha Poliester', codigo='LIN-01', ncm='55081000',
            preco_custo=0, preco_venda=15,
        )
        ProdutoFilial.objects.create(produto=produto_2, filial=self.filial)
        dados = self._payload()
        dados['products'].append({'id': produto_2.pk, 'quantity': '10'})
        dados['prices'].extend([
            {'product_id': produto_2.pk, 'supplier_key': 's1', 'unit_price': '8'},
            {'product_id': produto_2.pk, 'supplier_key': 's2', 'unit_price': '12'},
        ])
        cotacao = CotacaoCompraService.criar_e_analisar(
            filial=self.filial, usuario=self.usuario, dados=dados,
        )
        self.assertEqual(cotacao.custo_efetivo_total, Decimal('168.0000'))
        self.assertEqual(cotacao.fornecedor_base_nome, 'Fornecedor Simples')
        self.assertEqual(cotacao.economia_estimada, Decimal('12.0000'))

    def test_fornecedor_manual_pode_ser_salvo_no_cadastro_existente(self):
        dados = self._payload()
        dados['suppliers'][1] = {
            'key': 'manual-1', 'type': 'manual', 'name': 'Novo Fornecedor',
            'cnpj': '33333333000191', 'regime': 'lucro_presumido', 'ibs_cbs': 'regular',
            'save_registration': True,
        }
        dados['prices'][1]['supplier_key'] = 'manual-1'
        cotacao = CotacaoCompraService.criar_e_analisar(
            filial=self.filial, usuario=self.usuario, dados=dados,
            pode_criar_fornecedor=True,
        )
        participante = CotacaoCompraFornecedor.objects.get(cotacao=cotacao, supplier_name='Novo Fornecedor')
        self.assertIsNotNone(participante.fornecedor)
        self.assertTrue(participante.salvo_no_cadastro)
        self.assertTrue(FornecedorFilial.objects.filter(
            fornecedor=participante.fornecedor, filial=self.filial,
        ).exists())
