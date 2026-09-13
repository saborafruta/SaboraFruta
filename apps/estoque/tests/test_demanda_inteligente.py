"""Fase 16: demanda ponderada por periodo, indice por dia da semana e sazonalidade."""
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import ConfiguracaoDemandaPonderada
from apps.estoque.services.demanda_inteligente import (
    calcular_demanda_ponderada, calcular_indice_dia_semana, calcular_sazonalidade, carregar_pesos,
)
from apps.pdv.models import ItemVendaPDV, VendaPDV
from apps.produtos.models import Produto, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial


class DemandaInteligenteBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social="Rede Demanda LTDA", nome_fantasia="Rede Demanda",
            cnpj="93445678000191", regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social="Loja A", nome_fantasia="Loja A",
            cnpj="93445678000192", uf="RN", is_matriz=True,
        )
        cls.perfil = PerfilAcesso.objects.create(empresa=cls.empresa, nome="Admin", is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email="demanda@inoovated.com", nome="Usuario Demanda", password="teste1234",
            empresa=cls.empresa, filial=cls.filial, perfil=cls.perfil,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla="UN", descricao="Unidade", tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        cls.produto = Produto.objects.create(
            filial=cls.filial, unidade_medida=cls.unidade, descricao="Produto Demanda",
            ncm="20089900", preco_venda=Decimal("10"),
        )
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.filial)

    def _vender(self, quantidade, quando, numero):
        venda = VendaPDV.objects.create(
            filial=self.filial, numero_venda=numero, usuario=self.usuario,
            data_venda=quando, status="finalizada", valor_total=quantidade * 10,
        )
        ItemVendaPDV.objects.create(
            venda_pdv=venda, produto=self.produto, numero_item=1, quantidade=quantidade,
            unidade_medida="UN", valor_unitario=10, valor_total=quantidade * 10,
        )


class CarregarPesosTests(DemandaInteligenteBase):
    def test_sem_configuracao_usa_padrao(self):
        pesos = carregar_pesos(empresa=self.empresa)
        self.assertEqual(pesos, {7: 0, 15: 0, 30: 50, 60: 30, 90: 20})

    def test_configuracao_customizada_e_usada(self):
        ConfiguracaoDemandaPonderada.objects.create(
            empresa=self.empresa, peso_7_dias=100, peso_15_dias=0,
            peso_30_dias=0, peso_60_dias=0, peso_90_dias=0,
        )
        pesos = carregar_pesos(empresa=self.empresa)
        self.assertEqual(pesos[7], 100)


class CalcularDemandaPonderadaTests(DemandaInteligenteBase):
    def test_media_ponderada_combina_os_periodos_com_peso(self):
        agora = timezone.now()
        # 30 dias: 300 unidades (10/dia). 60 dias: mais 300 unidades entre
        # dia 31 e 60 (10/dia tambem) -- media do periodo de 60 dias fica
        # em 600/60=10/dia. Com pesos 30d=50%, 60d=50%, a ponderada da 10.
        self._vender(300, agora - timezone.timedelta(days=5), numero=1)
        self._vender(300, agora - timezone.timedelta(days=45), numero=2)

        resultado = calcular_demanda_ponderada(
            produto_id=self.produto.pk, filial_id=self.filial.pk,
            pesos={30: 50, 60: 50}, agora=agora,
        )

        self.assertEqual(resultado["demanda_ponderada"], Decimal("10.000"))
        self.assertEqual(len(resultado["detalhamento"]), 2)

    def test_periodos_com_peso_zero_sao_ignorados(self):
        agora = timezone.now()
        self._vender(700, agora - timezone.timedelta(days=1), numero=1)

        resultado = calcular_demanda_ponderada(
            produto_id=self.produto.pk, filial_id=self.filial.pk,
            pesos={7: 0, 30: 100}, agora=agora,
        )

        self.assertEqual(len(resultado["detalhamento"]), 1)
        self.assertEqual(resultado["detalhamento"][0]["dias"], 30)


class IndiceDiaSemanaTests(DemandaInteligenteBase):
    def test_dia_com_mais_venda_tem_indice_maior_que_um(self):
        agora = timezone.now()
        # Concentra a venda numa segunda-feira (weekday()==0) desta semana.
        data_segunda = agora - timezone.timedelta(days=agora.weekday())
        self._vender(100, data_segunda, numero=1)

        resultado = calcular_indice_dia_semana(produto_id=self.produto.pk, filial_id=self.filial.pk, agora=agora)

        indice_segunda = next(item for item in resultado if item["dia"] == 0)
        self.assertGreater(indice_segunda["indice"], Decimal("1"))


class SazonalidadeTests(DemandaInteligenteBase):
    def test_sem_venda_no_ano_anterior_retorna_indice_none(self):
        agora = timezone.now()
        self._vender(50, agora - timezone.timedelta(days=5), numero=1)

        resultado = calcular_sazonalidade(produto_id=self.produto.pk, filial_id=self.filial.pk, agora=agora)

        self.assertIsNone(resultado["indice"])
        self.assertEqual(resultado["vendido_atual"], Decimal("50"))

    def test_compara_com_mesmo_periodo_ano_anterior(self):
        agora = timezone.now()
        self._vender(100, agora - timezone.timedelta(days=5), numero=1)
        self._vender(50, agora - timezone.timedelta(days=365 + 5), numero=2)

        resultado = calcular_sazonalidade(produto_id=self.produto.pk, filial_id=self.filial.pk, dias_analise=30, agora=agora)

        self.assertEqual(resultado["indice"], Decimal("2.00"))


class DemandaInteligenteViewTests(DemandaInteligenteBase):
    def setUp(self):
        self.client = Client()
        self.client.force_login(self.usuario)

    def test_analise_abre_com_parametros(self):
        response = self.client.get(reverse("estoque:demanda-inteligente"), {
            "produto": self.produto.pk, "filial": self.filial.pk,
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Demanda ponderada")

    def test_config_get_cria_linha_e_post_atualiza(self):
        response = self.client.get(reverse("estoque:demanda-inteligente-config"))
        self.assertEqual(response.status_code, 200)

        response = self.client.post(reverse("estoque:demanda-inteligente-config"), {
            "peso_7_dias": 100, "peso_15_dias": 0, "peso_30_dias": 0,
            "peso_60_dias": 0, "peso_90_dias": 0,
        })
        self.assertRedirects(response, reverse("estoque:demanda-inteligente-config"))
        config = ConfiguracaoDemandaPonderada.objects.get(empresa=self.empresa)
        self.assertEqual(config.peso_7_dias, 100)
