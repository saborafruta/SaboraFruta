"""
O formulario de manifesto de carga.

Dezessete campos numa tela so'. O que decide se ela funciona nao e' o que
aparece, e' o que NAO aparece: campo sem rotulo, data que chega em branco e
opcao vazia escrita "---------".
"""
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.logistica.forms import ManifestoCargaForm
from apps.logistica.models import ManifestoCarga


class FormularioDeManifestoTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Manifesto LTDA', nome_fantasia='Manifesto',
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
            email='man@carga.local', nome='Man', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        self.url = reverse('logistica:manifesto-create')

    # ── A data que chegava em branco ─────────────────────────────────────

    def test_a_emissao_chega_preenchida_com_hoje(self):
        """
        Com `pt-br`, o Django renderiza a data como 27/08/2026 -- e
        `<input type="date">` so' aceita o formato ISO, descartando o resto e
        mostrando o campo VAZIO. A emissao nasce com a data de hoje e chegava
        em branco; quem nao reparasse levava erro de campo obrigatorio.
        """
        html = self.client.get(self.url).content.decode()
        hoje = timezone.localdate().isoformat()

        self.assertIn(f'name="data_emissao" value="{hoje}"', html)

    def test_a_data_gravada_volta_no_campo_ao_editar(self):
        """Editar um manifesto nao pode apagar a data que ele ja' tinha."""
        manifesto = ManifestoCarga.objects.create(
            filial=self.filial, numero=1,
            data_emissao='2026-03-15', data_saida='2026-03-16',
        )

        form = ManifestoCargaForm(instance=manifesto, filial=self.filial)

        self.assertIn('value="2026-03-15"', str(form['data_emissao']))
        self.assertIn('value="2026-03-16"', str(form['data_saida']))

    # ── Os campos que não diziam o que eram ──────────────────────────────

    def test_todo_campo_de_texto_tem_rotulo_na_tela(self):
        """
        O nome do motorista e a descricao do veiculo ficavam soltos logo abaixo
        do seletor, sem rotulo: duas caixas vazias no meio do formulario, e
        ninguem sabia se precisava preencher.
        """
        html = self.client.get(self.url).content.decode()

        for campo in ('motorista_nome', 'motorista_documento',
                      'veiculo_placa', 'veiculo_descricao'):
            self.assertIn(f'id="id_{campo}"', html, f'{campo} sumiu da tela')
            self.assertIn(
                ManifestoCargaForm.PLACEHOLDERS[campo], html,
                f'{campo} continua sem dizer o que espera',
            )

    def test_a_opcao_vazia_diz_o_que_significa(self):
        """"---------" nao informa que o campo e' opcional."""
        html = self.client.get(self.url).content.decode()

        self.assertNotIn('---------', html)
        self.assertIn('— sem romaneio —', html)
        self.assertIn('— sem transportadora —', html)

    def test_a_tela_esta_dividida_em_secoes(self):
        """
        Dezessete campos numa lista unica: quem preenche precisava reler tudo
        para achar onde tinha parado.
        """
        html = self.client.get(self.url).content.decode()

        for secao in ('Identificação', 'Quem leva', 'Por onde passa', 'Observações'):
            self.assertIn(secao, html)

    def test_uf_e_placa_sao_gravadas_em_maiuscula(self):
        """
        `text-transform` no campo e' so' pintura: mostra "RN" e envia "rn". O
        documento fiscal sairia com a sigla em minuscula, e a mesma placa
        gravada de dois jeitos nao casaria numa busca.
        """
        form = ManifestoCargaForm(
            {
                'numero': '3',
                'data_emissao': timezone.localdate().isoformat(),
                'status': ManifestoCarga.Status.RASCUNHO,
                'modal': ManifestoCarga.Modal.RODOVIARIO,
                'uf_origem': 'rn', 'uf_destino': ' pb ',
                'veiculo_placa': 'abc1d23',
            },
            filial=self.filial,
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['uf_origem'], 'RN')
        self.assertEqual(form.cleaned_data['uf_destino'], 'PB')
        self.assertEqual(form.cleaned_data['veiculo_placa'], 'ABC1D23')

    def test_os_rotulos_do_modal_estao_acentuados(self):
        html = self.client.get(self.url).content.decode()

        self.assertIn('Rodoviário', html)
        self.assertIn('Aéreo', html)

    def test_a_tela_nao_vaza_sintaxe_de_template(self):
        html = self.client.get(self.url).content.decode()

        for resto in ('{#', '#}', '{%', '%}'):
            self.assertNotIn(resto, html, 'vazou sintaxe de template no HTML')

    # ── Continua salvando ────────────────────────────────────────────────

    def test_o_formulario_salva(self):
        resposta = self.client.post(self.url, {
            'numero': '7',
            'data_emissao': timezone.localdate().isoformat(),
            'status': ManifestoCarga.Status.RASCUNHO,
            'modal': ManifestoCarga.Modal.RODOVIARIO,
            'motorista_nome': 'Seu Zé',
            'veiculo_placa': 'ABC1D23',
            'cidade_origem': 'Natal', 'uf_origem': 'RN',
            'cidade_destino': 'Mossoró', 'uf_destino': 'RN',
        })

        self.assertEqual(resposta.status_code, 302)
        manifesto = ManifestoCarga.objects.get(numero=7)
        self.assertEqual(manifesto.motorista_nome, 'Seu Zé')
        self.assertEqual(manifesto.data_emissao, timezone.localdate())
