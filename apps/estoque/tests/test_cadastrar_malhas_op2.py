from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.management.commands.cadastrar_malhas_op2 import catalogo_malhas
from apps.produtos.models import Produto


class CadastrarMalhasOp2Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao Malha LTDA', nome_fantasia='Confeccao Malha',
            cnpj='58345678000191', regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Filial Malha', nome_fantasia='Filial Malha',
            cnpj='58345678000192', uf='RN',
        )
        perfil = PerfilAcesso.objects.create(empresa=cls.empresa, nome='Admin', is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email='malha@inoovated.com', nome='Usuario Malha', password='teste1234',
            empresa=cls.empresa, filial=cls.filial, perfil=perfil,
        )

    def _rodar(self, *extras):
        saida = StringIO()
        call_command(
            'cadastrar_malhas_op2', filial_id=self.filial.pk, stdout=saida, **dict(extras),
        )
        return saida.getvalue()

    def test_catalogo_malhas_nao_esta_vazio_e_nao_tem_na_nem_outro(self):
        malhas = catalogo_malhas()
        self.assertGreater(len(malhas), 0)
        self.assertNotIn('N/A', malhas)
        self.assertNotIn('OUTRO', malhas)
        self.assertIn('DRY', malhas)

    def test_simulacao_nao_grava_nada(self):
        saida = self._rodar()

        self.assertIn('SIMULAÇÃO', saida)
        self.assertEqual(Produto.objects.filter(filial=self.filial).count(), 0)

    def test_confirmar_cria_produtos_sem_estoque(self):
        self._rodar(('confirmar', True))

        total = len(catalogo_malhas())
        self.assertEqual(Produto.objects.filter(filial=self.filial).count(), total)
        dry = Produto.objects.get(filial=self.filial, descricao='DRY')
        self.assertEqual(dry.unidade_medida.sigla, 'KG')
        self.assertTrue(dry.rascunho_comercial)

    def test_produto_ja_existente_e_pulado(self):
        unidade = None
        from apps.produtos.models import UnidadeMedida
        unidade = UnidadeMedida.objects.create(
            empresa=self.empresa, sigla='KG', descricao='Quilograma',
            tipo=UnidadeMedida.Tipo.PESO,
        )
        Produto.objects.create(
            filial=self.filial, unidade_medida=unidade, descricao='DRY',
            ncm='00000000',
        )

        saida = self._rodar(('confirmar', True))

        self.assertIn('PULA (já existe)', saida)
        self.assertEqual(Produto.objects.filter(filial=self.filial, descricao='DRY').count(), 1)
        total = len(catalogo_malhas())
        self.assertEqual(Produto.objects.filter(filial=self.filial).count(), total)
