from datetime import date, datetime
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Empresa, Filial, PerfilAcesso, RegistroAuditoria, Usuario
from apps.cadastros.models import Cliente
from apps.financeiro.constants.enums import StatusContaPagar, TipoFormaPagamento
from apps.financeiro.models import ContaBancaria, FormaPagamento
from apps.financeiro.models.extrato import ExtratoBancario
from apps.financeiro.models.receber_pagar import ContaPagar, ContaReceber, PagamentoContaPagar
from apps.financeiro.services.receber_service import ContaReceberService
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
        self.assertContains(response, "Adicionar entrada manual")
        self.assertContains(response, "Adicionar saída manual")
        self.assertContains(response, "Adicionar conta a pagar")
        self.assertContains(response, reverse("financeiro:pagar_criar") + "?modal=1")
        self.assertContains(response, reverse("financeiro:despesa_paga_criar") + "?modal=1")
        self.assertContains(response, "Transferir entre contas")
        self.assertEqual(len(response.context["dias_mes"]), 31)
        self.assertContains(response, 'aria-label="Ver dias anteriores"')
        self.assertContains(response, 'aria-label="Ver dias posteriores"')
        self.assertContains(response, "tituloPagarModal")

    def test_taxa_de_recebimento_reduz_entrada_e_exibe_bruto(self):
        self.forma.taxa_administrativa = Decimal("2.00")
        self.forma.taxa_fixa = Decimal("0.50")
        self.forma.save(update_fields=["taxa_administrativa", "taxa_fixa"])
        self._criar_cenario()

        posicao = PosicaoDiariaCaixaService(self.filial, date(2026, 8, 21)).gerar()
        venda = next(mov for mov in posicao["entradas"] if mov.origem_codigo == "venda")
        self.assertEqual(venda.valor_bruto, Decimal("80.00"))
        self.assertEqual(venda.valor_taxa, Decimal("2.10"))
        self.assertEqual(venda.entrada, Decimal("77.90"))
        self.assertEqual(posicao["total_entradas"], Decimal("107.90"))
        self.assertEqual(posicao["total_fechamento"], Decimal("207.90"))

        response = self.client.get(reverse("financeiro:posicao_diaria"), {"data": "2026-08-21"})
        self.assertContains(response, "Bruto R$ 80,00")
        self.assertContains(response, "taxa R$ 2,10")

    def test_venda_so_entra_na_data_de_compensacao(self):
        self.forma.prazo_compensacao_dias_uteis = 1
        self.forma.save(update_fields=["prazo_compensacao_dias_uteis"])
        venda = VendaPDV.objects.create(
            filial=self.filial, numero_venda=2, status="finalizada",
            valor_total=Decimal("100.00"), valor_pago=Decimal("100.00"), usuario=self.usuario,
            data_venda=datetime(2026, 8, 21, 12, tzinfo=timezone.get_current_timezone()),
        )
        PagamentoVendaPDV.objects.create(
            venda_pdv=venda, forma_pagamento=self.forma, conta_bancaria=self.banco,
            valor=Decimal("100.00"),
        )

        sexta = PosicaoDiariaCaixaService(self.filial, date(2026, 8, 21)).gerar(incluir_previstos=True)
        segunda = PosicaoDiariaCaixaService(self.filial, date(2026, 8, 24)).gerar()
        self.assertEqual(sexta["total_entradas"], Decimal("0"))
        self.assertEqual(sexta["total_previsto"], Decimal("100.00"))
        self.assertEqual(segunda["total_entradas"], Decimal("100.00"))

    def test_baixa_de_boleto_compensa_no_proximo_dia_util(self):
        self.forma.tipo = TipoFormaPagamento.BOLETO
        self.forma.prazo_compensacao_dias_uteis = 1
        self.forma.save(update_fields=["tipo", "prazo_compensacao_dias_uteis"])
        cliente = Cliente.objects.create(
            filial=self.filial, razao_social="Cliente boleto", tipo_pessoa="F", cpf_cnpj="12345678901",
        )
        conta = ContaReceber.objects.create(
            filial=self.filial, cliente=cliente, valor_original=Decimal("80.00"),
            valor_final=Decimal("80.00"), valor_saldo=Decimal("80.00"),
            data_emissao=date(2026, 8, 20), data_vencimento=date(2026, 8, 21),
        )
        ContaReceberService.registrar_baixa(
            conta, date(2026, 8, 21), Decimal("80.00"), self.forma, self.usuario,
            conta_bancaria=self.banco,
        )
        conta.refresh_from_db()

        self.assertEqual(conta.data_liquidacao_prevista, date(2026, 8, 24))
        self.assertEqual(
            PosicaoDiariaCaixaService(self.filial, date(2026, 8, 21)).gerar()["total_entradas"],
            Decimal("0"),
        )
        self.assertEqual(
            PosicaoDiariaCaixaService(self.filial, date(2026, 8, 24)).gerar()["total_entradas"],
            Decimal("80.00"),
        )

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

    def test_periodo_consolida_movimentos_sem_duplicar_saldo_inicial(self):
        ExtratoBancario.objects.create(
            filial=self.filial, conta_bancaria=self.banco, data_lancamento=date(2026, 8, 19),
            historico="Antes do periodo", valor=Decimal("10.00"), origem="manual",
        )
        ExtratoBancario.objects.create(
            filial=self.filial, conta_bancaria=self.banco, data_lancamento=date(2026, 8, 20),
            historico="Entrada no periodo", valor=Decimal("25.00"), origem="manual",
        )
        ExtratoBancario.objects.create(
            filial=self.filial, conta_bancaria=self.banco, data_lancamento=date(2026, 8, 21),
            historico="Saida no periodo", valor=Decimal("-5.00"), origem="manual",
        )

        posicao = PosicaoDiariaCaixaService(
            self.filial, date(2026, 8, 21), data_inicio=date(2026, 8, 20),
        ).gerar()

        self.assertEqual(posicao["total_abertura"], Decimal("160.00"))
        self.assertEqual(posicao["total_entradas"], Decimal("25.00"))
        self.assertEqual(posicao["total_saidas"], Decimal("5.00"))
        self.assertEqual(posicao["total_fechamento"], Decimal("180.00"))

    def test_lancamento_manual_positivo_e_negativo_salva_na_posicao(self):
        url = reverse("financeiro:posicao_diaria")
        credito = self.client.post(url, {
            "acao": "lancar_movimento", "tipo": "credito",
            "conta_destino": self.banco.pk, "data_lancamento": "2020-01-01",
            "data_referencia": "2026-08-21", "valor": "35.50", "historico": "Credito manual",
        })
        debito = self.client.post(url, {
            "acao": "lancar_movimento", "tipo": "debito",
            "conta_origem": self.banco.pk, "data_lancamento": "2026-08-21",
            "data_referencia": "2026-08-21", "valor": "10.25", "historico": "Debito manual",
        })

        self.assertEqual(credito.status_code, 302)
        self.assertEqual(debito.status_code, 302)
        valores = list(ExtratoBancario.objects.filter(
            filial=self.filial, historico__in=("Credito manual", "Debito manual"),
        ).order_by("historico").values_list("valor", flat=True))
        self.assertEqual(valores, [Decimal("35.50"), Decimal("-10.25")])
        self.assertFalse(ExtratoBancario.objects.filter(
            filial=self.filial, historico__in=("Credito manual", "Debito manual"),
        ).exclude(data_lancamento=timezone.localdate()).exists())
