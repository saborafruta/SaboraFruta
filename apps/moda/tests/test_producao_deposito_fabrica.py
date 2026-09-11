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


class RoteamentoPorTipoDeMaterialTests(AutomacaoBase):
    """Com dois depósitos de produção configurados por tipo, tecido e
    aviamento (zíper) vão cada um para o seu -- não disputam um só."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.usuario)
        self.tecidos = Deposito.objects.create(
            filial=self.filial, nome='Tecidos', tipo=Deposito.Tipo.PRODUCAO,
            tipos_material=['tecido_principal'],
        )
        self.aviamentos = Deposito.objects.create(
            filial=self.filial, nome='Aviamentos', tipo=Deposito.Tipo.PRODUCAO,
            tipos_material=['ziper', 'aviamento'],
        )

    def _ordem_com_ziper(self, consumo_tecido='2', consumo_ziper='1', quantidade=10, numero=1):
        from apps.produtos.models import Produto
        from apps.moda.models import (
            FichaTecnica, ItemPedidoProducao, MaterialFicha, OrdemProducao,
            PedidoProducao, ProdutoModa,
        )

        self.ziper = Produto.objects.create(
            filial=self.filial, codigo='ZIP001', descricao='Zíper 20cm',
            unidade_medida=self.unidade, controla_lote=False,
        )
        produto_moda = ProdutoModa.objects.create(
            filial=self.filial, codigo=f'CAM{numero:03d}', nome='Camisa',
        )
        ficha = FichaTecnica.objects.create(filial=self.filial, produto=produto_moda)
        MaterialFicha.objects.create(
            ficha=ficha, tipo=MaterialFicha.Tipo.TECIDO_PRINCIPAL,
            descricao='Malha PV', consumo=Decimal(consumo_tecido),
            produto_estoque=self.tecido,
        )
        MaterialFicha.objects.create(
            ficha=ficha, tipo=MaterialFicha.Tipo.ZIPER,
            descricao='Zíper 20cm', consumo=Decimal(consumo_ziper),
            produto_estoque=self.ziper,
        )
        pedido = PedidoProducao.objects.create(
            filial=self.filial, cliente=self.cliente, numero=numero,
        )
        item = ItemPedidoProducao.objects.create(
            pedido=pedido, produto=produto_moda, descricao='Camisa',
            quantidade=quantidade, valor_unitario=Decimal('45'),
        )
        return OrdemProducao.objects.create(
            filial=self.filial, pedido=pedido, item=item,
            numero=f'OP-{numero:04d}', ano=2026, sequencial=numero,
            quantidade=quantidade,
        )

    def _saldo(self, produto, deposito, total):
        Estoque.objects.update_or_create(
            produto=produto, filial=self.filial, deposito=deposito,
            defaults={
                'quantidade_atual': Decimal(total),
                'quantidade_reservada': Decimal('0'),
                'quantidade_disponivel': Decimal(total),
            },
        )
        if produto.controla_lote:
            from datetime import timedelta
            from apps.moda.tests.test_estoque_automatico import LoteProduto
            LoteProduto.objects.update_or_create(
                filial=self.filial, produto=produto, numero_lote='L1',
                defaults={
                    'quantidade_inicial': Decimal(total),
                    'quantidade_atual': Decimal(total),
                    'custo_unitario': Decimal('10'),
                    'data_validade': HOJE + timedelta(days=60),
                },
            )

    def test_reserva_automatica_separa_tecido_e_ziper_em_depositos_diferentes(self):
        ordem = self._ordem_com_ziper()
        self._saldo(self.tecido, self.tecidos, 100)
        self._saldo(self.ziper, self.aviamentos, 50)

        NecessidadeService.reservar_da_ordem(ordem, self.usuario)

        tecido_estoque = Estoque.objects.get(
            produto=self.tecido, filial=self.filial, deposito=self.tecidos,
        )
        ziper_estoque = Estoque.objects.get(
            produto=self.ziper, filial=self.filial, deposito=self.aviamentos,
        )
        self.assertEqual(tecido_estoque.quantidade_reservada, Decimal('20.0000'))
        self.assertEqual(ziper_estoque.quantidade_reservada, Decimal('10.0000'))
        # não vazou pro depósito do outro tipo
        self.assertFalse(
            Estoque.objects.filter(
                produto=self.ziper, filial=self.filial, deposito=self.tecidos,
            ).exists()
        )
