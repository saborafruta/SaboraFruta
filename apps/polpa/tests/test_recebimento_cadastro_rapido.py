"""
Cadastrar fruta e produtor sem sair do romaneio.

A TELA ERA UM BECO. `fruta` e `produtor` sao obrigatorios, e numa filial nova
os dois selects vem vazios -- nao havia como criar nenhum dos dois dali. O
caminho era abandonar o romaneio (perdendo o que ja' foi digitado), ir ao
cadastro e comecar de novo. Com o motorista esperando na balanca, o que
acontece de verdade e' a pesagem ir para um papel para ser digitada depois --
que e' exatamente o registro que se perde.
"""
import json

from django.test import TestCase
from django.urls import reverse

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.polpa.models import Fruta


class CadastroRapidoBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Polpa Rapida LTDA', nome_fantasia='Polpa Rapida',
            cnpj='71145678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1, segmento='polpa_frutas',
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='71145678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='balanca@polpa.local', nome='Balanca', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )

    def setUp(self):
        self.client.force_login(self.usuario)


class FrutaRelampagoTests(CadastroRapidoBase):

    def _criar(self, **dados):
        return self.client.post(reverse('polpa:fruta-ajax-create'), dados)

    def test_cria_a_fruta_e_devolve_a_opcao_pronta(self):
        """
        Criar e mandar procurar na lista seria metade do favor: a resposta traz
        id e rotulo justamente para a tela ja' deixar selecionado.
        """
        resposta = self._criar(nome='Manga', variedade='Tommy')

        corpo = json.loads(resposta.content)
        fruta = Fruta.objects.for_filial(self.filial).get(nome='Manga')
        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(corpo['ok'])
        self.assertEqual(corpo['id'], fruta.pk)
        self.assertIn('Manga', corpo['label'])

    def test_a_fruta_nasce_na_filial_de_quem_criou(self):
        self._criar(nome='Acerola')

        fruta = Fruta.objects.get(nome='Acerola')
        self.assertEqual(fruta.filial_id, self.filial.pk)

    def test_nome_vazio_nao_grava(self):
        resposta = self._criar(nome='   ', variedade='Tommy')

        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(json.loads(resposta.content)['ok'])
        self.assertFalse(Fruta.objects.exists())

    def test_fruta_repetida_e_recusada(self):
        """
        Sem isto, quem nao achou "Manga" na lista por causa de um acento cria a
        segunda "Manga" -- e o historico de rendimento nasce partido em dois.
        """
        self._criar(nome='Manga', variedade='Tommy')
        resposta = self._criar(nome='manga', variedade='TOMMY')

        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(Fruta.objects.count(), 1)

    def test_mesma_fruta_com_outra_variedade_passa(self):
        """Manga Tommy e Manga Palmer sao frutas diferentes na pratica."""
        self._criar(nome='Manga', variedade='Tommy')
        resposta = self._criar(nome='Manga', variedade='Palmer')

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(Fruta.objects.count(), 2)

    def test_a_ficha_tecnica_fica_para_depois(self):
        """
        Quem esta' na balanca nao tem a tabela do laboratorio na mao. Exigir
        brix aqui so' produziria numero inventado -- e brix inventado vira
        criterio de aceite errado la' na classificacao. Nulo e' honesto:
        significa "ninguem mediu ainda", e nao "o limite e' zero".
        """
        self._criar(nome='Cajá')

        fruta = Fruta.objects.get(nome='Cajá')
        self.assertIsNone(fruta.brix_minimo)
        self.assertIsNone(fruta.ph_maximo)


class TelaDoRomaneioTests(CadastroRapidoBase):

    def test_a_tela_oferece_as_duas_saidas(self):
        """O beco tinha que deixar de ser beco NA PROPRIA TELA."""
        resposta = self.client.get(reverse('polpa:recebimento-create'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Nova fruta')
        self.assertContains(resposta, 'Novo produtor')
        self.assertContains(resposta, reverse('polpa:fruta-ajax-create'))
        self.assertContains(resposta, reverse('cadastros:fornecedor-ajax-create'))

    def test_o_select_vazio_diz_o_que_fazer(self):
        """
        `---------`, o padrao do Django, nao diz nada -- e num select vazio
        parece campo carregando, e nao campo sem opcao.
        """
        resposta = self.client.get(reverse('polpa:recebimento-create'))

        self.assertContains(resposta, 'Selecione a fruta')
        self.assertContains(resposta, 'Selecione o produtor')
        self.assertNotContains(resposta, '---------')

    def test_a_fruta_recem_criada_aparece_na_lista_do_romaneio(self):
        """A volta do favor: criada, ela precisa estar selecionavel."""
        self.client.post(reverse('polpa:fruta-ajax-create'), {'nome': 'Goiaba'})

        resposta = self.client.get(reverse('polpa:recebimento-create'))

        self.assertContains(resposta, 'Goiaba')
