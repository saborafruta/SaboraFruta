"""
A tela da viagem.

O que ela precisa acertar não é o layout: é não deixar criar viagem que a
operação não sabe executar — sem quem leve, com volta antes da saída, ou já
marcada como finalizada sem nunca ter saído.
"""
from datetime import date, time, timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.models import Motorista, Veiculo
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.logistica.forms_viagem import ViagemForm
from apps.logistica.models import Viagem


class TelaDaViagemTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Viagem LTDA', nome_fantasia='Viagem',
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
            email='tela@viagem.local', nome='Tela', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.motorista = Motorista.objects.create(
            filial=cls.filial, nome='João da Silva', cpf='12345678901',
        )
        cls.veiculo = Veiculo.objects.create(
            filial=cls.filial, placa='ABC1D23', marca='Volvo', modelo='FH 460',
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        self.url = reverse('logistica:viagem-create')

    def _dados(self, **extras):
        base = {
            'data_saida': timezone.localdate().isoformat(),
            'motorista_nome': 'Seu Zé',
            'veiculo_placa': 'abc1d23',
            'status': Viagem.Status.RASCUNHO,
        }
        base.update(extras)
        return base

    # ── A tela ───────────────────────────────────────────────────────────

    def test_a_tela_abre_com_todos_os_campos_pedidos(self):
        html = self.client.get(self.url).content.decode()

        for campo in (
            'data_saida', 'hora_saida', 'previsao_retorno', 'motorista',
            'motorista_nome', 'veiculo', 'veiculo_placa', 'uf_origem',
            'uf_destino', 'rota', 'observacao', 'responsavel', 'status',
        ):
            self.assertIn(f'id="id_{campo}"', html, f'{campo} não está na tela')

    def test_o_numero_e_mostrado_e_nao_pedido(self):
        """
        Deixar digitar produz número repetido, que só bate na unique depois de
        a pessoa já ter preenchido tudo.
        """
        html = self.client.get(self.url).content.decode()

        self.assertNotIn('id="id_numero"', html)
        self.assertIn('#000001', html)

    def test_a_filial_vem_da_sessao_e_nao_e_escolhida(self):
        """
        Escolher abriria a porta para criar viagem na unidade errada, e o erro
        só aparece quando a carga não bate com o estoque de lá.
        """
        html = self.client.get(self.url).content.decode()

        self.assertNotIn('id="id_filial"', html)
        self.assertIn(str(self.filial), html)

    def test_a_data_de_saida_chega_no_formato_que_o_navegador_aceita(self):
        """
        Com pt-br o Django renderiza 27/08/2026, e `<input type="date">`
        descarta o que não for ISO — mostrando o campo vazio.
        """
        viagem = Viagem.objects.create(
            filial=self.filial, numero=9, data_saida=date(2026, 3, 15),
            previsao_retorno=date(2026, 3, 20),
        )

        form = ViagemForm(instance=viagem, filial=self.filial)

        self.assertIn('value="2026-03-15"', str(form['data_saida']))
        self.assertIn('value="2026-03-20"', str(form['previsao_retorno']))

    def test_a_tela_nao_vaza_sintaxe_de_template(self):
        html = self.client.get(self.url).content.decode()

        for resto in ('{#', '#}', '{%', '%}'):
            self.assertNotIn(resto, html, 'vazou sintaxe de template no HTML')

    # ── O que ela recusa ─────────────────────────────────────────────────

    def test_viagem_sem_quem_leve_nao_e_criada(self):
        """
        Sem motorista nem veículo a viagem sai sem identificar quem levou, e o
        MDF-e não tem o que declarar.
        """
        resposta = self.client.post(self.url, self._dados(
            motorista_nome='', veiculo_placa='',
        ))

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(Viagem.objects.count(), 0)

    def test_volta_antes_da_saida_e_recusada(self):
        hoje = timezone.localdate()
        resposta = self.client.post(self.url, self._dados(
            data_saida=hoje.isoformat(),
            previsao_retorno=(hoje - timedelta(days=1)).isoformat(),
        ))

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(Viagem.objects.count(), 0)

    def test_a_tela_de_criacao_so_oferece_as_etapas_iniciais(self):
        """
        Uma viagem que ainda não existe não pode nascer "Em trânsito" nem
        "Finalizada".
        """
        form = ViagemForm(filial=self.filial)

        oferecidos = {valor for valor, _ in form.fields['status'].choices}
        self.assertEqual(
            oferecidos, {Viagem.Status.RASCUNHO, Viagem.Status.EM_PREPARACAO},
        )

    def test_ao_editar_o_status_so_oferece_o_atual_e_os_proximos(self):
        """
        Sem isso alguém marca "Finalizada" numa viagem que nunca saiu, e a
        prestação de contas deixa de significar coisa alguma.
        """
        viagem = Viagem.objects.create(
            filial=self.filial, numero=3, motorista_nome='Zé',
            status=Viagem.Status.RASCUNHO,
        )

        form = ViagemForm(instance=viagem, filial=self.filial)

        oferecidos = {valor for valor, _ in form.fields['status'].choices}
        self.assertIn(Viagem.Status.EM_PREPARACAO, oferecidos)
        self.assertNotIn(Viagem.Status.FINALIZADA, oferecidos)
        self.assertNotIn(Viagem.Status.EM_TRANSITO, oferecidos)

    # ── O que ela grava ──────────────────────────────────────────────────

    def test_criar_gera_o_numero_e_leva_para_a_viagem(self):
        resposta = self.client.post(self.url, self._dados())

        viagem = Viagem.objects.get()
        self.assertEqual(viagem.numero, 1)
        self.assertRedirects(
            resposta, reverse('logistica:viagem-detail', args=[viagem.pk]),
        )

    def test_a_placa_e_a_uf_vao_em_maiuscula(self):
        """
        `text-transform` no campo é só pintura: mostra "RN" e envia "rn", e a
        mesma placa gravada de dois jeitos não casa numa busca.
        """
        self.client.post(self.url, self._dados(
            veiculo_placa='abc1d23', uf_origem='rn', uf_destino=' pb ',
        ))

        viagem = Viagem.objects.get()
        self.assertEqual(viagem.veiculo_placa, 'ABC1D23')
        self.assertEqual(viagem.uf_origem, 'RN')
        self.assertEqual(viagem.uf_destino, 'PB')

    def test_escolher_do_cadastro_preenche_nome_e_placa(self):
        self.client.post(self.url, self._dados(
            motorista=self.motorista.pk, motorista_nome='',
            veiculo=self.veiculo.pk, veiculo_placa='',
        ))

        viagem = Viagem.objects.get()
        self.assertEqual(viagem.motorista_nome, 'João da Silva')
        self.assertEqual(viagem.motorista_documento, '12345678901')
        self.assertEqual(viagem.veiculo_placa, 'ABC1D23')
        self.assertIn('Volvo', viagem.veiculo_descricao)

    def test_o_texto_digitado_vence_o_cadastro(self):
        """Placa de reboque, motorista substituto — quem digitou teve um motivo."""
        self.client.post(self.url, self._dados(
            motorista=self.motorista.pk, motorista_nome='João (substituto)',
        ))

        self.assertEqual(Viagem.objects.get().motorista_nome, 'João (substituto)')

    def test_a_uf_de_origem_cai_para_a_da_filial(self):
        self.client.post(self.url, self._dados(uf_origem=''))

        self.assertEqual(Viagem.objects.get().uf_origem, 'RN')

    def test_a_hora_de_saida_e_gravada(self):
        self.client.post(self.url, self._dados(hora_saida='06:30'))

        self.assertEqual(Viagem.objects.get().hora_saida, time(6, 30))

    # ── Andar no ciclo pela tela ─────────────────────────────────────────

    def test_a_tela_move_a_viagem_uma_etapa(self):
        viagem = Viagem.objects.create(
            filial=self.filial, numero=5, motorista_nome='Zé',
        )

        self.client.post(
            reverse('logistica:viagem-status', args=[viagem.pk]),
            {'status': Viagem.Status.EM_PREPARACAO},
        )

        viagem.refresh_from_db()
        self.assertEqual(viagem.status, Viagem.Status.EM_PREPARACAO)

    def test_a_tela_recusa_o_salto_e_diz_para_onde_da_para_ir(self):
        viagem = Viagem.objects.create(
            filial=self.filial, numero=6, motorista_nome='Zé',
        )

        resposta = self.client.post(
            reverse('logistica:viagem-status', args=[viagem.pk]),
            {'status': Viagem.Status.FINALIZADA}, follow=True,
        )

        viagem.refresh_from_db()
        self.assertEqual(viagem.status, Viagem.Status.RASCUNHO)
        avisos = [str(m) for m in resposta.context['messages']]
        self.assertTrue(any('não vai direto para' in a for a in avisos), avisos)

    def test_viagem_de_outra_filial_nao_abre(self):
        outra = Filial.objects.create(
            empresa=self.empresa, razao_social='Segunda',
            cnpj='31345678000677', uf='RN', cidade='Mossoro',
        )
        alheia = Viagem.objects.create(filial=outra, numero=1, motorista_nome='Zé')

        resposta = self.client.get(reverse('logistica:viagem-detail', args=[alheia.pk]))

        self.assertEqual(resposta.status_code, 404)
