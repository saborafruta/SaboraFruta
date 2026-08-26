"""
A fila da classificação — a bancada do laboratório.

A CLASSIFICAÇÃO POR ROMANEIO JÁ EXISTIA, dentro da tela da carga. O que faltava
era a FILA, e a fila é que é o trabalho de quem classifica: sem ela, descobrir o
que falta medir exigia abrir a lista de recebimentos, entrar em cada carga e
olhar se já tinha análise.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.models import Fornecedor
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.polpa.models import Fruta, Recebimento


class FilaBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Classifica LTDA', nome_fantasia='Classifica',
            cnpj='81145678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1, segmento='polpa_frutas',
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='81145678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='lab@polpa.local', nome='Laboratorio', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.produtor = Fornecedor.objects.create(
            filial=cls.filial, razao_social='Sitio Boa Vista',
            cpf_cnpj='12345678000190',
        )
        # A RÉGUA DA FRUTA é o que decide aceitar ou devolver.
        cls.manga = Fruta.objects.create(
            filial=cls.filial, nome='Manga', variedade='Tommy',
            brix_minimo=Decimal('12.00'), ph_maximo=Decimal('4.50'),
            impureza_maxima=Decimal('5.00'),
        )

    def setUp(self):
        self.client.force_login(self.usuario)

    def _carga(self, numero=1, **extra):
        dados = {
            'filial': self.filial, 'numero': numero, 'fruta': self.manga,
            'produtor': self.produtor, 'data': timezone.localdate(),
            'peso_bruto': Decimal('1200'), 'tara': Decimal('300'),
            'preco_kg': Decimal('2.50'),
            'status': Recebimento.Status.PESAGEM,
        }
        dados.update(extra)
        return Recebimento.objects.create(**dados)


class AFilaMostraOTrabalhoTests(FilaBase):

    def test_a_carga_pesada_e_nao_medida_aparece_para_analise(self):
        self._carga()

        resposta = self.client.get(reverse('polpa:recebimento-classificacao'))

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(len(resposta.context['aguardando']), 1)

    def test_a_regua_da_fruta_aparece_ao_lado_do_campo(self):
        """
        Deixar os limites noutra tela obriga a decorar numero, e numero
        decorado e' o que vira aceite errado.
        """
        self._carga()

        resposta = self.client.get(reverse('polpa:recebimento-classificacao'))

        self.assertContains(resposta, 'mín. 12,00')
        self.assertContains(resposta, 'máx. 4,50')

    def test_carga_ja_medida_sai_da_fila_de_analise(self):
        self._carga(
            classificado_em=timezone.now(),
            status=Recebimento.Status.CLASSIFICACAO,
        )

        resposta = self.client.get(reverse('polpa:recebimento-classificacao'))

        self.assertEqual(len(resposta.context['aguardando']), 0)
        self.assertEqual(len(resposta.context['medidas']), 1)

    def test_carga_decidida_nao_aparece_em_lista_nenhuma(self):
        """
        Aprovada, a fruta ja' entrou no estoque: nao ha' mais o que medir nem o
        que decidir, e mante-la na bancada faria a fila nunca esvaziar.
        """
        self._carga(
            status=Recebimento.Status.APROVADO,
            classificado_em=timezone.now(),
        )

        resposta = self.client.get(reverse('polpa:recebimento-classificacao'))

        self.assertEqual(len(resposta.context['aguardando']), 0)
        self.assertEqual(len(resposta.context['medidas']), 0)

    def test_a_fila_vazia_diz_que_nao_ha_o_que_fazer(self):
        resposta = self.client.get(reverse('polpa:recebimento-classificacao'))

        self.assertContains(resposta, 'Nenhuma carga esperando análise')


class AnalisarPelaFilaTests(FilaBase):

    def test_gravar_a_analise_pela_fila_devolve_a_fila(self):
        """
        Sao varias cargas em sequencia na bancada. Despejar a pessoa na tela de
        UM romaneio a cada analise faria ela navegar de volta seis vezes por
        manha.
        """
        carga = self._carga()

        resposta = self.client.post(
            reverse('polpa:recebimento-classificar', args=[carga.pk]),
            {'brix': '14.00', 'ph': '4.00', 'voltar': 'fila'},
        )

        # A view devolve o MESMO redirect quando o formulario e' invalido, entao
        # so' conferir o destino deixaria este teste passar com a analise
        # perdida. O `classificado` prende as duas coisas.
        carga.refresh_from_db()
        self.assertRedirects(
            resposta, reverse('polpa:recebimento-classificacao'),
        )
        self.assertTrue(carga.classificado)

    def test_sem_o_marcador_continua_voltando_para_o_romaneio(self):
        """A tela do romaneio nao pode mudar de comportamento por causa disto."""
        carga = self._carga()

        resposta = self.client.post(
            reverse('polpa:recebimento-classificar', args=[carga.pk]),
            {'brix': '14.00'},
        )

        self.assertRedirects(
            resposta, reverse('polpa:recebimento-detail', args=[carga.pk]),
        )

    def test_a_analise_gravada_pela_fila_fica_no_romaneio(self):
        carga = self._carga()

        self.client.post(
            reverse('polpa:recebimento-classificar', args=[carga.pk]),
            {'brix': '14.00', 'ph': '4.00', 'impureza': '2.00', 'voltar': 'fila'},
        )

        carga.refresh_from_db()
        self.assertEqual(carga.brix, Decimal('14.00'))
        self.assertTrue(carga.classificado)
        self.assertEqual(carga.status, Recebimento.Status.CLASSIFICACAO)

    def test_quem_mediu_fica_registrado(self):
        """Analise sem responsavel e' o primeiro registro que a fiscalizacao pede."""
        carga = self._carga()

        self.client.post(
            reverse('polpa:recebimento-classificar', args=[carga.pk]),
            {'brix': '14.00', 'voltar': 'fila'},
        )

        carga.refresh_from_db()
        self.assertEqual(carga.classificado_por_id, self.usuario.pk)


