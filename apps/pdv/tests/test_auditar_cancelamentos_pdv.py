import json
from datetime import date
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from apps.cadastros.models import Cliente, ClienteFilial
from apps.core.models import RegistroAuditoria
from apps.estoque.models import Estoque, MovimentacaoEstoque
from apps.financeiro.constants.enums import TipoFormaPagamento
from apps.financeiro.models import ContaReceber, FormaPagamento
from apps.financeiro.services.receber_service import ContaReceberService
from apps.pdv.models import VendaPDV
from apps.pdv.services.venda_pdv_service import VendaPDVService
from apps.pdv.tests import test_venda_pdv_service as venda_tests


class AuditarCancelamentosPDVTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        venda_tests.VendaPDVServiceTests.setUpTestData.__func__(cls)

    criar_produto = venda_tests.VendaPDVServiceTests.criar_produto
    abastecer = venda_tests.VendaPDVServiceTests.abastecer

    def setUp(self):
        venda_tests.VendaPDVServiceTests.setUp(self)
        cliente = Cliente.objects.create(
            filial=self.filial,
            razao_social="Cliente do auditor",
        )
        ClienteFilial.objects.create(cliente=cliente, filial=self.filial)
        self.vale = FormaPagamento.objects.create(
            empresa=self.empresa,
            descricao="Vale auditoria",
            tipo=TipoFormaPagamento.VALE,
        )
        self.produto = self.criar_produto("Produto auditado")
        self.abastecer(self.produto, "5")
        self.venda = VendaPDVService.finalizar_venda(
            sessao=self.sessao,
            filial=self.filial,
            usuario=self.usuario,
            cliente_id=cliente.pk,
            itens=[{"produto_id": self.produto.pk, "quantidade": 2}],
            pagamentos=[{"forma_id": self.vale.pk, "valor": "20"}],
        )
        self.conta = ContaReceber.objects.get(
            documento_tipo="venda_pdv",
            documento_id=self.venda.pk,
        )
        VendaPDV.objects.filter(pk=self.venda.pk).update(
            status="cancelada",
            cancelado_em=timezone.now(),
            cancelado_por=self.usuario,
            motivo_cancelamento="Cancelamento antigo sem estorno",
        )
        self.venda.refresh_from_db()

    def executar(self, **opcoes):
        saida = StringIO()
        call_command(
            "auditar_cancelamentos_pdv",
            filial_id=self.filial.pk,
            como_json=True,
            stdout=saida,
            **opcoes,
        )
        return json.loads(saida.getvalue())

    def test_auditoria_padrao_e_somente_leitura(self):
        relatorio = self.executar()

        self.assertEqual(relatorio["modo"], "auditoria")
        self.assertEqual(relatorio["vendas_canceladas_analisadas"], 1)
        self.assertEqual(relatorio["vendas_com_divergencia"][0]["numero_venda"], self.venda.numero_venda)
        self.assertEqual(relatorio["vendas_com_divergencia"][0]["estoque_faltante"][0]["diferenca"], "2.000")
        self.assertEqual(relatorio["sessoes_divergentes"][0]["registrado"], "20.00")
        self.assertEqual(relatorio["sessoes_divergentes"][0]["esperado"], "0.00")
        self.conta.refresh_from_db()
        self.sessao.refresh_from_db()
        self.assertEqual(self.conta.status, "aberto")
        self.assertEqual(self.sessao.total_vendas, Decimal("20.00"))
        self.assertEqual(
            Estoque.objects.get(produto=self.produto, filial=self.filial).quantidade_atual,
            Decimal("3.000"),
        )

    def test_correcao_exige_confirmacao_literal(self):
        with self.assertRaises(CommandError):
            self.executar(corrigir=True)

    def test_correcao_repara_financeiro_estoque_e_sessao_e_e_idempotente(self):
        relatorio = self.executar(
            corrigir=True,
            confirmar="CORRIGIR_CANCELAMENTOS_PDV",
        )

        self.conta.refresh_from_db()
        self.sessao.refresh_from_db()
        self.assertEqual(self.conta.status, "cancelado")
        self.assertEqual(self.sessao.total_vendas, Decimal("0.00"))
        self.assertEqual(
            Estoque.objects.get(produto=self.produto, filial=self.filial).quantidade_atual,
            Decimal("5.000"),
        )
        self.assertTrue(relatorio["vendas_com_divergencia"][0]["corrigida"])
        self.assertTrue(relatorio["sessoes_divergentes"][0]["corrigida"])
        self.assertEqual(
            MovimentacaoEstoque.objects.filter(
                documento_id=self.venda.pk,
                tipo_operacao=MovimentacaoEstoque.TipoOperacao.DEVOLUCAO_CLIENTE,
            ).count(),
            1,
        )
        self.assertEqual(
            RegistroAuditoria.objects.filter(
                objeto_tipo="pdv.vendapdv",
                objeto_id=self.venda.pk,
            ).count(),
            1,
        )

        segunda = self.executar(
            corrigir=True,
            confirmar="CORRIGIR_CANCELAMENTOS_PDV",
        )
        self.assertEqual(segunda["vendas_com_divergencia"], [])
        self.assertEqual(
            MovimentacaoEstoque.objects.filter(
                documento_id=self.venda.pk,
                tipo_operacao=MovimentacaoEstoque.TipoOperacao.DEVOLUCAO_CLIENTE,
            ).count(),
            1,
        )

    def test_recebimento_existente_bloqueia_reparo_automatico(self):
        ContaReceberService.registrar_baixa(
            conta=self.conta,
            data_pagamento=date.today(),
            valor_pago=Decimal("5.00"),
            forma_pagamento=self.forma,
            usuario=self.usuario,
        )

        relatorio = self.executar(
            corrigir=True,
            confirmar="CORRIGIR_CANCELAMENTOS_PDV",
        )

        self.conta.refresh_from_db()
        self.sessao.refresh_from_db()
        self.assertEqual(relatorio["vendas_bloqueadas_por_recebimento"][0]["numero_venda"], self.venda.numero_venda)
        self.assertEqual(self.conta.status, "pago_parcial")
        self.assertEqual(self.sessao.total_vendas, Decimal("20.00"))
        self.assertEqual(
            Estoque.objects.get(produto=self.produto, filial=self.filial).quantidade_atual,
            Decimal("3.000"),
        )

    def test_sessao_fechada_e_auditada_sem_reabrir_fechamento(self):
        self.sessao.status = "fechado"
        self.sessao.save(update_fields=["status"])

        relatorio = self.executar(
            corrigir=True,
            confirmar="CORRIGIR_CANCELAMENTOS_PDV",
        )

        self.sessao.refresh_from_db()
        self.assertEqual(self.sessao.total_vendas, Decimal("20.00"))
        self.assertTrue(relatorio["sessoes_divergentes"][0]["correcao_bloqueada"])
        self.assertFalse(relatorio["sessoes_divergentes"][0]["corrigida"])

    def test_repara_metadados_antigos_e_nao_os_aponta_novamente(self):
        relatorio_inicial = self.executar(
            corrigir=True,
            confirmar="CORRIGIR_CANCELAMENTOS_PDV",
        )
        self.assertTrue(relatorio_inicial["vendas_com_divergencia"][0]["corrigida"])
        VendaPDV.objects.filter(pk=self.venda.pk).update(
            cancelado_em=None,
            cancelado_por=None,
        )

        relatorio = self.executar(
            corrigir=True,
            confirmar="CORRIGIR_CANCELAMENTOS_PDV",
        )

        self.venda.refresh_from_db()
        self.assertIsNotNone(self.venda.cancelado_em)
        self.assertEqual(self.venda.cancelado_por, self.usuario)
        self.assertTrue(relatorio["vendas_com_divergencia"][0]["corrigida"])
        self.assertEqual(self.executar()["vendas_com_divergencia"], [])
