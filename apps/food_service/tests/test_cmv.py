from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario


class CmvViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Padaria Teste LTDA', nome_fantasia='Padaria Teste',
            cnpj='55345678000191', regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1, segmento='padarias',
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Filial Teste', nome_fantasia='Filial Teste',
            cnpj='55345678000192', uf='RN',
        )
        cls.perfil = PerfilAcesso.objects.create(empresa=cls.empresa, nome='Admin', is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email='cmv@inoovated.com', nome='Usuario CMV', password='teste1234',
            empresa=cls.empresa, filial=cls.filial, perfil=cls.perfil,
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        session = self.client.session
        session['filial_ativa_id'] = self.filial.pk
        session.save()

    def test_pagina_sem_vendas_no_periodo(self):
        response = self.client.get(reverse('food_service:cmv'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Controle de CMV')
        self.assertContains(response, 'Nenhuma venda no período.')

    def test_pagina_sem_vendas_com_meta_informada_nao_quebra(self):
        response = self.client.get(reverse('food_service:cmv'), {'meta_cmv': '30'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Controle de CMV')

    def test_pagina_com_vendas_e_meta_mostra_indicador_de_progresso(self):
        resumo = {
            'receita_total': Decimal('1000.00'), 'custo_total': Decimal('350.00'),
            'cmv_percentual': Decimal('35.0'), 'margem_contribuicao': Decimal('650.00'),
            'lucro_bruto': Decimal('650.00'), 'despesas_operacionais': Decimal('200.00'),
            'lucro_liquido': Decimal('450.00'),
            'alertas': [{'mensagem': 'CMV geral em 35.0%, acima da meta de 30%.'}],
            'por_prato': [
                {
                    'nome': 'X-Burguer', 'receita': Decimal('500'), 'custo': Decimal('150'),
                    'cmv_percentual': Decimal('30.0'), 'margem': Decimal('350'), 'tem_ficha': True,
                },
                {
                    'nome': 'Suco', 'receita': Decimal('100'), 'custo': Decimal('50'),
                    'cmv_percentual': Decimal('50.0'), 'margem': Decimal('50'), 'tem_ficha': False,
                },
            ],
            'por_categoria': [
                {
                    'nome': 'Lanches', 'receita': Decimal('500'), 'custo': Decimal('150'),
                    'cmv_percentual': Decimal('30.0'), 'margem': Decimal('350'),
                },
            ],
        }
        with patch('apps.food_service.views.cmv.CmvService.resumo', return_value=resumo):
            response = self.client.get(reverse('food_service:cmv'), {'meta_cmv': '30'})

        self.assertEqual(response.status_code, 200)
        conteudo = response.content.decode()
        self.assertIn('Meta: 30%', conteudo)
        self.assertIn('CMV geral em 35.0%', conteudo)
        self.assertIn('sem ficha técnica', conteudo)
        self.assertIn('X-Burguer', conteudo)
