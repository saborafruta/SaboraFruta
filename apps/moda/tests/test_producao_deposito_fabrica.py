"""
Fase 3 do estoque por depósito: a produção da moda consome, estorna e
reserva matéria-prima no depósito de fábrica quando a filial tem um.

Sem depósito de fábrica, `Deposito.producao_id` cai no padrão e nada muda
— por isso o resto da suíte de `test_estoque_automatico` segue igual.
"""
from decimal import Decimal

from django.urls import reverse

from apps.estoque.models import Deposito, Estoque
from apps.moda.models import RegistroCorte
from apps.moda.services.integracao import IntegracaoService
from apps.moda.services.necessidade import NecessidadeService

from apps.moda.tests.test_estoque_automatico import AutomacaoBase, HOJE


class ProducaoNoDepositoDeFabricaTests(AutomacaoBase):

    def setUp(self):
        super().setUp()
        self.client.force_login(self.usuario)
        self.fabrica = Deposito.objects.create(
            filial=self.filial, nome='Fábrica', tipo=Deposito.Tipo.PRODUCAO,
        )

    def _saldo_fabrica(self, total):
        """Coloca tecido no depósito de fábrica (o `_saldo` da base usa o padrão)."""
        Estoque.objects.update_or_create(
            produto=self.tecido, filial=self.filial, deposito=self.fabrica,
            defaults={
                'quantidade_atual': Decimal(total),
                'quantidade_reservada': Decimal('0'),
                'quantidade_disponivel': Decimal(total),
            },
        )
        from apps.moda.tests.test_estoque_automatico import LoteProduto
        from datetime import timedelta
        LoteProduto.objects.update_or_create(
            filial=self.filial, produto=self.tecido, numero_lote='L1',
            defaults={
                'quantidade_inicial': Decimal(total),
                'quantidade_atual': Decimal(total),
                'custo_unitario': Decimal('10'),
                'data_validade': HOJE + timedelta(days=60),
            },
        )

    def _salvar_corte(self, ordem, status, consumo='20'):
        return self.client.post(
            reverse('moda:corte-create'),
            {
                'ordem': ordem.pk, 'consumo_real': consumo, 'status': status,
                'data': HOJE.strftime('%Y-%m-%d'),
                'largura_tecido': '1.60', 'comprimento_encaixe': '5.00',
                'folhas': 4, 'aproveitamento': '85', 'consumo_planejado': '20',
            },
            follow=True,
        )

    def test_baixa_do_corte_sai_do_deposito_de_fabrica(self):
        self._saldo_fabrica(100)
        ordem = self._ordem()

        self._salvar_corte(ordem, RegistroCorte.Status.CORTADO)

        corte = RegistroCorte.all_objects.get(ordem=ordem)
        self.assertIsNotNone(corte.estoque_baixado_em)

        saldo_fabrica = Estoque.objects.get(
            produto=self.tecido, filial=self.filial, deposito=self.fabrica,
        )
        self.assertEqual(saldo_fabrica.quantidade_atual, Decimal('80.000'))
        # o padrão não foi tocado
        self.assertFalse(
            Estoque.objects.filter(
                produto=self.tecido, filial=self.filial,
                deposito_id=Deposito.padrao_id(self.filial.pk),
            ).exists()
        )

    def test_estorno_do_corte_volta_para_a_fabrica(self):
        self._saldo_fabrica(100)
        ordem = self._ordem()
        self._salvar_corte(ordem, RegistroCorte.Status.CORTADO)
        corte = RegistroCorte.all_objects.get(ordem=ordem)

        IntegracaoService.estornar_estoque_do_corte(corte, self.usuario)

        saldo_fabrica = Estoque.objects.get(
            produto=self.tecido, filial=self.filial, deposito=self.fabrica,
        )
        self.assertEqual(saldo_fabrica.quantidade_atual, Decimal('100.000'))

    def test_reserva_ao_iniciar_separa_no_deposito_de_fabrica(self):
        self._saldo_fabrica(100)
        ordem = self._ordem()

        NecessidadeService.reservar_ao_iniciar(ordem, self.usuario)

        saldo_fabrica = Estoque.objects.get(
            produto=self.tecido, filial=self.filial, deposito=self.fabrica,
        )
        self.assertEqual(saldo_fabrica.quantidade_reservada, Decimal('20.0000'))
