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
        vendas = {v.pk: v for v in contexto['page_obj']}
        self.assertEqual(vendas[self.venda_aberta.pk].saldo_restante, Decimal('40.00'))
        self.assertEqual(vendas[self.venda_paga.pk].saldo_restante, Decimal('0.00'))

    def test_ordenacao_por_saldo_alterna_crescente_e_decrescente(self):
        _, contexto = self.contexto_lista(ordem='saldo')
        self.assertEqual(contexto['page_obj'][0].pk, self.venda_paga.pk)
        _, contexto = self.contexto_lista(ordem='-saldo')
        self.assertEqual(contexto['page_obj'][0].pk, self.venda_aberta.pk)

    def test_todas_as_colunas_de_dados_aceitam_ordenacao_nos_dois_sentidos(self):
        for chave in dashboards.ORDENACAO_HISTORICO_VENDAS:
            for ordem in (chave, f'-{chave}'):
                with self.subTest(ordem=ordem):
                    _, contexto = self.contexto_lista(ordem=ordem)
                    self.assertEqual(contexto['ordem'], ordem)
                    self.assertEqual(len(contexto['page_obj']), 2)

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
