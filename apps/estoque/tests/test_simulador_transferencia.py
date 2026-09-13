"""Fase 15: simulador de antes/depois de uma transferência manual."""
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import Deposito, Estoque
from apps.estoque.services.simulador_transferencia import simular_transferencia
from apps.pdv.models import ItemVendaPDV, VendaPDV
from apps.produtos.models import Produto, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial


class SimuladorBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social="Rede Simulador LTDA", nome_fantasia="Rede Simulador",
            cnpj="92345678000191", regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial_a = Filial.objects.create(
            empresa=cls.empresa, razao_social="Filial A", nome_fantasia="Filial A",
            cnpj="92345678000192", uf="RN", is_matriz=True,
        )
        cls.filial_b = Filial.objects.create(
            empresa=cls.empresa, razao_social="Filial B", nome_fantasia="Filial B",
            cnpj="92345678000273", uf="RN",
        )
        cls.perfil = PerfilAcesso.objects.create(empresa=cls.empresa, nome="Admin", is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email="simulador@inoovated.com", nome="Usuario Simulador", password="teste1234",
            empresa=cls.empresa, filial=cls.filial_a, perfil=cls.perfil,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla="UN", descricao="Unidade", tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial_a)
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial_b)
        cls.produto = Produto.objects.create(
            filial=cls.filial_a, unidade_medida=cls.unidade, descricao="Polpa Morango",
            codigo="PM01", ncm="20089900", estoque_minimo=Decimal("0"), preco_custo=Decimal("4"),
        )
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.filial_a)
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.filial_b)
        deposito_a = Deposito.objects.create(filial=cls.filial_a, nome="Geral A", is_padrao=True)
        deposito_b = Deposito.objects.create(filial=cls.filial_b, nome="Geral B", is_padrao=True)
        Estoque.objects.create(
            produto=cls.produto, filial=cls.filial_a, deposito=deposito_a,
            quantidade_atual=500, quantidade_disponivel=500,
        )
        Estoque.objects.create(
            produto=cls.produto, filial=cls.filial_b, deposito=deposito_b,
            quantidade_atual=20, quantidade_disponivel=20,
        )
        # Venda so' na Filial B: 10/dia (300 em 30 dias).
        venda = VendaPDV.objects.create(
            filial=cls.filial_b, numero_venda=1, usuario=cls.usuario,
            data_venda=timezone.now(), status="finalizada", valor_total=Decimal("2400"),
        )
        ItemVendaPDV.objects.create(
            venda_pdv=venda, produto=cls.produto, numero_item=1, quantidade=Decimal("300"),
            unidade_medida="UN", valor_unitario=8, valor_total=Decimal("2400"),
        )


class SimularTransferenciaServiceTests(SimuladorBase):
    def test_calcula_saldo_e_cobertura_antes_e_depois(self):
        resultado = simular_transferencia(
            empresa=self.empresa, produto_id=self.produto.pk,
            origem_id=self.filial_a.pk, destino_id=self.filial_b.pk,
            quantidade=Decimal("150"), dias_analise=30, dias_cobertura=14,
        )

        self.assertEqual(resultado["origem_saldo_antes"], Decimal("500"))
        self.assertEqual(resultado["origem_saldo_depois"], Decimal("350"))
        self.assertEqual(resultado["destino_saldo_antes"], Decimal("20"))
        self.assertEqual(resultado["destino_saldo_depois"], Decimal("170"))
        # Demanda destino = 300/30 = 10/dia -> cobertura antes 2.0, depois 17.0.
        self.assertEqual(resultado["destino_cobertura_antes"], Decimal("2.0"))
        self.assertEqual(resultado["destino_cobertura_depois"], Decimal("17.0"))
        self.assertTrue(resultado["reduz_risco_ruptura"])
        self.assertEqual(resultado["capital_redistribuido"], Decimal("600.00"))
        self.assertFalse(resultado["excede_saldo_origem"])

    def test_quantidade_maior_que_saldo_origem_sinaliza_excesso(self):
        resultado = simular_transferencia(
            empresa=self.empresa, produto_id=self.produto.pk,
            origem_id=self.filial_a.pk, destino_id=self.filial_b.pk,
            quantidade=Decimal("999"), dias_analise=30, dias_cobertura=14,
        )
        self.assertTrue(resultado["excede_saldo_origem"])

    def test_produto_nao_vinculado_a_filial_retorna_none(self):
        outra_filial = Filial.objects.create(
            empresa=self.empresa, razao_social="Filial C", nome_fantasia="Filial C",
            cnpj="92345678000354", uf="RN",
        )
        resultado = simular_transferencia(
            empresa=self.empresa, produto_id=self.produto.pk,
            origem_id=self.filial_a.pk, destino_id=outra_filial.pk,
            quantidade=Decimal("10"),
        )
        self.assertIsNone(resultado)


class SimuladorTransferenciaViewTests(SimuladorBase):
    def setUp(self):
        self.client = Client()
        self.client.force_login(self.usuario)

    def test_abre_sem_parametros(self):
        response = self.client.get(reverse("estoque:simulador-transferencia"))
        self.assertEqual(response.status_code, 200)

    def test_simula_com_parametros_completos(self):
        response = self.client.get(reverse("estoque:simulador-transferencia"), {
            "produto": self.produto.pk, "origem": self.filial_a.pk,
            "destino": self.filial_b.pk, "quantidade": "150",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Aplicar transferência")
        self.assertContains(response, "Capital redistribuído")

    def test_mesma_filial_como_origem_e_destino_da_erro(self):
        response = self.client.get(reverse("estoque:simulador-transferencia"), {
            "produto": self.produto.pk, "origem": self.filial_a.pk,
            "destino": self.filial_a.pk, "quantidade": "10",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "filiais diferentes")
