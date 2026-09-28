import os
import tempfile
from decimal import Decimal
from io import StringIO

import openpyxl
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import Deposito, Estoque
from apps.produtos.models import Produto


class ImportarEstoqueTecidosTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao Teste LTDA', nome_fantasia='Confeccao Teste',
            cnpj='57345678000191', regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Filial Confeccao', nome_fantasia='Filial Confeccao',
            cnpj='57345678000192', uf='RN',
        )
        perfil = PerfilAcesso.objects.create(empresa=cls.empresa, nome='Admin', is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email='import@inoovated.com', nome='Importador', password='teste1234',
            empresa=cls.empresa, filial=cls.filial, perfil=perfil,
        )
        cls.deposito = Deposito.objects.create(
            filial=cls.filial, nome='Tecidos', tipo=Deposito.Tipo.PRODUCAO,
        )

    def _planilha(self, linhas):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(['Nome do tecido', 'Peso (KG)'])
        for linha in linhas:
            ws.append(linha)
        arquivo = tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False)
        arquivo.close()
        wb.save(arquivo.name)
        self.addCleanup(os.remove, arquivo.name)
        return arquivo.name

    def _rodar(self, caminho, *extras):
        saida = StringIO()
        call_command(
            'importar_estoque_tecidos', arquivo=caminho, filial_id=self.filial.pk,
            deposito_id=self.deposito.pk, usuario_email=self.usuario.email,
            stdout=saida, **dict(extras),
        )
        return saida.getvalue()

    def test_simulacao_nao_grava_nada(self):
        caminho = self._planilha([['DRY PRETO', 4.86], ['Dry (Branco)', 9.46]])

        saida = self._rodar(caminho)

        self.assertIn('SIMULAÇÃO', saida)
        self.assertEqual(Produto.objects.filter(filial=self.filial).count(), 0)
        self.assertEqual(Estoque.objects.count(), 0)

    def test_confirmar_cria_produtos_e_saldo_no_deposito(self):
        caminho = self._planilha([['DRY PRETO', 4.86], ['Dry (Branco)', 9.46]])

        self._rodar(caminho, ('confirmar', True))

        preto = Produto.objects.get(filial=self.filial, descricao='DRY PRETO')
        self.assertEqual(preto.unidade_medida.sigla, 'KG')
        estoque = Estoque.objects.get(produto=preto, deposito=self.deposito)
        self.assertEqual(estoque.quantidade_atual, Decimal('4.860'))

    def test_repetir_nao_duplica_nem_soma(self):
        caminho = self._planilha([['DRY PRETO', 4.86]])

        self._rodar(caminho, ('confirmar', True))
        saida = self._rodar(caminho, ('confirmar', True))

        self.assertIn('PULA (já existe)', saida)
        self.assertEqual(Produto.objects.filter(filial=self.filial, descricao='DRY PRETO').count(), 1)
        self.assertEqual(
            Estoque.objects.get(produto__descricao='DRY PRETO').quantidade_atual, Decimal('4.860'),
        )

    def test_nome_repetido_na_planilha_soma_por_padrao(self):
        caminho = self._planilha([['Mescla (Cinza)', 13.92], ['mescla (cinza) ', 28.6]])

        self._rodar(caminho, ('confirmar', True))

        self.assertEqual(Produto.objects.filter(filial=self.filial).count(), 1)
        self.assertEqual(
            Estoque.objects.get(produto__descricao='Mescla (Cinza)').quantidade_atual,
            Decimal('42.520'),
        )

    def test_deposito_de_outra_filial_e_recusado(self):
        outra = Filial.objects.create(
            empresa=self.empresa, razao_social='Outra', nome_fantasia='Outra',
            cnpj='57345678000273', uf='RN',
        )
        estranho = Deposito.objects.create(filial=outra, nome='Outro')
        caminho = self._planilha([['X', 1]])

        with self.assertRaises(CommandError):
            call_command(
                'importar_estoque_tecidos', arquivo=caminho, filial_id=self.filial.pk,
                deposito_id=estranho.pk, usuario_email=self.usuario.email, stdout=StringIO(),
            )
