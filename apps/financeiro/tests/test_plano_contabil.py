from io import StringIO

from django.core.management import call_command
from django.test import RequestFactory, TestCase

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.financeiro.models import PlanoContabil
from apps.financeiro.views.plano_contabil import PlanoContabilListView


class PlanoContabilTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social="EUREKA",
            nome_fantasia="Eureka",
            cnpj="50649395000126",
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa,
            razao_social="Eureka",
            nome_fantasia="Eureka",
            cnpj="50649395000127",
            uf="RN",
            ativo=True,
        )
        cls.perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa,
            nome="Administrador",
            is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email="plano-contabil@inoovated.com",
            nome="Administrador Eureka",
            password="teste1234",
            empresa=cls.empresa,
            filial=cls.filial,
            perfil=cls.perfil,
        )

    def importar(self):
        saida = StringIO()
        call_command(
            "importar_plano_contabil",
            empresa_cnpj=self.empresa.cnpj,
            stdout=saida,
        )
        return saida.getvalue()

    def test_importa_pdf_completo_e_preserva_hierarquia(self):
        self.importar()
        contas = PlanoContabil.objects.filter(empresa=self.empresa)

        self.assertEqual(contas.count(), 646)
        self.assertEqual(contas.filter(tipo_conta="S").count(), 196)
        self.assertEqual(contas.filter(tipo_conta="A").count(), 450)
        self.assertEqual(contas.filter(ativo=False).count(), 18)
        self.assertEqual(contas.filter(nivel=1).count(), 5)
        self.assertFalse(contas.filter(nivel__gt=1, conta_pai__isnull=True).exists())
        self.assertEqual(
            contas.get(classificacao="1110100001").conta_pai.classificacao,
            "11101",
        )

    def test_importacao_e_idempotente(self):
        self.importar()
        segunda_saida = self.importar()

        self.assertEqual(PlanoContabil.objects.filter(empresa=self.empresa).count(), 646)
        self.assertIn("0 criadas, 646 atualizadas", segunda_saida)

    def test_tela_filtra_grupo_tipo_status_e_busca(self):
        self.importar()
        request = RequestFactory().get(
            "/financeiro/plano-contabil/",
            {"grupo": "1", "tipo": "A", "status": "ativo", "q": "CAIXA GERAL"},
        )
        request.user = self.usuario
        request.filial_ativa = self.filial
        request.session = {}

        response = PlanoContabilListView.as_view()(request)
        content = response.content.decode("utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertIn("CAIXA GERAL", content)
        self.assertNotIn("BANCO DO BRASIL", content)
