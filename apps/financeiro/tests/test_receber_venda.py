from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

from django.shortcuts import render
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import DomainError
from apps.financeiro.constants.enums import TipoFormaPagamento
from apps.financeiro.models import FormaPagamento
from apps.financeiro.models.conta_bancaria import PlanoContas
from apps.financeiro.models.receber_pagar import ContaReceber
from apps.financeiro.services.receber_service import ContaReceberService
from apps.financeiro.views.receber import (
    ContaReceberListView, ContaReceberRelatorioView, _vincular_vendas,
)
from apps.pdv.models import VendaPDV


@override_settings(TIME_ZONE='America/Sao_Paulo')
class ReceberVendaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        empresa = Empresa.objects.create(
            razao_social='Empresa Teste', cnpj='11222333000181',
            regime_tributario='simples', codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=empresa, razao_social='Matriz', nome_fantasia='Matriz',
            cnpj='11222333000181', uf='RN', is_matriz=True,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Cliente Teste', cpf_cnpj='12345678901',
        )
        perfil = PerfilAcesso.objects.create(empresa=empresa, nome='Admin', is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email='receber@example.test', nome='Operador', password='teste',
            empresa=empresa, filial=cls.filial, perfil=perfil, is_superuser=True,
        )
        cls.venda = VendaPDV.objects.create(
            filial=cls.filial, cliente=cls.cliente, usuario=cls.usuario,
            numero_venda=1014, status='finalizada', valor_total=Decimal('41'),
            # 18/08 em UTC ainda é 17/08 no histórico em São Paulo.
            data_venda=datetime(2026, 8, 18, 1, 0, tzinfo=dt_timezone.utc),
        )
        cls.categoria = PlanoContas.objects.create(
            empresa=empresa, codigo='1.99', descricao='Categoria que não deve aparecer',
            tipo='R', nivel=1, aceita_lancamento=True,
        )
        cls.conta = ContaReceber.objects.create(
            filial=cls.filial, cliente=cls.cliente,
            plano_contas=cls.categoria,
            documento_tipo='venda_pdv', documento_id=cls.venda.pk,
            documento_numero='DOC-DIFERENTE', valor_original=41, valor_final=41,
            valor_saldo=41, data_emissao=date(2026, 8, 19),
            data_vencimento=date(2026, 8, 27), status='vencido',
        )

    def _get(self, view, **params):
        request = RequestFactory().get('/financeiro/receber/', params)
        request.user = self.usuario
        request.filial_ativa = self.filial
        request.session = {}
        with timezone.override('America/Sao_Paulo'), patch(
            'apps.financeiro.views.receber.render', wraps=render,
        ) as rendering:
            response = view().get(request)
        return response, rendering.call_args.args[2]

    def test_listagem_e_relatorio_separam_conta_documento_venda_e_datas(self):
        for view in (ContaReceberListView, ContaReceberRelatorioView):
            with self.subTest(view=view):
                response, _ = self._get(view)
                self.assertContains(response, 'Nº da conta')
                self.assertContains(response, f'#{self.conta.pk}')
                self.assertContains(response, 'DOC-DIFERENTE')
                self.assertContains(response, '#001014')
                self.assertContains(response, '17/08/2026')
                self.assertContains(response, '27/08/2026')
                self.assertNotContains(response, '19/08/2026')

    def test_listagem_ocupa_toda_a_largura_disponivel(self):
        response, _ = self._get(ContaReceberListView)

        self.assertContains(
            response,
            'class="app-content-frame erp-list-content w-full max-w-none mx-auto',
        )
        self.assertContains(response, 'class="space-y-5 erp-list-page"')

    def test_listagem_exibe_apenas_nome_na_coluna_cliente(self):
        response, _ = self._get(ContaReceberListView)

        self.assertContains(response, self.cliente.razao_social)
        self.assertNotContains(response, 'Forma não definida')
        self.assertNotContains(response, self.categoria.descricao)

    def test_sem_vinculo_nao_inventa_venda_por_documento_ou_emissao(self):
        ContaReceber.objects.filter(pk=self.conta.pk).update(
            documento_tipo='', documento_id=None, documento_numero='1014',
        )
        for view in (ContaReceberListView, ContaReceberRelatorioView):
            with self.subTest(view=view):
                response, _ = self._get(view)
                self.assertContains(response, '1014')
                self.assertNotContains(response, '#001014')
                self.assertNotContains(response, '19/08/2026')

    def test_vinculo_inexistente_ou_de_outra_filial_nao_exibe_venda(self):
        outra = Filial.objects.create(
            empresa=self.filial.empresa, razao_social='Outra',
            cnpj='11222333000262', uf='RN',
        )
        VendaPDV.objects.filter(pk=self.venda.pk).update(filial=outra)
        _vincular_vendas([self.conta], self.filial)
        self.assertIsNone(self.conta.venda_vinculada)
        self.conta.documento_id = 999999
        _vincular_vendas([self.conta], self.filial)
        self.assertIsNone(self.conta.venda_vinculada)

    def test_vendas_carregadas_em_uma_consulta_para_varias_parcelas(self):
        parcelas = [self.conta] * 20
        with self.assertNumQueries(1):
            _vincular_vendas(parcelas, self.filial)
            for conta in parcelas:
                self.assertEqual(conta.venda_vinculada.numero_venda, 1014)
                self.assertEqual(conta.venda_vinculada.data_venda, self.venda.data_venda)

    def test_relatorio_respeita_pendentes_todos_e_status_especifico(self):
        paga = ContaReceber.objects.create(
            filial=self.filial, cliente=self.cliente, valor_original=10,
            valor_final=10, valor_saldo=0, valor_pago=10,
            data_emissao=date(2026, 8, 17), data_vencimento=date(2026, 8, 27),
            status='pago',
        )
        for status, total in [('pendentes', 1), ('todos', 2), ('pago', 1), ('', 1)]:
            with self.subTest(status=status):
                _, context = self._get(ContaReceberRelatorioView, status=status)
                self.assertEqual(context['total_titulos'], total)
                if status == 'pago':
                    self.assertEqual(context['titulos'][0], paga)

    def test_relatorio_repete_a_tabela_da_listagem_e_os_totais_filtrados(self):
        ContaReceber.objects.create(
            filial=self.filial, cliente=self.cliente, documento_numero='QUITADA',
            valor_original=10, valor_final=10, valor_saldo=0, valor_pago=10,
            data_emissao=date(2026, 8, 17), data_vencimento=date(2026, 8, 28),
            data_pagamento=date(2026, 8, 28), status='pago',
        )

        response, context = self._get(ContaReceberRelatorioView, status='todos')

        for coluna in (
            'Nº da conta', 'Cliente', 'Documento', 'Nº da venda',
            'Data da venda', 'Parcela', 'Vencimento', 'Valor',
            'Valor restante', 'Status',
        ):
            self.assertContains(response, coluna)
        self.assertNotContains(response, 'cli-card')
        self.assertNotContains(response, 'Ações')
        self.assertContains(response, 'Imprimir / Salvar PDF')
        self.assertEqual(context['total_geral_valor'], Decimal('51'))
        self.assertEqual(context['total_geral_pago'], Decimal('10'))
        self.assertEqual(context['total_geral_saldo'], Decimal('41'))

    def test_conta_com_baixa_nao_pode_ser_cancelada_diretamente(self):
        forma = FormaPagamento.objects.create(
            empresa=self.filial.empresa,
            descricao="Dinheiro teste",
            tipo=TipoFormaPagamento.DINHEIRO,
        )
        ContaReceberService.registrar_baixa(
            conta=self.conta,
            data_pagamento=date(2026, 8, 28),
            valor_pago=Decimal("10.00"),
            forma_pagamento=forma,
            usuario=self.usuario,
        )

        with self.assertRaisesMessage(DomainError, "recebimento registrado"):
            ContaReceberService.cancelar(
                conta=self.conta,
                motivo="Tentativa indevida",
                usuario=self.usuario,
            )

        self.conta.refresh_from_db()
        self.assertEqual(self.conta.status, "pago_parcial")
        self.assertEqual(self.conta.valor_pago, Decimal("10.00"))

    def test_listagem_mantem_impressao_quando_todas_as_contas_estao_pagas(self):
        ContaReceber.objects.filter(pk=self.conta.pk).update(
            status='pago', valor_pago=Decimal('41'), valor_saldo=Decimal('0'),
            data_pagamento=date(2026, 8, 27),
        )

        response, _ = self._get(ContaReceberListView, status='pago')

        self.assertContains(response, 'Imprimir relatório')

    def test_periodo_filtra_vencimento_e_nao_data_da_venda(self):
        for view in (ContaReceberListView, ContaReceberRelatorioView):
            with self.subTest(view=view):
                response, _ = self._get(view, data_ini='2026-08-27', data_fim='2026-08-27')
                self.assertContains(response, '#001014')
                response, _ = self._get(view, data_ini='2026-08-17', data_fim='2026-08-17')
                self.assertNotContains(response, '#001014')
