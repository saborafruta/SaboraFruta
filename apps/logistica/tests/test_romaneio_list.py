"""
A lista de romaneios de carga.

O menu de status abre em cima de uma tabela que mora dentro de um card com
`overflow: hidden`. Menu posicionado por `absolute` e' recortado por esse card:
na ultima linha ele abria para baixo e o card cortava tudo -- sobrava uma faixa
escura vazia, e parecia que o status nao tinha para onde ir.
"""
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.logistica.models import RomaneioCarga


class ListaDeRomaneiosTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Carga LTDA', nome_fantasia='Carga',
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
            email='carga@doca.local', nome='Carga', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        self.url = reverse('logistica:romaneio-list')

    def _romaneio(self, numero, status=RomaneioCarga.Status.EM_CARREGAMENTO):
        return RomaneioCarga.objects.create(
            filial=self.filial, numero=numero, data=timezone.localdate(),
            status=status, motorista_nome='Seu Zé', veiculo_placa='ABC1D23',
        )

    # ── O menu de status ─────────────────────────────────────────────────

    def test_o_menu_de_status_nao_fica_preso_dentro_do_card(self):
        """
        `absolute` e' recortado pelo `card overflow-hidden` que embrulha a
        tabela; `fixed` escapa. Na ultima linha era a diferenca entre ver as
        opcoes e ver uma faixa vazia.
        """
        self._romaneio(1)

        html = self.client.get(self.url).content.decode()

        self.assertIn('fixed z-50', html, 'o menu voltou a ser posicionado por absolute')
        self.assertNotIn('absolute left-0 top-full', html)

    def test_o_menu_e_medido_antes_de_aparecer(self):
        """
        A altura muda conforme o status atual -- de onde ele esta' saem opcoes
        diferentes. Chutar um valor faria o menu subir de menos e sair da tela.
        """
        self._romaneio(1)

        html = self.client.get(self.url).content.decode()

        self.assertIn('menuDeStatus', html)
        self.assertIn("this.$nextTick(() => this.posicionar())", html)

    def test_em_carregamento_oferece_as_saidas_possiveis(self):
        self._romaneio(1)

        html = self.client.get(self.url).content.decode()

        for destino in ('em_rota', 'rascunho', 'cancelado'):
            self.assertIn(f'name="status" value="{destino}"', html)
        # Nao oferece o status em que ele ja' esta'.
        self.assertNotIn('name="status" value="em_carregamento"', html)

    # ── A ordem da lista ─────────────────────────────────────────────────

    def test_a_lista_tem_ordem_definida(self):
        """
        `annotate` derruba a ordenacao padrao do Meta, e paginar sem ordem
        deixa o banco livre para devolver as linhas em ordem diferente a cada
        consulta -- a mesma carga aparece duas vezes, ou some, conforme a
        pagina.
        """
        for numero in (1, 2, 3):
            self._romaneio(numero)

        romaneios = self.client.get(self.url).context['romaneios']

        self.assertTrue(romaneios.query.order_by or romaneios.ordered)
        self.assertEqual(
            [r.numero for r in romaneios], [3, 2, 1],
            'a lista deveria vir do romaneio mais novo para o mais antigo',
        )

    # ── Os filtros ───────────────────────────────────────────────────────

    def test_os_dois_campos_de_data_dizem_qual_e_qual(self):
        """
        Eram identicos -- ambos "Selecionar data", so' com `title` no hover.
        Filtrar pelo periodo trocado devolve a lista errada em silencio.
        """
        html = self.client.get(self.url).content.decode()

        self.assertIn('for="data_ini"', html)
        self.assertIn('for="data_fim"', html)

    def test_a_tela_nao_vaza_sintaxe_de_template(self):
        self._romaneio(1)

        html = self.client.get(self.url).content.decode()

        for resto in ('{#', '#}', '{%', '%}'):
            self.assertNotIn(resto, html, 'vazou sintaxe de template no HTML')


class ExcluirRomaneioTests(TestCase):
    """
    Excluir apaga o romaneio (e as entregas junto, via cascata). Em rota ou
    entregue vira historico de operacao -- ai' a saida e' cancelar, nao
    excluir, mesma regra do Pedido de Expedicao.
    """

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Carga LTDA', nome_fantasia='Carga',
            cnpj='73345678000191', segmento='polpa_frutas',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='73345678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='exclui-carga@doca.local', nome='Carga', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )

    def setUp(self):
        self.client.force_login(self.usuario)

    def _romaneio(self, numero=1, status=RomaneioCarga.Status.RASCUNHO):
        return RomaneioCarga.objects.create(
            filial=self.filial, numero=numero, data=timezone.localdate(),
            status=status, motorista_nome='Seu Zé', veiculo_placa='ABC1D23',
        )

    def test_exclui_romaneio_em_rascunho(self):
        romaneio = self._romaneio(status=RomaneioCarga.Status.RASCUNHO)

        resposta = self.client.post(
            reverse('logistica:romaneio-delete', args=[romaneio.pk])
        )

        self.assertRedirects(resposta, reverse('logistica:romaneio-list'))
        self.assertFalse(RomaneioCarga.objects.filter(pk=romaneio.pk).exists())

    def test_exclui_romaneio_cancelado(self):
        romaneio = self._romaneio(status=RomaneioCarga.Status.CANCELADO)

        self.client.post(reverse('logistica:romaneio-delete', args=[romaneio.pk]))

        self.assertFalse(RomaneioCarga.objects.filter(pk=romaneio.pk).exists())

    def test_recusa_excluir_romaneio_em_rota(self):
        romaneio = self._romaneio(status=RomaneioCarga.Status.EM_ROTA)

        resposta = self.client.post(
            reverse('logistica:romaneio-delete', args=[romaneio.pk])
        )

        self.assertRedirects(resposta, reverse('logistica:romaneio-list'))
        self.assertTrue(RomaneioCarga.objects.filter(pk=romaneio.pk).exists())

    def test_recusa_excluir_romaneio_entregue(self):
        romaneio = self._romaneio(status=RomaneioCarga.Status.ENTREGUE)

        self.client.post(reverse('logistica:romaneio-delete', args=[romaneio.pk]))

        self.assertTrue(RomaneioCarga.objects.filter(pk=romaneio.pk).exists())

    def test_lista_mostra_botao_excluir_so_para_status_nao_bloqueados(self):
        bloqueado = self._romaneio(numero=1, status=RomaneioCarga.Status.EM_ROTA)
        liberado = self._romaneio(numero=2, status=RomaneioCarga.Status.CANCELADO)

        html = self.client.get(reverse('logistica:romaneio-list')).content.decode()

        url_bloqueado = reverse('logistica:romaneio-delete', args=[bloqueado.pk])
        url_liberado = reverse('logistica:romaneio-delete', args=[liberado.pk])
        self.assertNotIn(url_bloqueado, html)
        self.assertIn(url_liberado, html)
