from datetime import date
from decimal import Decimal

from django.template.loader import render_to_string
from django.test import TestCase

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial
from apps.financeiro.constants.enums import StatusContaPagar, StatusContaReceber
from apps.financeiro.models import PlanoContas
from apps.financeiro.models.receber_pagar import ContaPagar, ContaReceber
from apps.financeiro.services.dashboard_contas_service import DashboardContasService


class DashboardContasServiceTests(TestCase):
    def setUp(self):
        self.hoje = date(2026, 8, 20)
        self.empresa = Empresa.objects.create(
            razao_social='Empresa Teste',
            nome_fantasia='Empresa Teste',
            cnpj='11222333000181',
            regime_tributario='simples_nacional',
            codigo_regime_tributario=1,
        )
        self.filial = Filial.objects.create(
            empresa=self.empresa,
            razao_social='Matriz Teste',
            nome_fantasia='Matriz Teste',
            cnpj='11222333000181',
            uf='RN',
            is_matriz=True,
        )
        self.cliente = Cliente.objects.create(
            filial=self.filial,
            tipo_pessoa='J',
            razao_social='Cliente Alpha',
            cpf_cnpj='12345678000195',
        )
        self.categoria = PlanoContas.objects.create(
            empresa=self.empresa,
            codigo='3.1.1',
            descricao='Materia-prima',
            tipo='D',
            nivel=3,
        )

    def _receber(self, valor, vencimento, status=StatusContaReceber.ABERTO, pago=0):
        return ContaReceber.objects.create(
            filial=self.filial,
            cliente=self.cliente,
            valor_original=valor,
            valor_final=valor,
            valor_pago=pago,
            valor_saldo=Decimal(str(valor)) - Decimal(str(pago)),
            data_emissao=self.hoje,
            data_vencimento=vencimento,
            data_pagamento=self.hoje if status == StatusContaReceber.PAGO else None,
            status=status,
        )

    def _pagar(self, valor, vencimento, status=StatusContaPagar.ABERTO, pago=0, categoria=True):
        return ContaPagar.objects.create(
            filial=self.filial,
            plano_contas=self.categoria if categoria else None,
            valor_original=valor,
            valor_final=valor,
            valor_pago=pago,
            valor_saldo=Decimal(str(valor)) - Decimal(str(pago)),
            data_emissao=self.hoje,
            data_vencimento=vencimento,
            data_pagamento=self.hoje if status == StatusContaPagar.PAGO else None,
            status=status,
        )

    def test_apura_saldos_agenda_e_concentracoes(self):
        self._receber('1000.00', date(2026, 8, 19), StatusContaReceber.VENCIDO)
        self._receber('500.00', date(2026, 8, 25))
        self._receber('300.00', self.hoje, StatusContaReceber.PAGO, pago='300.00')
        self._pagar('600.00', date(2026, 8, 24))
        self._pagar('200.00', date(2026, 9, 25), categoria=False)
        self._pagar('100.00', self.hoje, StatusContaPagar.PAGO, pago='100.00')

        painel = DashboardContasService.apurar(self.filial, hoje=self.hoje)

        self.assertEqual(painel['receber']['aberto'], Decimal('1500.00'))
        self.assertEqual(painel['pagar']['aberto'], Decimal('800.00'))
        self.assertEqual(painel['saldo_projetado'], Decimal('700.00'))
        self.assertEqual(painel['saldo_realizado_mes'], Decimal('200.00'))
        self.assertEqual(painel['receber']['vencido'], Decimal('1000.00'))
        self.assertEqual(painel['pagar']['sete'], Decimal('600.00'))
        self.assertEqual(painel['pagar']['sem_categoria'], Decimal('200.00'))
        self.assertEqual(painel['maiores_clientes'][0]['nome'], 'Cliente Alpha')
        self.assertEqual(painel['maiores_categorias'][0]['nome'], 'Materia-prima')

    def test_modal_renderiza_indicadores_e_atalhos(self):
        self._receber('150.00', date(2026, 8, 22))
        painel = DashboardContasService.apurar(self.filial, hoje=self.hoje)

        html = render_to_string(
            'financeiro/_dashboard_contas_modal.html',
            {'dashboard_contas': painel},
        )

        self.assertIn('Visão financeira', html)
        self.assertIn('Contas a pagar e receber', html)
        self.assertIn('Agenda financeira', html)
        self.assertIn('Cliente Alpha', html)