class ODesvioApareceTests(FilaBase):

    def test_brix_abaixo_do_minimo_vira_desvio(self):
        carga = self._carga()

        self.client.post(
            reverse('polpa:recebimento-classificar', args=[carga.pk]),
            {'brix': '9.00', 'voltar': 'fila'},
        )

        carga.refresh_from_db()
        desvios = carga.reprovacoes()
        self.assertEqual(len(desvios), 1)
        self.assertIn('verde', desvios[0])

    def test_o_desvio_nao_impede_gravar(self):
        """
        Quem decide e' a pessoa na bancada: travar aqui faria alguem digitar
        outro numero para conseguir seguir -- o registro que nao se quer.
        """
        carga = self._carga()

        self.client.post(
            reverse('polpa:recebimento-classificar', args=[carga.pk]),
            {'brix': '9.00', 'voltar': 'fila'},
        )

        carga.refresh_from_db()
        self.assertTrue(carga.classificado)

    def test_a_lista_de_medidas_marca_quem_tem_desvio(self):
        self._carga(
            numero=1, brix=Decimal('9.00'),
            classificado_em=timezone.now(),
            status=Recebimento.Status.CLASSIFICACAO,
        )

        resposta = self.client.get(reverse('polpa:recebimento-classificacao'))

        self.assertContains(resposta, 'desvio(s)')

    def test_carga_dentro_da_regua_aparece_como_tal(self):
        self._carga(
            numero=1, brix=Decimal('15.00'), ph=Decimal('4.00'),
            impureza=Decimal('1.00'), classificado_em=timezone.now(),
            status=Recebimento.Status.CLASSIFICACAO,
        )

        resposta = self.client.get(reverse('polpa:recebimento-classificacao'))

        self.assertContains(resposta, 'Dentro da régua')


class OHubDeixaDeDizerEmConstrucaoTests(FilaBase):

    def test_a_rota_do_menu_abre_a_tela_de_verdade(self):
        """
        O hub descobre tela pronta por RESOLUCAO DE ROTA: enquanto o endereco
        do menu resolvesse para a `ItemView`, o selo "em construcao" continuaria
        aparecendo. Este teste prende as duas pontas -- o endereco que o menu
        aponta e a tela que ele abre.
        """
        from django.urls import resolve

        from apps.polpa.views import ItemView

        endereco = reverse('polpa:item', args=['recebimento', 'classificacao'])
        achado = resolve(endereco)

        self.assertIsNot(getattr(achado.func, 'view_class', None), ItemView)
        self.assertEqual(
            endereco, reverse('polpa:recebimento-classificacao'),
        )
