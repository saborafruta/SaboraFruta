"""
Tela de cadastro do peso por tecido, tipo de peça e grade
(`moda/engenharia/peso-tecido/`) -- independente de produto.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.moda.models import Grade, ItemGrade, PesoTecidoGrade, Tamanho, Tecido


class PesoTecidoViewsBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao Views LTDA', nome_fantasia='Views',
            cnpj='73345678000191', segmento='moda_confeccao',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='73345678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        perfil = PerfilAcesso.objects.create(empresa=cls.empresa, nome='Admin', is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email='pesoviews@teste.local', nome='Fulano', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.tecido = Tecido.objects.create(
            filial=cls.filial, nome='PV', gramatura=200, largura_cm=Decimal('150'),
        )
        cls.grade = Grade.objects.create(filial=cls.filial, nome='Adulto')
        cls.p = Tamanho.objects.create(filial=cls.filial, sigla='P', ordem=10)
        cls.m = Tamanho.objects.create(filial=cls.filial, sigla='M', ordem=20)
        ItemGrade.objects.create(grade=cls.grade, tamanho=cls.p, ordem=10)
        ItemGrade.objects.create(grade=cls.grade, tamanho=cls.m, ordem=20)

    def setUp(self):
        self.client.force_login(self.usuario)

    def _set_filial_sessao(self):
        session = self.client.session
        session['filial_ativa_id'] = self.filial.pk
        session.save()


class PesoTecidoViewTests(PesoTecidoViewsBase):

    def test_tela_abre_sem_combo_escolhido(self):
        self._set_filial_sessao()
        resp = self.client.get(reverse('moda:peso-tecido'))
        self.assertEqual(resp.status_code, 200)

    def test_escolher_combo_sem_grade_mostra_acrescentar(self):
        self._set_filial_sessao()
        resp = self.client.get(reverse('moda:peso-tecido'), {
            'tecido': self.tecido.pk, 'tipo_peca': 'Camisa',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Acrescentar grade')


class PesoTecidoGradeAddViewTests(PesoTecidoViewsBase):

    def test_acrescenta_uma_linha_por_tamanho_da_grade(self):
        self._set_filial_sessao()
        resp = self.client.post(reverse('moda:peso-tecido-grade-add'), {
            'tecido': self.tecido.pk, 'tipo_peca': 'Camisa', 'grade': self.grade.pk,
        })
        self.assertEqual(resp.status_code, 302)
        linhas = PesoTecidoGrade.objects.filter(
            filial=self.filial, tecido=self.tecido, tipo_peca='Camisa', grade=self.grade,
        )
        self.assertEqual(linhas.count(), 2)
        self.assertTrue(all(l.peso_g is None for l in linhas))

    def test_nao_duplica_ao_acrescentar_duas_vezes(self):
        self._set_filial_sessao()
        for _ in range(2):
            self.client.post(reverse('moda:peso-tecido-grade-add'), {
                'tecido': self.tecido.pk, 'tipo_peca': 'Camisa', 'grade': self.grade.pk,
            })
        linhas = PesoTecidoGrade.objects.filter(
            filial=self.filial, tecido=self.tecido, tipo_peca='Camisa', grade=self.grade,
        )
        self.assertEqual(linhas.count(), 2)


class PesoTecidoSalvarViewTests(PesoTecidoViewsBase):

    def test_grava_os_pesos_digitados(self):
        self._set_filial_sessao()
        linha_p = PesoTecidoGrade.objects.create(
            filial=self.filial, tecido=self.tecido, tipo_peca='Camisa',
            grade=self.grade, tamanho=self.p,
        )
        linha_m = PesoTecidoGrade.objects.create(
            filial=self.filial, tecido=self.tecido, tipo_peca='Camisa',
            grade=self.grade, tamanho=self.m,
        )

        resp = self.client.post(reverse('moda:peso-tecido-salvar'), {
            'tecido': self.tecido.pk, 'tipo_peca': 'Camisa',
            f'peso_{linha_p.pk}': '145', f'peso_{linha_m.pk}': '176,5',
        })

        self.assertEqual(resp.status_code, 302)
        linha_p.refresh_from_db()
        linha_m.refresh_from_db()
        self.assertEqual(linha_p.peso_g, Decimal('145.0'))
        self.assertEqual(linha_m.peso_g, Decimal('176.5'))


class PesoTecidoGradeRemoverViewTests(PesoTecidoViewsBase):

    def test_remove_as_linhas_da_grade(self):
        self._set_filial_sessao()
        PesoTecidoGrade.objects.create(
            filial=self.filial, tecido=self.tecido, tipo_peca='Camisa',
            grade=self.grade, tamanho=self.p, peso_g=Decimal('145'),
        )

        resp = self.client.post(
            reverse('moda:peso-tecido-grade-remover', args=[self.grade.pk]),
            {'tecido': self.tecido.pk, 'tipo_peca': 'Camisa'},
        )

        self.assertEqual(resp.status_code, 302)
        self.assertFalse(
            PesoTecidoGrade.objects.filter(
                filial=self.filial, tecido=self.tecido, tipo_peca='Camisa',
            ).exists()
        )
