"""Fase 31: API REST de equalização de estoque (sessão + RBAC, ver apps/estoque/api/)."""
from decimal import Decimal

from django.urls import reverse
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.models import Permissao
from apps.estoque.models import Deposito, Estoque, SolicitacaoTransferencia
from apps.pdv.models import ItemVendaPDV, VendaPDV
from apps.produtos.models import Produto, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial


class ApiEqualizacaoBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social="Rede API LTDA", nome_fantasia="Rede API",
            cnpj="91145678000191", regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.loja_a = Filial.objects.create(
            empresa=cls.empresa, razao_social="Loja A", nome_fantasia="Loja A",
            cnpj="91145678000192", uf="RN", is_matriz=True,
        )
        cls.loja_b = Filial.objects.create(
            empresa=cls.empresa, razao_social="Loja B", nome_fantasia="Loja B",
            cnpj="91145678000273", uf="RN",
        )
        cls.perfil = PerfilAcesso.objects.create(empresa=cls.empresa, nome="Admin", is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email="api@inoovated.com", nome="Usuario API", password="teste1234",
            empresa=cls.empresa, filial=cls.loja_a, perfil=cls.perfil,
        )
        cls.perfil_sem_permissao = PerfilAcesso.objects.create(empresa=cls.empresa, nome="Sem Permissao", is_admin=False)
        Permissao.objects.create(perfil=cls.perfil_sem_permissao, modulo="estoque")  # tudo False por padrao
        cls.usuario_sem_permissao = Usuario.objects.create_user(
            email="sempermissao@inoovated.com", nome="Usuario Sem Permissao", password="teste1234",
            empresa=cls.empresa, filial=cls.loja_a, perfil=cls.perfil_sem_permissao,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla="UN", descricao="Unidade", tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.loja_a)
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.loja_b)
        cls.produto = Produto.objects.create(
            filial=cls.loja_a, unidade_medida=cls.unidade, descricao="Produto API",
            ncm="20089900", preco_venda=Decimal("10"), preco_custo=Decimal("4"),
        )
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.loja_a)
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.loja_b)
        deposito_a = Deposito.objects.create(filial=cls.loja_a, nome="Geral A", is_padrao=True)
        deposito_b = Deposito.objects.create(filial=cls.loja_b, nome="Geral B", is_padrao=True)
        Estoque.objects.create(
            produto=cls.produto, filial=cls.loja_a, deposito=deposito_a,
            quantidade_atual=500, quantidade_disponivel=500,
        )
        Estoque.objects.create(
            produto=cls.produto, filial=cls.loja_b, deposito=deposito_b,
            quantidade_atual=0, quantidade_disponivel=0,
        )
        venda = VendaPDV.objects.create(
            filial=cls.loja_b, numero_venda=1, usuario=cls.usuario,
            data_venda=timezone.now(), status="finalizada", valor_total=Decimal("3000"),
        )
        ItemVendaPDV.objects.create(
            venda_pdv=venda, produto=cls.produto, numero_item=1, quantidade=Decimal("300"),
            unidade_medida="UN", valor_unitario=10, valor_total=Decimal("3000"),
        )

    def setUp(self):
        self.client = APIClient()


class ApiAutenticacaoTests(ApiEqualizacaoBase):
    def test_sem_login_retorna_401_ou_403(self):
        response = self.client.get(reverse("equalizacao_api:status"))
        self.assertIn(response.status_code, (401, 403))

    def test_sem_permissao_de_estoque_retorna_403(self):
        self.client.force_login(self.usuario_sem_permissao)
        response = self.client.get(reverse("equalizacao_api:status"))
        self.assertEqual(response.status_code, 403)


