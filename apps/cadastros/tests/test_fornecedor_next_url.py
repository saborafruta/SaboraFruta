"""
Editar e "excluir" fornecedor sem sair de onde a pessoa estava.

Antes, as duas ações mandavam sempre para a lista geral de fornecedores —
mesmo quando quem clicou veio de outra tela (a lista de Produtores do
Polpa, por exemplo). Editar um fornecedor no meio de outro trabalho e cair
na lista de Cadastros é perder o lugar; `?next=` traz de volta.

"EXCLUIR" É DESATIVAR. `Recebimento.produtor` tem `on_delete=PROTECT`, e um
fornecedor com histórico de entrega nem deixaria apagar de verdade — o
banco recusaria. Por isso a view marca `ativo=False`, do mesmo jeito que o
botão de ativar/desativar; a diferença é só o rótulo do botão.
"""
from django.test import TestCase
from django.urls import reverse

from apps.cadastros.models import Fornecedor, FornecedorFilial
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario


class FornecedorProximaTelaTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Fornecedor Next LTDA', nome_fantasia='Next',
            cnpj='83345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='83345678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='next@fornecedor.local', nome='Next', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.fornecedor = Fornecedor.objects.create(
            filial=cls.filial, tipo_pessoa='J', razao_social='Sítio Modelo',
            cpf_cnpj='12345678000190',
        )
        # `Fornecedor.objects.for_filial` enxerga pelo vínculo
        # (`FornecedorFilial`), não pela FK `filial` sozinha — é o cadastro
        # central podendo atender mais de uma filial.
        FornecedorFilial.objects.create(fornecedor=cls.fornecedor, filial=cls.filial)

    def setUp(self):
        self.client.force_login(self.usuario)

    # ── Editar ───────────────────────────────────────────────────────────

    def test_sem_next_o_cancelar_vai_para_a_lista_de_fornecedores(self):
        resposta = self.client.get(
            reverse('cadastros:fornecedor-update', args=[self.fornecedor.pk]),
        )

        self.assertContains(resposta, reverse('cadastros:fornecedor-list'))

    def test_com_next_o_cancelar_volta_para_quem_chamou(self):
        volta = reverse('polpa:recebimento-produtores')

        resposta = self.client.get(
            reverse('cadastros:fornecedor-update', args=[self.fornecedor.pk]) + f'?next={volta}',
        )

        self.assertContains(resposta, volta)

    def test_salvar_com_next_redireciona_para_quem_chamou(self):
        volta = reverse('polpa:recebimento-produtores')

        resposta = self.client.post(
            reverse('cadastros:fornecedor-update', args=[self.fornecedor.pk]) + f'?next={volta}',
            {
                'tipo_pessoa': 'J', 'razao_social': 'Sítio Modelo Atualizado',
                'pais': 'Brasil', 'codigo_pais_bacen': '1058', 'prazo_entrega_dias': '0',
            },
        )

        self.assertRedirects(resposta, volta, fetch_redirect_response=False)

    def test_next_para_outro_site_e_ignorado(self):
        """
        `next` malicioso (open redirect) não é seguido — cai no padrão.
        """
        resposta = self.client.get(
            reverse('cadastros:fornecedor-update', args=[self.fornecedor.pk])
            + '?next=https://site-malicioso.exemplo/',
        )

        self.assertContains(resposta, reverse('cadastros:fornecedor-list'))
        self.assertNotContains(resposta, 'site-malicioso.exemplo')

    # ── Excluir (= desativar) ────────────────────────────────────────────

    def test_excluir_desativa_em_vez_de_apagar(self):
        self.client.post(
            reverse('cadastros:fornecedor-delete', args=[self.fornecedor.pk]),
        )

        self.fornecedor.refresh_from_db()
        self.assertFalse(self.fornecedor.ativo)
        self.assertTrue(
            Fornecedor.objects.filter(pk=self.fornecedor.pk).exists(),
            'a linha deveria continuar existindo — "excluir" desativa, não apaga',
        )

    def test_excluir_volta_para_quem_chamou(self):
        volta = reverse('polpa:recebimento-produtores')

        resposta = self.client.post(
            reverse('cadastros:fornecedor-delete', args=[self.fornecedor.pk]),
            HTTP_REFERER=volta,
        )

        self.assertRedirects(resposta, volta, fetch_redirect_response=False)

    def test_excluir_sem_referer_cai_na_lista_de_fornecedores(self):
        resposta = self.client.post(
            reverse('cadastros:fornecedor-delete', args=[self.fornecedor.pk]),
        )

        self.assertRedirects(resposta, reverse('cadastros:fornecedor-list'))
