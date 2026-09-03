"""
O peso por tamanho e a grade da peça, na ficha técnica.

DUAS PERGUNTAS QUE A FICHA NÃO RESPONDIA

  · QUANTO PESA CADA TAMANHO. A mesma camisa pesa 145 g no P e 230 g no XG
    — um número único não dizia nada disso. E a mesma ficha pode ser
    cortada em mais de uma grade (a versão solta e a oversized do mesmo
    modelo), cada uma com peso próprio tamanho a tamanho. Por isso a
    tabela é por grade, e a ficha aceita quantas forem necessárias
    (Adulto, Oversized, Baby Look...).

  · QUAIS TAMANHOS A PEÇA CORTA. A ficha mostrava "Adulto" — que não diz
    se a peça vai até GG ou até XGG — e um traço quando o produto não
    tinha grade. Um traço não separa "falta cadastrar a grade" de "falta
    ligá-la ao produto", e são telas diferentes.

AUSÊNCIA APARECE. Peso em branco continua em branco (placeholder "não
pesado"), nunca vira zero — zero gravado passaria por peça real na hora de
cotar frete.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.core.constants.segmentos import MODA_CONFECCAO
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.moda.models import (
    FichaTecnica, Grade, ItemGrade, PesoTamanhoFicha, ProdutoModa, Tamanho,
)


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

    # ── Peso por tamanho ─────────────────────────────────────────────────

    def test_acrescentar_a_grade_cria_uma_linha_por_tamanho_em_branco(self):
        """
        A tabela aparece vazia, esperando ser preenchida — e não some até
        alguém digitar o primeiro peso.
        """
        grade = self._grade('Adulto', ['P', 'M', 'G'])

        self.client.post(
            reverse('moda:ficha-peso-grade-add', args=[self.ficha.pk]),
            {'grade': grade.pk},
        )

        linhas = PesoTamanhoFicha.objects.filter(ficha=self.ficha, grade=grade)
        self.assertEqual(linhas.count(), 3)
        self.assertTrue(all(l.peso_g is None for l in linhas))

    def test_a_tela_mostra_o_tamanho_e_o_peso_da_grade_acrescentada(self):
        grade = self._grade('Adulto', ['P', 'M', 'G'])
        self.client.post(
            reverse('moda:ficha-peso-grade-add', args=[self.ficha.pk]),
            {'grade': grade.pk},
        )
        linha = PesoTamanhoFicha.objects.get(
            ficha=self.ficha, grade=grade, tamanho__sigla='M',
        )
        linha.peso_g = Decimal('176.0')
        linha.save(update_fields=['peso_g'])

        html = self.client.get(self.url).content.decode()

        self.assertIn('Adulto', html)
        self.assertIn('Peso por tamanho', html)

    def test_salvar_grava_os_pesos_digitados(self):
        grade = self._grade('Adulto', ['P', 'M', 'G'])
        self.client.post(
            reverse('moda:ficha-peso-grade-add', args=[self.ficha.pk]),
            {'grade': grade.pk},
        )
        linhas = list(PesoTamanhoFicha.objects.filter(ficha=self.ficha, grade=grade).order_by('ordem'))

        self.client.post(
            reverse('moda:ficha-peso-salvar', args=[self.ficha.pk]),
            {f'peso_{linhas[0].pk}': '145', f'peso_{linhas[1].pk}': '176,0', f'peso_{linhas[2].pk}': '193.5'},
        )

        pesos = {l.tamanho.sigla: l.peso_g for l in PesoTamanhoFicha.objects.filter(ficha=self.ficha)}
        self.assertEqual(pesos['P'], Decimal('145'))
        self.assertEqual(pesos['M'], Decimal('176.0'))
        self.assertEqual(pesos['G'], Decimal('193.5'))

    def test_salvar_com_campo_vazio_mantem_o_peso_ausente_e_nao_zero(self):
        """
        Zero gravado passaria por peça real na hora de cotar frete — o
        campo vazio precisa continuar `None`, não virar `0`.
        """
        grade = self._grade('Adulto', ['P'])
        self.client.post(
            reverse('moda:ficha-peso-grade-add', args=[self.ficha.pk]),
            {'grade': grade.pk},
        )
        linha = PesoTamanhoFicha.objects.get(ficha=self.ficha, grade=grade)

        self.client.post(
            reverse('moda:ficha-peso-salvar', args=[self.ficha.pk]),
            {f'peso_{linha.pk}': ''},
        )

        linha.refresh_from_db()
        self.assertIsNone(linha.peso_g)

    def test_duas_grades_pesadas_ao_mesmo_tempo_ficam_separadas(self):
        """
        A camisa oversized não é a versão escalada da solta — pesa
        diferente tamanho a tamanho, e a ficha precisa das duas tabelas
        lado a lado.
        """
        adulto = self._grade('Adulto', ['P', 'M', 'G'])
        oversized = self._grade('Oversized', ['UNICO'], ordem_base=10)

        self.client.post(
            reverse('moda:ficha-peso-grade-add', args=[self.ficha.pk]),
            {'grade': adulto.pk},
        )
        self.client.post(
            reverse('moda:ficha-peso-grade-add', args=[self.ficha.pk]),
            {'grade': oversized.pk},
        )

        grupos = self.ficha.pesos_por_grade
        nomes = {g['grade_nome'] for g in grupos}
        self.assertEqual(nomes, {'Adulto', 'Oversized'})
        adulto_grupo = next(g for g in grupos if g['grade_nome'] == 'Adulto')
        self.assertEqual(len(adulto_grupo['linhas']), 3)

    def test_remover_a_grade_apaga_a_tabela_inteira(self):
        grade = self._grade('Adulto', ['P', 'M'])
        self.client.post(
            reverse('moda:ficha-peso-grade-add', args=[self.ficha.pk]),
            {'grade': grade.pk},
        )

        self.client.post(
            reverse('moda:ficha-peso-grade-remover', args=[self.ficha.pk, grade.pk]),
        )

        self.assertEqual(
            PesoTamanhoFicha.objects.filter(ficha=self.ficha, grade=grade).count(), 0,
        )

    def test_acrescentar_a_mesma_grade_duas_vezes_nao_duplica(self):
        grade = self._grade('Adulto', ['P', 'M'])
        for _ in range(2):
            self.client.post(
                reverse('moda:ficha-peso-grade-add', args=[self.ficha.pk]),
                {'grade': grade.pk},
            )

        self.assertEqual(
            PesoTamanhoFicha.objects.filter(ficha=self.ficha, grade=grade).count(), 2,
        )

    def test_sem_nenhuma_grade_pesada_a_tela_diz_para_acrescentar(self):
        html = self.client.get(self.url).content.decode()

        self.assertIn('Nenhuma grade pesada ainda', html)

    # ── Grade da peça (produto) ─────────────────────────────────────────

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