class ApiLeituraTests(ApiEqualizacaoBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.usuario)

    def test_status(self):
        response = self.client.get(reverse("equalizacao_api:status"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total_sugestoes"], 1)

    def test_recomendacoes_lista_a_sugestao(self):
        response = self.client.get(reverse("equalizacao_api:recomendacoes"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        item = response.data["results"][0]
        self.assertEqual(item["produto"]["descricao"], "Produto API")
        self.assertEqual(item["destino"]["nome"], "Loja B")

    def test_dashboard(self):
        response = self.client.get(reverse("equalizacao_api:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("estoque_total", response.data)

    def test_indicadores(self):
        response = self.client.get(reverse("equalizacao_api:indicadores"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("atuais", response.data)
        self.assertIn("historico", response.data)

    def test_historico_vazio_sem_snapshot_gerado(self):
        response = self.client.get(reverse("equalizacao_api:historico"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 0)

    def test_analisar_post_com_filtros(self):
        response = self.client.post(reverse("equalizacao_api:analisar"), {
            "dias_analise": 30, "dias_cobertura": 14, "destino": self.loja_b.pk,
        }, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total_sugestoes"], 1)

    def test_simular(self):
        response = self.client.post(reverse("equalizacao_api:simular"), {
            "produto_id": self.produto.pk, "origem_id": self.loja_a.pk,
            "destino_id": self.loja_b.pk, "quantidade": "50",
        }, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Decimal(response.data["origem_saldo_depois"]), Decimal("450"))


class ApiTransferirTests(ApiEqualizacaoBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.usuario)

    def test_transferir_dentro_da_alcada_executa_direto(self):
        response = self.client.post(reverse("equalizacao_api:transferir"), {
            "produto_id": self.produto.pk, "destino_id": self.loja_b.pk, "quantidade": "50",
            "motivo": "Reforço via API",
        }, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data["executada"])
        estoque_b = Estoque.objects.get(produto=self.produto, filial=self.loja_b)
        self.assertEqual(estoque_b.quantidade_atual, Decimal("50"))

    def test_transferir_acima_da_alcada_cria_solicitacao(self):
        self.perfil.alcada_transferencia = Decimal("10")
        self.perfil.is_admin = False
        self.perfil.save()
        # Precisa de permissao 'criar' explicita ja' que nao e' mais admin.
        Permissao.objects.filter(perfil=self.perfil).delete()
        Permissao.objects.create(perfil=self.perfil, modulo="estoque", pode_ver=True, pode_criar=True, pode_aprovar=True)

        response = self.client.post(reverse("equalizacao_api:transferir"), {
            "produto_id": self.produto.pk, "destino_id": self.loja_b.pk, "quantidade": "50",
            "motivo": "Reforço via API",
        }, format="json")

        self.assertEqual(response.status_code, 202)
        self.assertFalse(response.data["executada"])
        self.assertEqual(response.data["solicitacao"]["status"], "pendente")
        self.assertEqual(SolicitacaoTransferencia.objects.count(), 1)
        estoque_b = Estoque.objects.get(produto=self.produto, filial=self.loja_b)
        self.assertEqual(estoque_b.quantidade_atual, Decimal("0"))


class ApiAprovarRejeitarTests(ApiEqualizacaoBase):
    def setUp(self):
        super().setUp()
        self.gerente_perfil = PerfilAcesso.objects.create(empresa=self.empresa, nome="Gerente", is_admin=True)
        self.gerente = Usuario.objects.create_user(
            email="gerenteapi@inoovated.com", nome="Gerente API", password="teste1234",
            empresa=self.empresa, filial=self.loja_a, perfil=self.gerente_perfil,
        )
        from apps.estoque.services.aprovacao_transferencia import solicitar_transferencia
        self.solicitacao = solicitar_transferencia(
            produto=self.produto, filial_origem=self.loja_a, filial_destino=self.loja_b,
            quantidade=Decimal("50"), motivo="Via teste", solicitante=self.usuario,
        )
        self.client.force_login(self.gerente)

    def test_aprovar(self):
        response = self.client.post(reverse("equalizacao_api:aprovar"), {
            "solicitacao_id": self.solicitacao.pk, "observacao": "Ok",
        }, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "aprovada")

    def test_rejeitar(self):
        response = self.client.post(reverse("equalizacao_api:rejeitar"), {
            "solicitacao_id": self.solicitacao.pk, "observacao": "Sem necessidade",
        }, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "rejeitada")

    def test_rejeitar_sem_observacao_falha(self):
        response = self.client.post(reverse("equalizacao_api:rejeitar"), {
            "solicitacao_id": self.solicitacao.pk,
        }, format="json")
        self.assertEqual(response.status_code, 400)
