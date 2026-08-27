"""
A tela de configuração das naturezas de operação.

É ELA QUE TIRA O FISCAL DO CÓDIGO. Sem ela a parametrização existe só no
banco, e mudar um CFOP volta a ser tarefa de quem tem acesso ao servidor — que
é exatamente o que a tabela veio evitar.
"""
from datetime import date

from django.test import TestCase
from django.urls import reverse

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.fiscal.forms_natureza import RegraNaturezaForm
from apps.fiscal.models import NaturezaOperacao, RegraNaturezaOperacao


class ConfiguracaoDeNaturezaTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Config LTDA', nome_fantasia='Config',
            cnpj='63345678000191', segmento='polpa_frutas',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='63345678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='cfg@fiscal.local', nome='Cfg', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        self.url_lista = reverse('fiscal:natureza-list')
        self.url_nova = reverse('fiscal:natureza-create')

    def _natureza(self, codigo='remessa_venda_fora'):
        return NaturezaOperacao.objects.create(
            filial=self.filial, codigo=codigo,
            descricao='Remessa para venda fora do estabelecimento',
            especie=NaturezaOperacao.Especie.REMESSA_VENDA_FORA,
            exige_destinatario=False,
        )

    def _dados_regra(self, **extras):
        base = {'cfop': '5904', 'finalidade_nfe': '1'}
        base.update(extras)
        return base

    # ── A tela existe e é usável ─────────────────────────────────────────

    def test_a_lista_abre(self):
        self._natureza()

        resposta = self.client.get(self.url_lista)

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Remessa para venda fora do estabelecimento')

    def test_natureza_sem_regra_e_sinalizada(self):
        """
        Natureza sem regra não emite nada: o resolvedor recusa em vez de chutar
        um CFOP, e o lugar de descobrir isso é aqui.
        """
        self._natureza()

        resposta = self.client.get(self.url_lista)

        self.assertContains(resposta, 'sem regra')

    def test_criar_natureza_pela_tela(self):
        resposta = self.client.post(self.url_nova, {
            'codigo': 'remessa_venda_fora',
            'descricao': 'Remessa para venda fora do estabelecimento',
            'especie': NaturezaOperacao.Especie.REMESSA_VENDA_FORA,
            'movimenta_estoque': 'on',
        })

        natureza = NaturezaOperacao.objects.get()
        self.assertRedirects(
            resposta, reverse('fiscal:natureza-edit', args=[natureza.pk]),
        )
        self.assertEqual(natureza.filial, self.filial)

    def test_codigo_repetido_na_filial_e_recusado(self):
        self._natureza('remessa')

        self.client.post(self.url_nova, {
            'codigo': 'remessa', 'descricao': 'Outra',
            'especie': NaturezaOperacao.Especie.REMESSA_SIMPLES,
        })

        self.assertEqual(NaturezaOperacao.objects.count(), 1)

    def test_natureza_de_outra_filial_nao_abre(self):
        outra = Filial.objects.create(
            empresa=self.empresa, razao_social='Segunda',
            cnpj='31345678000677', uf='RN', cidade='Mossoro',
        )
        alheia = NaturezaOperacao.objects.create(
            filial=outra, codigo='x', descricao='Alheia',
            especie=NaturezaOperacao.Especie.VENDA,
        )

        resposta = self.client.get(reverse('fiscal:natureza-edit', args=[alheia.pk]))

        self.assertEqual(resposta.status_code, 404)

    # ── As regras: o que a seção 7 pede configurar ───────────────────────

    def test_a_regra_configura_tudo_que_a_nota_precisa(self):
        natureza = self._natureza()

        self.client.post(
            reverse('fiscal:natureza-regra-create', args=[natureza.pk]),
            self._dados_regra(
                csosn='400', cst_pis='49', cst_cofins='49', cst_ipi='53',
                aliquota_icms='18', aliquota_ipi='5', aliquota_pis='1.65',
                aliquota_cofins='7.6',
                informacoes_complementares='Mercadoria para venda fora do estabelecimento.',
            ),
        )

        regra = RegraNaturezaOperacao.objects.get()
        self.assertEqual(regra.cfop, '5904')
        self.assertEqual(regra.csosn, '400')
        self.assertEqual(regra.cst_ipi, '53')
        self.assertEqual(str(regra.aliquota_ipi), '5.0000')
        self.assertIn('venda fora', regra.informacoes_complementares)

    def test_a_regra_por_uf_e_a_interestadual_convivem(self):
        """5.904 interna e 6.904 interestadual — nenhum dos dois no código."""
        natureza = self._natureza()
        url = reverse('fiscal:natureza-regra-create', args=[natureza.pk])

        self.client.post(url, self._dados_regra(cfop='5904'))
        self.client.post(url, self._dados_regra(
            cfop='6904', somente_interestadual='on',
        ))

        self.assertEqual(
            set(RegraNaturezaOperacao.objects.values_list('cfop', flat=True)),
            {'5904', '6904'},
        )

    def test_destino_e_interestadual_juntos_sao_recusados(self):
        """As duas coisas juntas descrevem alvos diferentes."""
        natureza = self._natureza()

        resposta = self.client.post(
            reverse('fiscal:natureza-regra-create', args=[natureza.pk]),
            self._dados_regra(uf_destino='PB', somente_interestadual='on'),
            follow=True,
        )

        self.assertEqual(RegraNaturezaOperacao.objects.count(), 0)
        avisos = [str(m) for m in resposta.context['messages']]
        self.assertTrue(any('interestadual' in a for a in avisos), avisos)

    def test_cst_e_csosn_juntos_sao_recusados(self):
        """
        O regime da empresa define qual vale, e mandar os dois na nota é
        rejeição certa na SEFAZ.
        """
        natureza = self._natureza()

        resposta = self.client.post(
            reverse('fiscal:natureza-regra-create', args=[natureza.pk]),
            self._dados_regra(cst_icms='00', csosn='400'), follow=True,
        )

        self.assertEqual(RegraNaturezaOperacao.objects.count(), 0)
        avisos = [str(m) for m in resposta.context['messages']]
        self.assertTrue(any('CSOSN' in a for a in avisos), avisos)

    def test_vigencia_invertida_e_recusada(self):
        natureza = self._natureza()

        self.client.post(
            reverse('fiscal:natureza-regra-create', args=[natureza.pk]),
            self._dados_regra(
                vigencia_inicio='2026-09-01', vigencia_fim='2026-08-01',
            ),
        )

        self.assertEqual(RegraNaturezaOperacao.objects.count(), 0)

    def test_a_uf_vai_em_maiuscula(self):
        natureza = self._natureza()

        self.client.post(
            reverse('fiscal:natureza-regra-create', args=[natureza.pk]),
            self._dados_regra(uf_destino='pb'),
        )

        self.assertEqual(RegraNaturezaOperacao.objects.get().uf_destino, 'PB')

    def test_a_data_de_vigencia_volta_no_formato_que_o_navegador_aceita(self):
        """
        Com pt-br o Django renderiza 27/08/2026, e `<input type="date">`
        descarta o que não for ISO — mostrando o campo vazio.
        """
        natureza = self._natureza()
        regra = RegraNaturezaOperacao.objects.create(
            natureza=natureza, cfop='5904', vigencia_inicio=date(2026, 3, 15),
        )

        form = RegraNaturezaForm(instance=regra, filial=self.filial)

        self.assertIn('value="2026-03-15"', str(form['vigencia_inicio']))

    def test_o_erro_diz_qual_campo(self):
        """Quem cadastra regra fiscal lida com vinte campos parecidos."""
        natureza = self._natureza()

        resposta = self.client.post(
            reverse('fiscal:natureza-regra-create', args=[natureza.pk]),
            {'finalidade_nfe': '1'}, follow=True,
        )

        avisos = [str(m) for m in resposta.context['messages']]
        self.assertTrue(any('Cfop' in a or 'CFOP' in a for a in avisos), avisos)

    def test_remover_regra(self):
        natureza = self._natureza()
        regra = RegraNaturezaOperacao.objects.create(natureza=natureza, cfop='5904')

        self.client.post(
            reverse('fiscal:natureza-regra-delete', args=[natureza.pk, regra.pk]),
        )

        self.assertEqual(RegraNaturezaOperacao.objects.count(), 0)

    def test_a_tela_nao_vaza_sintaxe_de_template(self):
        natureza = self._natureza()
        RegraNaturezaOperacao.objects.create(natureza=natureza, cfop='5904')

        for url in (self.url_lista, reverse('fiscal:natureza-edit', args=[natureza.pk])):
            html = self.client.get(url).content.decode()
            for resto in ('{#', '#}', '{%', '%}'):
                self.assertNotIn(resto, html, f'vazou sintaxe em {url}')
