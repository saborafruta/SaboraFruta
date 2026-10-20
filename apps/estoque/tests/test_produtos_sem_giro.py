from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.models import Cliente, ClienteFilial
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import Deposito, Estoque
from apps.estoque.services.produtos_sem_giro_service import ProdutosSemGiroService
from apps.pdv.models import ItemVendaPDV, VendaPDV
from apps.produtos.models import Produto, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial
from apps.vendas.models import ItemPedidoVenda, PedidoVenda


class ProdutosSemGiroTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Empresa Giro LTDA', nome_fantasia='Empresa Giro',
            cnpj='71345678000191', regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Filial Giro', nome_fantasia='Matriz Giro',
            cnpj='71345678000192', uf='RN', is_matriz=True,
        )
        cls.perfil = PerfilAcesso.objects.create(empresa=cls.empresa, nome='Admin Giro', is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email='giro@inoovated.com', nome='Usuario Giro', password='teste1234',
            empresa=cls.empresa, filial=cls.filial, perfil=cls.perfil,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='UN', descricao='Unidade', tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        cls.deposito = Deposito.objects.create(filial=cls.filial, nome='Geral', is_padrao=True)
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, tipo_pessoa='J', razao_social='Cliente Giro',
            cpf_cnpj='71345678000193', uf='RN',
        )
        ClienteFilial.objects.create(cliente=cls.cliente, filial=cls.filial)

    def setUp(self):
        self.client.force_login(self.usuario)
        session = self.client.session
        session['filial_ativa_id'] = self.filial.pk
        session.save()

    def criar_produto(self, descricao, *, ultima_saida=None):
        produto = Produto.objects.create(
            filial=self.filial, unidade_medida=self.unidade, descricao=descricao,
            ncm='20089900', preco_venda=Decimal('10.00'), ativo=True,
        )
        ProdutoFilial.objects.create(produto=produto, filial=self.filial)
        Estoque.objects.create(
            produto=produto, filial=self.filial, deposito=self.deposito,
            quantidade_atual=Decimal('10'), quantidade_disponivel=Decimal('10'),
            custo_medio=Decimal('4.00'), ultima_saida=ultima_saida,
        )
        return produto

    def criar_venda_pdv(self, produto, dias, *, status='finalizada', bonificacao=False):
        venda = VendaPDV.objects.create(
            filial=self.filial,
            numero_venda=VendaPDV.objects.count() + 1,
            usuario=self.usuario,
            data_venda=timezone.now() - timedelta(days=dias),
            status=status,
            bonificacao=bonificacao,
            valor_total=Decimal('10.00'),
        )
        ItemVendaPDV.objects.create(
            venda_pdv=venda, produto=produto, numero_item=1,
            quantidade=Decimal('1'), unidade_medida='UN',
            valor_unitario=Decimal('10.00'), valor_total=Decimal('10.00'),
        )

    def criar_venda_b2b(self, produto, dias, *, status=PedidoVenda.Status.FATURADO):
        pedido = PedidoVenda.objects.create(
            filial=self.filial,
            numero_pedido=f'PV-{PedidoVenda.objects.count() + 1:07d}',
            cliente=self.cliente,
            usuario=self.usuario,
            tipo=PedidoVenda.Tipo.PEDIDO,
            status=status,
            data_emissao=timezone.now() - timedelta(days=dias),
            valor_total=Decimal('10.00'),
        )
        ItemPedidoVenda.objects.create(
            pedido=pedido, produto=produto, numero_item=1,
            quantidade=Decimal('1'), quantidade_solicitada=Decimal('1'),
            valor_unitario=Decimal('10.00'), valor_bruto=Decimal('10.00'),
            valor_total=Decimal('10.00'),
        )

    def test_classifica_vendas_pdv_b2b_e_nunca_vendido(self):
        nunca = self.criar_produto('Produto nunca vendido', ultima_saida=timezone.now())
        sessenta = self.criar_produto('Produto 75 dias')
        noventa = self.criar_produto('Produto 100 dias')
        recente = self.criar_produto('Produto recente')
        self.criar_venda_pdv(sessenta, 75)
        self.criar_venda_b2b(noventa, 100)
        self.criar_venda_pdv(recente, 10)

        itens = ProdutosSemGiroService.analisar(self.filial)
        por_id = {item['produto_id']: item for item in itens}

        self.assertEqual(por_id[nunca.pk]['faixa'], 'nunca')
        self.assertEqual(por_id[sessenta.pk]['faixa'], '60_89')
        self.assertEqual(por_id[noventa.pk]['faixa'], '90_mais')
        self.assertNotIn(recente.pk, por_id)

    def test_saida_de_estoque_e_venda_nao_concluida_nao_reiniciam_giro(self):
        produto = self.criar_produto('Produto apenas movimentado', ultima_saida=timezone.now())
        self.criar_venda_pdv(produto, 5, status='cancelada')
        self.criar_venda_pdv(produto, 3, bonificacao=True)
        self.criar_venda_b2b(produto, 2, status=PedidoVenda.Status.CONFIRMADO)

        item = ProdutosSemGiroService.analisar(self.filial)[0]

        self.assertEqual(item['produto_id'], produto.pk)
        self.assertEqual(item['faixa'], 'nunca')

    def test_relatorio_exibe_faixas_e_exporta_csv(self):
        self.criar_produto('Produto relatório')

        response = self.client.get(reverse('estoque:produtos-sem-giro'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Produtos sem giro comercial')
        self.assertContains(response, 'Produto relatório')
        self.assertContains(response, 'Nunca vendido')

        csv_response = self.client.get(reverse('estoque:produtos-sem-giro'), {'export': 'csv'})
        self.assertEqual(csv_response.status_code, 200)
        self.assertIn('text/csv', csv_response['Content-Type'])
        self.assertIn('Produto relatório', csv_response.content.decode('utf-8-sig'))

    def test_dashboard_exibe_previa_e_link_do_relatorio(self):
        self.criar_produto('Produto parado no dashboard')

        response = self.client.get(reverse('core:dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Produtos sem giro')
        self.assertContains(response, 'Produto parado no dashboard')
        self.assertContains(response, reverse('estoque:produtos-sem-giro'))
        self.assertContains(response, f'{reverse("produtos:produto-list")}?status=ativo')
