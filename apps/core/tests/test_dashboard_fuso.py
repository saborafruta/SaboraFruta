"""
Dashboard: o dia tem de ser o dia de Sao Paulo, nao o do servidor.

O bug: `timezone.now().date()` devolve a data em UTC. O container roda em UTC,
o Brasil e UTC-3, entao das 21h a meia-noite a data em UTC ja e a de amanha.
Como o lookup `__date` do Django converte a coluna para TIME_ZONE antes de
extrair a data, a consulta procurava vendas de amanha e devolvia zero.

Resultado visivel: todo dia, a partir das 21h, "Informacoes do Dia" zerava
enquanto "Informacoes do Mes" seguia certo.

Estes testes congelam o relogio nesse intervalo -- e uma janela de 3h por dia,
que passaria batido em qualquer teste rodado de manha.
"""
import datetime
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class DashboardFusoTests(TestCase):
    """21:57 em Sao Paulo = 00:57 do dia seguinte em UTC."""

    def setUp(self):
        from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario

        self.empresa = Empresa.objects.create(
            razao_social='T', cnpj='11222333000181',
            regime_tributario='simples', codigo_regime_tributario=1,
        )
        self.filial = Filial.objects.create(
            empresa=self.empresa, razao_social='Matriz', nome_fantasia='Matriz',
            cnpj='11222333000181', uf='RN', is_matriz=True,
        )
        perfil = PerfilAcesso.objects.create(
            empresa=self.empresa, nome='Admin', is_admin=True,
        )
        self.usuario = Usuario.objects.create_user(
            email='u@teste.local', nome='U', password='senha-de-teste-123',
            empresa=self.empresa, perfil=perfil, filial=self.filial,
        )

    def _venda(self, quando, valor=100):
        from apps.pdv.models import VendaPDV

        return VendaPDV.objects.create(
            filial=self.filial, numero_venda=VendaPDV.objects.count() + 1,
            usuario=self.usuario, status='finalizada',
            valor_total=Decimal(str(valor)), data_venda=quando,
        )

    @staticmethod
    def _sp(ano, mes, dia, hora, minuto=0):
        """
        Instante de Sao Paulo, devolvido em UTC.

        `timezone.now()` sempre devolve UTC; um mock que devolvesse o
        datetime ja em -03:00 esconderia justamente o bug que se quer pegar.
        """
        from zoneinfo import ZoneInfo

        return datetime.datetime(
            ano, mes, dia, hora, minuto, tzinfo=ZoneInfo('America/Sao_Paulo'),
        ).astimezone(datetime.timezone.utc)

    @classmethod
    def _noite_de_sao_paulo(cls):
        """21:57 de 01/08/2026 em Sao Paulo — 00:57 de 02/08 em UTC."""
        return cls._sp(2026, 8, 1, 21, 57)

    def _resumo(self, periodo, agora):
        from apps.core.views.dashboard import DashboardView

        with patch('django.utils.timezone.now', return_value=agora):
            return DashboardView()._vendas_periodo(self.filial, periodo)

    def test_venda_das_21h_aparece_no_dia_de_hoje(self):
        """O caso que o usuario viu: card do dia zerado com vendas no mes."""
        agora = self._noite_de_sao_paulo()
        self._venda(agora, 100)

        d = self._resumo('dia', agora)

        self.assertEqual(d['erro'], None)
        self.assertEqual(d['qtd_pedidos'], 1)
        self.assertEqual(d['valor_total'], 100.0)

    def test_dia_e_mes_batem_quando_todas_as_vendas_sao_de_hoje(self):
        """
        Era esta a incoerencia da tela: 0 no dia e 27 no mes, sendo que todas
        as vendas do mes tinham acontecido naquele mesmo dia.
        """
        agora = self._noite_de_sao_paulo()
        for _ in range(3):
            self._venda(agora, 50)

        dia = self._resumo('dia', agora)
        mes = self._resumo('mes', agora)

        self.assertEqual(dia['qtd_pedidos'], mes['qtd_pedidos'])
        self.assertEqual(dia['valor_total'], mes['valor_total'])

    def test_venda_de_ontem_a_noite_nao_entra_no_dia_de_hoje(self):
        """A correcao nao pode passar a incluir o dia anterior."""
        agora = self._noite_de_sao_paulo()
        ontem = agora - datetime.timedelta(days=1)
        self._venda(ontem, 500)
        self._venda(agora, 100)

        d = self._resumo('dia', agora)

        self.assertEqual(d['qtd_pedidos'], 1)
        self.assertEqual(d['valor_total'], 100.0)

    def test_de_manha_continua_certo(self):
        """De manha UTC e Sao Paulo estao no mesmo dia -- nao podia quebrar."""
        agora = self._sp(2026, 8, 1, 9, 0)
        self._venda(agora, 70)

        d = self._resumo('dia', agora)
        self.assertEqual(d['qtd_pedidos'], 1)


class LocaldateTests(TestCase):
    """
    `timezone.localdate()` é o idioma correto; `timezone.now().date()` não.

    Fixa a diferença para que ninguem reintroduza a versao errada achando que
    sao equivalentes -- elas so divergem 3h por dia.
    """

    def test_localdate_segue_o_fuso_do_projeto(self):
        from zoneinfo import ZoneInfo

        agora = datetime.datetime(
            2026, 8, 1, 21, 57, tzinfo=ZoneInfo('America/Sao_Paulo'),
        ).astimezone(datetime.timezone.utc)

        with patch('django.utils.timezone.now', return_value=agora):
            self.assertEqual(timezone.localdate(), datetime.date(2026, 8, 1))
            # A forma antiga daria 02/08: e essa a diferenca.
            self.assertEqual(timezone.now().date(), datetime.date(2026, 8, 2))
