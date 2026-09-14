"""Alertas automáticos de equalização -- condição, não evento (ver docstring do service)."""
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.core.models import Empresa, Filial, Notificacao, PerfilAcesso, Usuario
from apps.estoque.models import Deposito, Estoque
from apps.estoque.services.alertas_equalizacao import REFERENCIA_TIPO, sincronizar
from apps.pdv.models import ItemVendaPDV, VendaPDV
from apps.produtos.models import Produto, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial


class AlertasEqualizacaoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social="Rede Alerta LTDA", nome_fantasia="Rede Alerta",
            cnpj="98945678000191", regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social="Loja A", nome_fantasia="Loja A",
            cnpj="98945678000192", uf="RN", is_matriz=True,
        )
        cls.perfil = PerfilAcesso.objects.create(empresa=cls.empresa, nome="Admin", is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email="alerta@inoovated.com", nome="Usuario Alerta", password="teste1234",
            empresa=cls.empresa, filial=cls.filial, perfil=cls.perfil,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla="UN", descricao="Unidade", tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        cls.deposito = Deposito.objects.create(filial=cls.filial, nome="Geral", is_padrao=True)

    def _produto(self, nome, estoque_minimo=Decimal("0")):
        produto = Produto.objects.create(
            filial=self.filial, unidade_medida=self.unidade, descricao=nome,
            ncm="20089900", preco_venda=Decimal("10"), estoque_minimo=estoque_minimo,
        )
        ProdutoFilial.objects.create(produto=produto, filial=self.filial)
        return produto

    def _saldo(self, produto, quantidade):
        Estoque.objects.create(
            produto=produto, filial=self.filial, deposito=self.deposito,
            quantidade_atual=quantidade, quantidade_disponivel=quantidade,
        )

    def _vender(self, produto, quantidade, numero):
        venda = VendaPDV.objects.create(
            filial=self.filial, numero_venda=numero, usuario=self.usuario,
            data_venda=timezone.now(), status="finalizada", valor_total=quantidade * 10,
        )
        ItemVendaPDV.objects.create(
            venda_pdv=venda, produto=produto, numero_item=1, quantidade=quantidade,
            unidade_medida="UN", valor_unitario=10, valor_total=quantidade * 10,
        )

    def test_produto_zerado_gera_alerta_de_ruptura(self):
        produto = self._produto("Produto Ruptura")
        self._saldo(produto, Decimal("0"))
        self._vender(produto, Decimal("30"), numero=1)

        resultado = sincronizar(empresa=self.empresa)

        self.assertEqual(resultado["criados"], 1)
        notificacao = Notificacao.objects.get(filial=self.filial, referencia_tipo=REFERENCIA_TIPO)
        self.assertIn("🔴", notificacao.titulo)
        self.assertTrue(notificacao.ativa)

    def test_produto_parado_com_saldo_gera_alerta(self):
        produto = self._produto("Produto Parado")
        self._saldo(produto, Decimal("50"))

        sincronizar(empresa=self.empresa)

        notificacao = Notificacao.objects.get(
            filial=self.filial, referencia_tipo=REFERENCIA_TIPO, referencia_id__startswith="parado:",
        )
        self.assertIn("⚫", notificacao.titulo)

    def test_condicao_resolvida_desativa_o_alerta(self):
        produto = self._produto("Produto Resolvido")
        self._saldo(produto, Decimal("0"))
        self._vender(produto, Decimal("30"), numero=1)
        sincronizar(empresa=self.empresa)
        notificacao = Notificacao.objects.get(filial=self.filial, referencia_tipo=REFERENCIA_TIPO)
        self.assertTrue(notificacao.ativa)

        Estoque.objects.filter(produto=produto, filial=self.filial).update(
            quantidade_atual=Decimal("1000"), quantidade_disponivel=Decimal("1000"),
        )

        sincronizar(empresa=self.empresa)

        notificacao.refresh_from_db()
        self.assertFalse(notificacao.ativa)

    def test_rodar_duas_vezes_nao_duplica(self):
        produto = self._produto("Produto Duplicidade")
        self._saldo(produto, Decimal("0"))
        self._vender(produto, Decimal("30"), numero=1)

        sincronizar(empresa=self.empresa)
        sincronizar(empresa=self.empresa)

        self.assertEqual(
            Notificacao.objects.filter(filial=self.filial, referencia_tipo=REFERENCIA_TIPO).count(), 1,
        )
