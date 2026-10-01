from datetime import datetime, time, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase
from django.utils import timezone

from apps.cadastros.models import Funcionario
from apps.core.models import Empresa, Filial
from apps.produtos.models import Produto, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial
from apps.pdv.views.pdv import _agendamento_para_checkout

from apps.agenda.models import Agendamento, BloqueioAgenda, JornadaTrabalho, ProfissionalAgenda, ProfissionalServico
from apps.agenda.services import criar_agendamento, listar_horarios


class DisponibilidadeAgendaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        hoje = timezone.localdate()
        cls.data_teste = hoje + timedelta(days=(7 - hoje.weekday()))
        cls.empresa = Empresa.objects.create(
            razao_social='Barbearia Agenda LTDA', nome_fantasia='Barbearia Agenda',
            cnpj='22345678000191', regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Barbearia Agenda', nome_fantasia='Barbearia',
            cnpj='22345678000192', uf='CE',
        )
        cls.unidade = UnidadeMedida.objects.create(empresa=cls.empresa, sigla='SV', descricao='Serviço')
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        cls.funcionario = Funcionario.objects.create(filial=cls.filial, nome='Carlos Barbeiro')
        cls.profissional = ProfissionalAgenda.objects.create(filial=cls.filial, funcionario=cls.funcionario)
        cls.corte = Produto.objects.create(
            filial=cls.filial, unidade_medida=cls.unidade, descricao='Corte', ncm='00000000',
            tipo_produto=Produto.TipoProduto.SERVICO, agendavel=True,
            duracao_servico_minutos=30, intervalo_apos_servico_minutos=10,
            preco_venda=Decimal('40.00'),
        )
        cls.barba = Produto.objects.create(
            filial=cls.filial, unidade_medida=cls.unidade, descricao='Barba', ncm='00000000',
            tipo_produto=Produto.TipoProduto.SERVICO, agendavel=True,
            duracao_servico_minutos=15, intervalo_apos_servico_minutos=5,
            preco_venda=Decimal('20.00'),
        )
        for servico in (cls.corte, cls.barba):
            ProdutoFilial.objects.create(produto=servico, filial=cls.filial)
            ProfissionalServico.objects.create(profissional=cls.profissional, servico=servico)
        JornadaTrabalho.objects.create(
            filial=cls.filial, profissional=cls.profissional, dia_semana=0,
            inicio=time(8), fim=time(12), pausa_inicio=time(10), pausa_fim=time(10, 30),
        )

    def inicio(self, hora, minuto=0):
        return timezone.make_aware(datetime.combine(self.data_teste, time(hora, minuto)))

    def test_soma_duracao_de_varios_servicos_e_preserva_snapshot(self):
        agendamento = criar_agendamento(
            filial=self.filial, profissional=self.profissional,
            servicos=[self.corte, self.barba], inicio=self.inicio(8),
            pessoa_atendida_nome='João',
        )

        self.assertEqual(agendamento.fim, self.inicio(8, 45))
        self.assertEqual(agendamento.fim_com_intervalo, self.inicio(8, 55))
        self.assertEqual(agendamento.valor_total, Decimal('60.00'))
        self.assertEqual(list(agendamento.itens.values_list('duracao_minutos', flat=True)), [30, 15])

    def test_nao_oferece_horarios_durante_pausa_ou_agendamento(self):
        criar_agendamento(
            filial=self.filial, profissional=self.profissional,
            servicos=[self.corte], inicio=self.inicio(8), pessoa_atendida_nome='Ana',
        )

        horarios = listar_horarios(self.profissional, [self.corte], self.inicio(8).date())
        valores = [timezone.localtime(item).strftime('%H:%M') for item in horarios]

        self.assertNotIn('08:00', valores)
        self.assertNotIn('08:30', valores)
        self.assertNotIn('10:00', valores)
        self.assertIn('10:30', valores)

    def test_impede_conflito_na_confirmacao(self):
        criar_agendamento(
            filial=self.filial, profissional=self.profissional,
            servicos=[self.corte], inicio=self.inicio(9), pessoa_atendida_nome='Ana',
        )

        with self.assertRaises(ValidationError):
            criar_agendamento(
                filial=self.filial, profissional=self.profissional,
                servicos=[self.barba], inicio=self.inicio(9, 15), pessoa_atendida_nome='Bia',
            )

    def test_bloqueio_geral_remove_horarios_do_periodo(self):
        BloqueioAgenda.objects.create(
            filial=self.filial, inicio=self.inicio(8, 30), fim=self.inicio(9, 30),
            motivo='Reunião da equipe',
        )

        valores = [
            timezone.localtime(item).strftime('%H:%M')
            for item in listar_horarios(self.profissional, [self.barba], self.inicio(8).date())
        ]

        self.assertIn('08:00', valores)
        self.assertNotIn('08:30', valores)
        self.assertNotIn('09:15', valores)
        self.assertIn('09:30', valores)

    def test_cancelamento_libera_horario(self):
        agendamento = criar_agendamento(
            filial=self.filial, profissional=self.profissional,
            servicos=[self.corte], inicio=self.inicio(8), pessoa_atendida_nome='Ana',
        )
        agendamento.status = Agendamento.Status.CANCELADO
        agendamento.save(update_fields=['status'])

        valores = [
            timezone.localtime(item).strftime('%H:%M')
            for item in listar_horarios(self.profissional, [self.corte], self.inicio(8).date())
        ]

        self.assertIn('08:00', valores)

    def test_nao_permite_profissional_de_outra_filial(self):
        outra_filial = Filial.objects.create(
            empresa=self.empresa, razao_social='Outra Unidade', nome_fantasia='Outra Unidade',
            cnpj='22345678000193', uf='CE',
        )

        with self.assertRaises(ValidationError):
            criar_agendamento(
                filial=outra_filial, profissional=self.profissional,
                servicos=[self.corte], inicio=self.inicio(8), pessoa_atendida_nome='Ana',
            )

    def test_checkout_recebe_servicos_do_agendamento(self):
        agendamento = criar_agendamento(
            filial=self.filial, profissional=self.profissional,
            servicos=[self.corte, self.barba], inicio=self.inicio(8),
            pessoa_atendida_nome='Ana',
        )
        request = RequestFactory().get('/pdv/checkout/', {'agendamento': agendamento.pk})
        request.filial_ativa = self.filial

        payload = _agendamento_para_checkout(request)

        self.assertEqual(payload['id'], agendamento.pk)
        self.assertEqual([item['id'] for item in payload['itens']], [self.corte.pk, self.barba.pk])
        self.assertEqual([item['quantidade'] for item in payload['itens']], [1, 1])
