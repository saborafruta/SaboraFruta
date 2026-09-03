"""
O peso da peça pronta e a grade, na ficha técnica.

DUAS PERGUNTAS QUE A FICHA NÃO RESPONDIA

  · QUANTO PESA. O custo estava no cabeçalho, o peso não existia. Quem cota
    frete pesava por cima, ou ia à balança e o número morria num papel.
    Peso é da FICHA e não do produto porque é resultado de engenharia: muda
    quando muda o tecido ou o consumo.

  · QUAIS TAMANHOS. A ficha mostrava "Adulto" — que não diz se a peça vai
    até GG ou até XGG — e um traço quando o produto não tinha grade. Um
    traço não separa "falta cadastrar a grade" de "falta ligá-la ao
    produto", e são telas diferentes.

AUSÊNCIA APARECE. Peso em branco é dito em laranja, não deixado vazio: um
campo vazio passa por peça leve, um aviso manda alguém à balança.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.core.constants.segmentos import MODA_CONFECCAO
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.moda.models import FichaTecnica, Grade, ItemGrade, ProdutoModa, Tamanho


class FichaPesoGradeTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao Peso LTDA', nome_fantasia='Peso',
            cnpj='63345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
            segmento=MODA_CONFECCAO,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Confeccao Peso LTDA',
            cnpj='63345678000272', uf='RN', cidade='Natal', is_matriz=True,
        )
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='peso@moda.local', nome='Peso', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.produto = ProdutoModa.objects.create(
            filial=cls.filial, codigo='AGA152', nome='Agasalho com zíper',
        )
        cls.ficha = FichaTecnica.objects.create(
            filial=cls.filial, produto=cls.produto,
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        self.url = reverse('moda:ficha-detail', args=[self.ficha.pk])

    def _grade(self, nome, siglas, ordem_base=0):
        grade = Grade.objects.create(filial=self.filial, nome=nome)
        for posicao, sigla in enumerate(siglas, start=1):
            tamanho, _ = Tamanho.objects.get_or_create(
                filial=self.filial, sigla=sigla,
                defaults={'ordem': (ordem_base + posicao) * 10},
            )
            ItemGrade.objects.create(
                grade=grade, tamanho=tamanho, ordem=posicao * 10,
            )
        return grade

    # ── Peso ─────────────────────────────────────────────────────────────

    def test_a_ficha_mostra_o_peso_em_gramas_e_em_quilos(self):
        """
        Gramas é como a balança da confecção pesa; quilos é como o frete é
        cotado. Obrigar quem cota a dividir por mil de cabeça é convidar ao
        erro de casa decimal.
        """
        self.ficha.peso_peca_g = Decimal('312.4')
        self.ficha.save(update_fields=['peso_peca_g'])

        html = self.client.get(self.url).content.decode()

        self.assertIn('312,4 g', html)
        # Quilo com tres casas e' precisao de grama: o decimo de grama que a
        # balanca da confeccao le fica na leitura em gramas, ao lado.
        self.assertIn('0,312 kg', html)

    def test_sem_peso_a_ficha_diz_que_ninguem_pesou(self):
        html = self.client.get(self.url).content.decode()

        self.assertIn('ninguém pesou ainda', html)

    def test_peso_ausente_e_none_e_nao_zero(self):
        """
        Zero gravado passaria por peso real na hora de cotar frete.
        """
        self.assertIsNone(self.ficha.peso_peca_g)
        self.assertIsNone(self.ficha.peso_peca_kg)

    def test_o_peso_e_gravado_pela_tela_de_edicao(self):
        resposta = self.client.post(
            reverse('moda:ficha-update', args=[self.ficha.pk]),
            {
                'produto': self.produto.pk, 'versao': 1, 'status': 'rascunho',
                'peso_peca_g': '312.4', 'descricao': '', 'observacoes': '',
            },
        )

        self.assertEqual(resposta.status_code, 302, resposta.content[:400])
        self.ficha.refresh_from_db()
        self.assertEqual(self.ficha.peso_peca_g, Decimal('312.4'))

    # ── Grade ────────────────────────────────────────────────────────────

    def test_a_ficha_lista_todas_as_grades_da_casa_com_os_tamanhos(self):
        """
        Como no comercial: o nome da grade não diz até que tamanho a peça
        vai — a lista de siglas diz.
        """
        self._grade('Adulto', ['PP', 'P', 'M', 'G', 'GG', 'XGG'])
        self._grade('Baby Look', ['P', 'M', 'G'], ordem_base=10)

        html = self.client.get(self.url).content.decode()

        self.assertIn('Adulto', html)
        self.assertIn('Baby Look', html)
        self.assertIn('PP | P | M | G | GG | XGG', html)

    def test_sem_grade_a_ficha_avisa_que_a_op_nao_sabe_o_que_cortar(self):
        self._grade('Adulto', ['P', 'M', 'G'])

        html = self.client.get(self.url).content.decode()

        self.assertIn('ainda não tem grade', html)

    def test_sem_nenhuma_grade_cadastrada_a_ficha_manda_cadastrar(self):
        """
        "Falta cadastrar a grade" e "falta ligá-la ao produto" resolvem-se
        em telas diferentes — a ficha precisa dizer qual dos dois é.
        """
        html = self.client.get(self.url).content.decode()

        self.assertIn('Nenhuma grade cadastrada', html)
        self.assertIn(reverse('moda:grade-create'), html)

    def test_escolher_a_grade_grava_no_produto(self):
        """
        A grade continua sendo campo do produto — a ficha não guarda cópia.
        """
        grade = self._grade('Adulto', ['P', 'M', 'G'])

        self.client.post(
            reverse('moda:ficha-grade', args=[self.ficha.pk]),
            {'grade': grade.pk},
        )

        self.produto.refresh_from_db()
        self.assertEqual(self.produto.grade_id, grade.pk)

    def test_a_grade_escolhida_aparece_marcada(self):
        grade = self._grade('Adulto', ['P', 'M', 'G'])
        self.produto.grade = grade
        self.produto.save(update_fields=['grade'])

        html = self.client.get(self.url).content.decode()

        self.assertIn('grade desta peça', html)
        self.assertIn('Adulto · P | M | G', html)

    def test_da_para_retirar_a_grade(self):
        grade = self._grade('Adulto', ['P', 'M', 'G'])
        self.produto.grade = grade
        self.produto.save(update_fields=['grade'])

        self.client.post(
            reverse('moda:ficha-grade', args=[self.ficha.pk]), {'grade': ''},
        )

        self.produto.refresh_from_db()
        self.assertIsNone(self.produto.grade_id)

    def test_grade_de_outra_filial_nao_entra(self):
        outra = Filial.objects.create(
            empresa=self.empresa, razao_social='Outra', cnpj='63345678000353',
            uf='RN', cidade='Mossoro',
        )
        alheia = Grade.objects.create(filial=outra, nome='Grade de fora')

        resposta = self.client.post(
            reverse('moda:ficha-grade', args=[self.ficha.pk]),
            {'grade': alheia.pk},
        )

        self.assertEqual(resposta.status_code, 404)
        self.produto.refresh_from_db()
        self.assertIsNone(self.produto.grade_id)
