from datetime import date, datetime
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Empresa, Filial, PerfilAcesso, RegistroAuditoria, Usuario
from apps.financeiro.constants.enums import StatusContaPagar, TipoFormaPagamento
from apps.financeiro.models import ContaBancaria, FormaPagamento
from apps.financeiro.models.extrato import ExtratoBancario
from apps.financeiro.models.receber_pagar import ContaPagar, PagamentoContaPagar
from apps.financeiro.services.posicao_diaria_service import PosicaoDiariaCaixaService
from apps.pdv.models import PagamentoVendaPDV, VendaPDV


class PosicaoDiariaCaixaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social="Empresa Caixa LTDA", nome_fantasia="Empresa Caixa",
            cnpj="53345678000191", regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social="Filial Caixa", nome_fantasia="Matriz",
            cnpj="53345678000192", uf="RN",
        )
        cls.perfil = PerfilAcesso.objects.create(empresa=cls.empresa, nome="Admin", is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email="caixa@inoovated.com", nome="Usuario Caixa", password="teste1234",
            empresa=cls.empresa, filial=cls.filial, perfil=cls.perfil,
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        session = self.client.session
        session["filial_ativa_id"] = self.filial.pk
        session.save()
        self.banco = ContaBancaria.objects.create(
            filial=self.filial, descricao="Banco principal", banco_nome="Nubank",
            banco_codigo="260", saldo_inicial=Decimal("100.00"), saldo_atual=Decimal("100.00"),
        )
        self.caixa = ContaBancaria.objects.create(
            filial=self.filial, descricao="Dinheiro em caixa", tipo_conta="dinheiro",
            banco_codigo="", saldo_inicial=Decimal("50.00"), saldo_atual=Decimal("50.00"),
        )
        self.forma = FormaPagamento.objects.create(
            empresa=self.empresa, filial=self.filial, descricao="PIX",
            tipo=TipoFormaPagamento.PIX, conta_bancaria_padrao=self.banco,
        )

    def _criar_cenario(self):
        ExtratoBancario.objects.create(
            filial=self.filial, conta_bancaria=self.banco, data_lancamento=date(2026, 8, 20),
            historico="Saldo anterior", valor=Decimal("20.00"), origem="manual",
        )
        ExtratoBancario.objects.create(
            filial=self.filial, conta_bancaria=self.banco, data_lancamento=date(2026, 8, 21),
            historico="Transferencia para caixa", valor=Decimal("-30.00"), origem="manual",
        )
        ExtratoBancario.objects.create(
            filial=self.filial, conta_bancaria=self.caixa, data_lancamento=date(2026, 8, 21),
            historico="Transferencia do banco", valor=Decimal("30.00"), origem="manual",
        )
        venda = VendaPDV.objects.create(
            filial=self.filial, numero_venda=1, status="finalizada",
            valor_total=Decimal("80.00"), valor_pago=Decimal("80.00"), usuario=self.usuario,
            data_venda=datetime(2026, 8, 21, 12, tzinfo=timezone.get_current_timezone()),
        )
        PagamentoVendaPDV.objects.create(
            venda_pdv=venda, forma_pagamento=self.forma, conta_bancaria=self.banco,
            valor=Decimal("80.00"),
        )
        conta_pagar = ContaPagar.objects.create(
            filial=self.filial, valor_original=Decimal("40.00"), valor_final=Decimal("40.00"),
            valor_pago=Decimal("40.00"), valor_saldo=Decimal("0.00"),
            data_emissao=date(2026, 8, 21), data_vencimento=date(2026, 8, 21),
            data_pagamento=date(2026, 8, 21), status=StatusContaPagar.PAGO, usuario=self.usuario,
        )
        PagamentoContaPagar.objects.create(
            filial=self.filial, conta_pagar=conta_pagar, data_pagamento=date(2026, 8, 21),
            valor_pago=Decimal("40.00"), forma_pagamento=self.forma,
            conta_bancaria=self.caixa, usuario=self.usuario,
        )

    def test_fecha_dia_somando_contas_e_neutraliza_transferencia(self):
        self._criar_cenario()
        posicao = PosicaoDiariaCaixaService(self.filial, date(2026, 8, 21)).gerar()

        self.assertEqual(posicao["total_abertura"], Decimal("170.00"))
        self.assertEqual(posicao["total_entradas"], Decimal("110.00"))
        self.assertEqual(posicao["total_saidas"], Decimal("70.00"))
        self.assertEqual(posicao["variacao_dia"], Decimal("40.00"))
        self.assertEqual(posicao["total_fechamento"], Decimal("210.00"))
        saldos = {conta.descricao: conta.posicao_fechamento for conta in posicao["contas"]}
        self.assertEqual(saldos["Banco principal"], Decimal("170.00"))
        self.assertEqual(saldos["Dinheiro em caixa"], Decimal("40.00"))
        self.assertTrue(posicao["possui_caixa_dinheiro"])

    def test_tela_exibe_entradas_saidas_e_atalhos(self):
        self._criar_cenario()
        response = self.client.get(reverse("financeiro:posicao_diaria"), {"data": "2026-08-21"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Posição Diária de Caixa")
        self.assertContains(response, "Venda #1")
        self.assertContains(response, "Pagamento para")
        self.assertContains(response, "Contas a receber")

    def test_admin_exclui_movimento_manual_sem_apagar_historico(self):
        movimento = ExtratoBancario.objects.create(
            filial=self.filial, conta_bancaria=self.banco, data_lancamento=date(2026, 8, 21),
            historico="Credito incorreto", valor=Decimal("90.00"), origem="manual", status="importado",
        )
        response = self.client.post(reverse("financeiro:posicao_diaria"), {
            "acao": "excluir_movimento", "movimento_id": movimento.pk,
            "data_referencia": "2026-08-21", "justificativa": "Lancamento duplicado",
        })

        self.assertEqual(response.status_code, 302)
        movimento.refresh_from_db()
        self.assertEqual(movimento.status, "excluido")
        self.assertTrue(RegistroAuditoria.objects.filter(
            objeto_tipo="financeiro.extratobancario", objeto_id=movimento.pk,
            acao=RegistroAuditoria.Acao.EXCLUIR,
        ).exists())
        posicao = PosicaoDiariaCaixaService(self.filial, date(2026, 8, 21)).gerar()
        self.assertEqual(posicao["total_entradas"], Decimal("0"))
