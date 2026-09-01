from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

from django.shortcuts import render
from django.test import RequestFactory, TestCase, override_settings

from apps.analytics.views import dashboards
from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.financeiro.models import FormaPagamento
from apps.financeiro.models.receber_pagar import ContaReceber, PagamentoContaReceber
from apps.moda.models import ItemPedidoProducao, OrdemProducao, PedidoProducao
from apps.pdv.models import PagamentoVendaPDV, VendaPDV


@override_settings(TIME_ZONE='America/Sao_Paulo')
class HistoricoVendasFinanceiroTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        empresa = Empresa.objects.create(
            razao_social='Empresa Teste', cnpj='11222333000181',
            regime_tributario='simples', codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=empresa, razao_social='Matriz', nome_fantasia='Matriz',
            cnpj=empresa.cnpj, uf='RN', is_matriz=True,
        )
        perfil = PerfilAcesso.objects.create(empresa=empresa, nome='Admin', is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email='historico@example.test', nome='Operador', password='teste',
            empresa=empresa, filial=cls.filial, perfil=perfil, is_superuser=True,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Cliente A', cpf_cnpj='12345678901',
        )
        cls.dinheiro = FormaPagamento.objects.create(
            empresa=empresa, filial=cls.filial, descricao='Dinheiro', tipo='dinheiro',
        )
        cls.boleto = FormaPagamento.objects.create(
            empresa=empresa, filial=cls.filial, descricao='Boleto', tipo='boleto',
        )
        cls.venda_aberta = VendaPDV.objects.create(
            filial=cls.filial, cliente=cls.cliente, usuario=cls.usuario,
            numero_venda=10, status='finalizada', valor_total=Decimal('100.00'),
            valor_pago=Decimal('100.00'),
            data_venda=datetime(2026, 8, 31, 14, 0, tzinfo=dt_timezone.utc),
        )
        PagamentoVendaPDV.objects.create(
            venda_pdv=cls.venda_aberta, forma_pagamento=cls.dinheiro, valor=Decimal('40.00'),
        )
        PagamentoVendaPDV.objects.create(
            venda_pdv=cls.venda_aberta, forma_pagamento=cls.boleto, valor=Decimal('60.00'),
        )
        cls.conta = ContaReceber.objects.create(
            filial=cls.filial, cliente=cls.cliente, documento_tipo='venda_pdv',
            documento_id=cls.venda_aberta.pk, documento_numero='10',
            valor_original=Decimal('60.00'), valor_final=Decimal('60.00'),
            valor_pago=Decimal('20.00'), valor_saldo=Decimal('40.00'),
            data_emissao=date(2026, 8, 31), data_vencimento=date(2026, 9, 20),
            forma_pagamento=cls.boleto, status='pago_parcial',
        )
        PagamentoContaReceber.objects.create(
            filial=cls.filial, conta_receber=cls.conta,
            data_pagamento=date(2026, 9, 5), valor_pago=Decimal('20.00'),
            forma_pagamento=cls.dinheiro, usuario=cls.usuario,
        )
        cls.venda_paga = VendaPDV.objects.create(
            filial=cls.filial, cliente=cls.cliente, usuario=cls.usuario,
            numero_venda=11, status='finalizada', valor_total=Decimal('50.00'),
            valor_pago=Decimal('50.00'),
            data_venda=datetime(2026, 8, 31, 15, 0, tzinfo=dt_timezone.utc),
        )
        PagamentoVendaPDV.objects.create(
            venda_pdv=cls.venda_paga, forma_pagamento=cls.dinheiro, valor=Decimal('50.00'),
        )
        cls.pedido_op = PedidoProducao.objects.create(
            filial=cls.filial, cliente=cls.cliente, numero=30,
            data_pedido=date(2026, 8, 31), status=PedidoProducao.Status.CONFIRMADO,
            forma_pagamento=cls.boleto,
            financeiro_gerado_em=datetime(2026, 8, 31, 12, 0, tzinfo=dt_timezone.utc),
        )
        item_op = ItemPedidoProducao.objects.create(
            pedido=cls.pedido_op, descricao='Uniformes', quantidade=10,
            valor_unitario=Decimal('50.00'),
        )
        for sequencial in (30, 31):
            OrdemProducao.objects.create(
                filial=cls.filial, numero='', ano=2026, sequencial=sequencial,
                pedido=cls.pedido_op, item=item_op, quantidade=5,
                emitida_por=cls.usuario,
            )
        cls.op_conta_paga = ContaReceber.objects.create(
            filial=cls.filial, cliente=cls.cliente, documento_tipo='pedido_moda',
            documento_id=cls.pedido_op.pk, documento_numero='30', parcela=1,
            total_parcelas=2, valor_original=Decimal('200.00'),
            valor_final=Decimal('200.00'), valor_pago=Decimal('200.00'),
            valor_saldo=Decimal('0.00'), data_emissao=date(2026, 8, 31),
            data_vencimento=date(2026, 8, 31), forma_pagamento=cls.boleto,
            status='pago',
        )
        PagamentoContaReceber.objects.create(
            filial=cls.filial, conta_receber=cls.op_conta_paga,
            data_pagamento=date(2026, 8, 31), valor_pago=Decimal('200.00'),
            forma_pagamento=cls.dinheiro, usuario=cls.usuario,
        )
        cls.op_conta_aberta = ContaReceber.objects.create(
            filial=cls.filial, cliente=cls.cliente, documento_tipo='pedido_moda',
            documento_id=cls.pedido_op.pk, documento_numero='30', parcela=2,
            total_parcelas=2, valor_original=Decimal('300.00'),
            valor_final=Decimal('300.00'), valor_pago=Decimal('0.00'),
            valor_saldo=Decimal('300.00'), data_emissao=date(2026, 8, 31),
            data_vencimento=date(2026, 9, 20), forma_pagamento=cls.boleto,
            status='aberto',
        )

    def request(self, path='/analytics/vendas/', **params):
        request = RequestFactory().get(path, params)
        request.user = self.usuario
        request.filial = self.filial
        request.filial_ativa = self.filial
        request.session = {}
        return request

    def contexto_lista(self, **params):
        request = self.request(data_ini='2026-08-31', data_fim='2026-08-31', **params)
        with patch('apps.analytics.views.dashboards.render', wraps=render) as rendering:
            response = dashboards.historico_vendas(request)
        return response, rendering.call_args.args[2]

    def test_lista_exibe_status_saldo_e_todos_os_cabecalhos_ordenaveis(self):
        response, contexto = self.contexto_lista()
        self.assertContains(response, 'Status')
        self.assertContains(response, 'Nome')
        self.assertContains(response, 'Valor restante')
        self.assertContains(response, '>Pago<')
        self.assertContains(response, 'Em aberto')
        self.assertNotContains(response, self.cliente.cpf_cnpj)
        self.assertContains(response, 'Ver pagamentos e recebimentos desta venda')
        self.assertEqual(response.content.count(b'class="hv-sort"'), 9)
        vendas = {(v.origem_historico, v.pk): v for v in contexto['page_obj']}
        self.assertEqual(vendas[('pdv', self.venda_aberta.pk)].saldo_restante, Decimal('40.00'))
        self.assertEqual(vendas[('pdv', self.venda_paga.pk)].saldo_restante, Decimal('0.00'))
        self.assertEqual(vendas[('op', self.pedido_op.pk)].saldo_restante, Decimal('300.00'))
        self.assertContains(response, '>OP<')

    def test_ordenacao_por_saldo_alterna_crescente_e_decrescente(self):
        _, contexto = self.contexto_lista(ordem='saldo')
        self.assertEqual(contexto['page_obj'][0].pk, self.venda_paga.pk)
        _, contexto = self.contexto_lista(ordem='-saldo')
        self.assertEqual(contexto['page_obj'][0].origem_historico, 'op')
        self.assertEqual(contexto['page_obj'][0].pk, self.pedido_op.pk)

    def test_todas_as_colunas_de_dados_aceitam_ordenacao_nos_dois_sentidos(self):
        for chave in dashboards.ORDENACAO_HISTORICO_VENDAS:
            for ordem in (chave, f'-{chave}'):
                with self.subTest(ordem=ordem):
                    _, contexto = self.contexto_lista(ordem=ordem)
                    self.assertEqual(contexto['ordem'], ordem)
                    self.assertEqual(len(contexto['page_obj']), 3)

    def test_filtro_op_exibe_pedido_e_total_financeiro_sem_duplicar(self):
        response, contexto = self.contexto_lista(tipo_venda='op')
        self.assertEqual(len(contexto['page_obj']), 1)
        registro = contexto['page_obj'][0]
        self.assertEqual(registro.origem_historico, 'op')
        self.assertEqual(registro.valor_total, Decimal('500.00'))
        self.assertEqual(registro.saldo_restante, Decimal('300.00'))
        self.assertEqual(registro.op_numeros, ['OP-2026-000031', 'OP-2026-000030'])
        self.assertEqual(contexto['valor_totalizador'], Decimal('500.00'))
        self.assertContains(response, 'Não se aplica à OP')

    def test_op_so_aparece_depois_do_clique_em_gerar_financeiro(self):
        self.pedido_op.financeiro_gerado_em = None
        self.pedido_op.save(update_fields=['financeiro_gerado_em'])

        _, contexto = self.contexto_lista(tipo_venda='op')

        self.assertEqual(len(contexto['page_obj']), 0)
        self.assertEqual(contexto['valor_totalizador'], Decimal('0.00'))

    def test_op_totalmente_paga_continua_no_historico(self):
        self.op_conta_aberta.valor_pago = Decimal('300.00')
        self.op_conta_aberta.valor_saldo = Decimal('0.00')
        self.op_conta_aberta.status = 'pago'
        self.op_conta_aberta.save(update_fields=['valor_pago', 'valor_saldo', 'status'])

        response, contexto = self.contexto_lista(tipo_venda='op')

        self.assertEqual(len(contexto['page_obj']), 1)
        registro = contexto['page_obj'][0]
        self.assertEqual(registro.saldo_restante, Decimal('0.00'))
        self.assertEqual(registro.status_financeiro_ordem, 0)
        self.assertContains(response, '>Pago<')

    def test_sobreposicao_financeira_da_op_mostra_recebido_saldo_e_quitacao(self):
        response = dashboards.historico_op_financeiro(
            self.request(f'/analytics/vendas/op/{self.pedido_op.pk}/financeiro/'),
            self.pedido_op.pk,
        )
        self.assertContains(response, 'OP #000030')
        self.assertContains(response, 'R$ 200,00')
        self.assertContains(response, 'R$ 300,00')
        self.assertContains(response, 'Quitar / receber')

    def test_sobreposicao_mostra_pago_pendente_historico_e_opcao_de_quitar(self):
        response = dashboards.historico_venda_financeiro(
            self.request(f'/analytics/vendas/{self.venda_aberta.pk}/financeiro/'),
            self.venda_aberta.pk,
        )
        self.assertContains(response, 'Já recebido')
        self.assertContains(response, 'Valor restante')
        self.assertContains(response, 'Quitar / receber')
        self.assertContains(response, 'Título #')
        self.assertContains(response, '05/09/2026')

    def test_relatorio_pdf_inclui_situacao_financeira_e_valor_restante(self):
        response = dashboards.historico_vendas_relatorio(
            self.request('/analytics/vendas/relatorio/', data_ini='2026-08-31', data_fim='2026-08-31'),
        )
        self.assertContains(response, 'Status')
        self.assertContains(response, 'Valor restante')
        self.assertContains(response, 'Pago')
        self.assertContains(response, 'Em aberto')
        self.assertContains(response, 'Total OP')
        self.assertContains(response, '>OP<')
