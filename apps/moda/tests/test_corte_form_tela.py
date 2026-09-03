"""
A tela de novo corte (enfesto).

O QUE A COLUNA ÚNICA ESCONDIA

14 campos empilhados numa faixa estreita tratavam "Ordem" e "Observação"
como se pesassem o mesmo, e números que se comparam no enfesto real —
largura × comprimento do risco, consumo planejado × real — ficavam
páginas de rolagem um do outro. Agrupados em seções, cada grupo responde
uma pergunta (de onde vem o corte, quando e quem, o risco, o consumo), e
os números que andam juntos ficam lado a lado.
"""
from django.test import TestCase
from django.urls import reverse

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario


class CorteFormTelaTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao Corte LTDA', nome_fantasia='Corte',
            cnpj='63345678000191', segmento='moda_confeccao',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Confeccao Corte LTDA',
            cnpj='63345678000272', uf='RN', cidade='Natal', is_matriz=True,
        )
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='corte@moda.local', nome='Corte', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )

    def setUp(self):
        self.client.force_login(self.usuario)

    def test_a_tela_ocupa_a_largura_toda(self):
        """
        A descrição continua com uma largura confortável de leitura
        (`max-w-3xl` nela é proposital) — o que muda é o container em
        volta do formulário, que não fica mais preso em `max-w-3xl mx-auto`.
        """
        html = self.client.get(reverse('moda:corte-create')).content.decode()

        self.assertNotIn('max-w-3xl mx-auto', html)
        self.assertIn('w-full space-y-4', html)

    def test_os_campos_ficam_agrupados_em_secoes(self):
        html = self.client.get(reverse('moda:corte-create')).content.decode()

        self.assertIn('1. Ordem e material', html)
        self.assertIn('2. Quando e quem', html)
        self.assertIn('3. O risco', html)
        self.assertIn('4. Consumo de tecido', html)
        self.assertIn('5. Observação', html)

    def test_todos_os_campos_do_formulario_continuam_na_tela(self):
        """
        Reorganizar em seções não pode ser reorganizar perdendo campo pelo
        caminho — cada um dos 14 continua alcançável.
        """
        html = self.client.get(reverse('moda:corte-create')).content.decode()

        for nome in (
            'ordem', 'tecido', 'cor', 'lote', 'data', 'responsavel', 'status',
            'encaixe', 'largura_tecido', 'comprimento_encaixe', 'folhas',
            'aproveitamento', 'consumo_planejado', 'consumo_real', 'observacao',
        ):
            self.assertIn(f'name="{nome}"', html, f'campo {nome} sumiu da tela')

    def test_as_acoes_ficam_na_tela(self):
        html = self.client.get(reverse('moda:corte-create')).content.decode()

        self.assertIn('Salvar corte', html)
        self.assertIn('Cancelar', html)
