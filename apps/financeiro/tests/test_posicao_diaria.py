from datetime import date, datetime
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Empresa, Filial, PerfilAcesso, RegistroAuditoria, Usuario
from apps.cadastros.models import Cliente
from apps.financeiro.constants.enums import StatusContaPagar, StatusContaReceber, TipoFormaPagamento
from apps.financeiro.models import ContaBancaria, FormaPagamento, PlanoContabil, PlanoContas
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
            descricao_despesa="Compra de material de limpeza",
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
        self.assertIn(
            "Sem forma vinculada",
            {item["nome"] for item in posicao["totais_forma_entrada"]},
        )
        self.assertIn(
            "Sem forma vinculada",
            {item["nome"] for item in posicao["totais_forma_saida"]},
        )

    def test_tela_exibe_entradas_saidas_e_atalhos(self):
        self._criar_cenario()
        response = self.client.get(reverse("financeiro:posicao_diaria"), {"data": "2026-08-21"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Posição Diária de Caixa")
        self.assertContains(response, "Venda #1")
        self.assertContains(response, "Compra de material de limpeza")
        self.assertContains(response, "Contas a receber")
        self.assertContains(response, "Adicionar entrada manual")
        self.assertContains(response, "Adicionar conta a pagar")
        self.assertContains(response, reverse("financeiro:pagar_criar") + "?modal=1")
        self.assertContains(response, reverse("financeiro:despesa_paga_criar") + "?modal=1")
        self.assertContains(response, "Transferir entre contas")
        self.assertEqual(len(response.context["dias_mes"]), 31)
        self.assertContains(response, 'aria-label="Ver dias anteriores"')
        self.assertContains(response, 'aria-label="Ver dias posteriores"')
        self.assertContains(response, "tituloPagarModal")
        self.assertContains(response, "Agrupar por forma de pagamento")

    def test_despesa_pessoal_fica_destacada_e_somada_separadamente(self):
        categoria = PlanoContas.objects.create(
            empresa=self.empresa,
            codigo="3900100001",
            descricao="Compras Pessoais",
            tipo="D",
            nivel=3,
            aceita_lancamento=True,
            despesa_pessoal=True,
        )
        conta = ContaPagar.objects.create(
            filial=self.filial,
            descricao_despesa="Compra pessoal da sócia",
            valor_original=Decimal("35.00"),
            valor_final=Decimal("35.00"),
            valor_pago=Decimal("35.00"),
            valor_saldo=Decimal("0.00"),
            data_emissao=date(2026, 8, 21),
            data_vencimento=date(2026, 8, 21),
            data_pagamento=date(2026, 8, 21),
            status=StatusContaPagar.PAGO,
            plano_contas=categoria,
            usuario=self.usuario,
        )
        PagamentoContaPagar.objects.create(
            filial=self.filial,
            conta_pagar=conta,
            data_pagamento=date(2026, 8, 21),
            valor_pago=Decimal("35.00"),
            forma_pagamento=self.forma,
            conta_bancaria=self.banco,
            usuario=self.usuario,
        )

        posicao = PosicaoDiariaCaixaService(self.filial, date(2026, 8, 21)).gerar()
        self.assertEqual(posicao["total_despesas_pessoais"], Decimal("35.00"))
        self.assertTrue(next(
            m for m in posicao["saidas"] if m.descricao == "Compra pessoal da sócia"
        ).despesa_pessoal)

        response = self.client.get(reverse("financeiro:posicao_diaria"), {"data": "2026-08-21"})
        self.assertContains(response, "Compra pessoal da sócia")
        self.assertContains(response, "Meta mensal de despesas pessoais")
        self.assertContains(response, 'class="pc-personal-badge"')
        self.assertContains(response, 'class="pc-personal-summary')
        self.assertContains(response, '<template x-teleport="body"><div x-show="tituloPagarModal"')

    def test_taxa_de_recebimento_reduz_entrada_e_exibe_bruto(self):
        self.forma.taxa_administrativa = Decimal("2.00")
        self.forma.taxa_fixa = Decimal("0.50")
        self.forma.save(update_fields=["taxa_administrativa", "taxa_fixa"])
        self._criar_cenario()

        posicao = PosicaoDiariaCaixaService(self.filial, date(2026, 8, 21)).gerar()
        venda = next(mov for mov in posicao["entradas"] if mov.origem_codigo == "venda")
        self.assertEqual(venda.valor_bruto, Decimal("80.00"))
        self.assertEqual(venda.valor_taxa, Decimal("2.10"))
        self.assertEqual(venda.taxa_percentual, Decimal("2.00"))
        self.assertEqual(venda.entrada, Decimal("77.90"))
        self.assertEqual(posicao["total_entradas"], Decimal("110.00"))
        self.assertEqual(posicao["total_saidas"], Decimal("72.10"))
        self.assertEqual(posicao["variacao_dia"], Decimal("37.90"))
        self.assertEqual(posicao["total_fechamento"], Decimal("207.90"))
        self.assertEqual(posicao["total_taxas_entradas"], Decimal("2.10"))
        self.assertEqual(posicao["total_liquido_entradas"], Decimal("107.90"))
        self.assertEqual(posicao["taxas_por_forma"][0]["nome"], self.forma.descricao)

        response = self.client.get(reverse("financeiro:posicao_diaria"), {"data": "2026-08-21"})
        self.assertContains(response, "TAXA 2,00% + R$ 0,50")
        self.assertContains(response, "Taxas do período")
        self.assertContains(response, "Detalhamento das taxas")
        self.assertContains(response, "Taxas descontadas")
        self.assertNotContains(response, 'class="pc-fee-summary')
        self.assertContains(response, "R$ 2,10")

    def test_entrada_manual_exibe_percentual_configurado_sem_descontar_saldo(self):
        self.forma.taxa_administrativa = Decimal("2.50")
        self.forma.taxa_fixa = Decimal("0.30")
        self.forma.save(update_fields=["taxa_administrativa", "taxa_fixa"])
        self._criar_cenario()
        ExtratoBancario.objects.create(
            filial=self.filial, conta_bancaria=self.banco,
            data_lancamento=date(2026, 8, 21), historico="Crédito manual com cartão",
            valor=Decimal("25.00"), origem="manual", forma_pagamento=self.forma,
        )

        response = self.client.get(reverse("financeiro:posicao_diaria"), {"data": "2026-08-21"})

        self.assertContains(response, "TAXA 2,50% + R$ 0,30")
        self.assertContains(response, "Informativa")

    def test_saida_manual_herda_marcacao_de_despesa_pessoal_do_grupo(self):
        grupo = PlanoContas.objects.create(
            empresa=self.empresa, codigo="3900000000", descricao="Despesas Pessoais e Sócios",
            tipo="D", nivel=1, aceita_lancamento=False, despesa_pessoal=True,
        )
        tipo = PlanoContas.objects.create(
            empresa=self.empresa, conta_pai=grupo, codigo="3900100000",
            descricao="Gastos Pessoais", tipo="D", nivel=2, aceita_lancamento=False,
        )
        categoria = PlanoContas.objects.create(
            empresa=self.empresa, conta_pai=tipo, codigo="3900100001",
            descricao="Compras Pessoais", tipo="D", nivel=3, aceita_lancamento=True,
        )
        ExtratoBancario.objects.create(
            filial=self.filial, conta_bancaria=self.banco, forma_pagamento=self.forma,
            plano_contas=categoria, data_lancamento=date(2026, 8, 21),
            historico="Gasolina pessoal", valor=Decimal("-10.00"), origem="manual",
        )

        posicao = PosicaoDiariaCaixaService(self.filial, date(2026, 8, 21)).gerar()
        movimento = next(m for m in posicao["saidas"] if m.descricao == "Gasolina pessoal")
        self.assertTrue(movimento.despesa_pessoal)
        self.assertEqual(posicao["total_despesas_pessoais"], Decimal("10.00"))

        response = self.client.get(reverse("financeiro:posicao_diaria"), {"data": "2026-08-21"})
        self.assertContains(response, "Gasolina pessoal")
        self.assertContains(response, "Despesa pessoal")

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
        response = self.client.get(reverse("financeiro:posicao_diaria"), {
            "data": "2026-08-21", "previsao": "7d",
        })
        self.assertContains(response, "Ver título")
        self.assertContains(response, reverse("financeiro:receber_detail", args=[conta.pk]))

    def test_recebimento_previsto_atrasado_aparece_em_vermelho(self):
        cliente = Cliente.objects.create(
            filial=self.filial, razao_social="Cliente atrasado", tipo_pessoa="F",
            cpf_cnpj="12345678902",
        )

    def _categoria_receita(self, descricao="Venda manual"):
        conta_contabil = PlanoContabil.objects.create(
            empresa=self.empresa,
            codigo_referencia=900001,
            classificacao="3.1.1.001",
            tipo_conta=PlanoContabil.TipoConta.ANALITICA,
            descricao=descricao,
            data_inicio=date(2026, 1, 1),
            nivel=4,
            ordem=900001,
        )
        return PlanoContas.objects.create(
            empresa=self.empresa,
            codigo="310010001",
            descricao=descricao,
            tipo="R",
            nivel=3,
            aceita_lancamento=True,
            conta_contabil=conta_contabil,
        )

    def _categoria_despesa(self, descricao="Despesa manual"):
        conta_contabil = PlanoContabil.objects.create(
            empresa=self.empresa,
            codigo_referencia=900002,
            classificacao="3.2.1.001",
            tipo_conta=PlanoContabil.TipoConta.ANALITICA,
            descricao=descricao,
            data_inicio=date(2026, 1, 1),
            nivel=4,
            ordem=900002,
        )
        return PlanoContas.objects.create(
            empresa=self.empresa,
            codigo="320010001",
            descricao=descricao,
            tipo="D",
            nivel=3,
            aceita_lancamento=True,
            conta_contabil=conta_contabil,
        )
        vencimento = timezone.localdate() - timezone.timedelta(days=3)
        ContaReceber.objects.create(
            filial=self.filial, cliente=cliente, valor_original=Decimal("140.00"),
            valor_final=Decimal("140.00"), valor_saldo=Decimal("140.00"),
            data_emissao=vencimento, data_vencimento=vencimento,
            status=StatusContaReceber.ABERTO, forma_pagamento=self.forma,
            conta_bancaria=self.banco,
        )

        response = self.client.get(reverse("financeiro:posicao_diaria"), {
            "data": timezone.localdate().isoformat(), "previsao": "hoje",
        })

        self.assertContains(response, "Cliente atrasado")
        self.assertContains(response, "Atrasado")
        self.assertContains(response, "Alterar data")
        self.assertContains(response, "is-overdue")

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

    def test_entrada_manual_salva_forma_e_pode_ser_corrigida_pelo_admin(self):
        movimento = ExtratoBancario.objects.create(
            filial=self.filial, conta_bancaria=self.banco, forma_pagamento=self.forma,
            data_lancamento=date(2026, 8, 21), historico="Entrada manual",
            valor=Decimal("25.00"), origem="manual", status="importado",
        )

        detalhe = self.client.get(reverse("financeiro:posicao_diaria"), {
            "data": "2026-08-21", "origem": "manual", "movimento": movimento.pk,
        })
        self.assertContains(detalhe, 'value="2026-08-21"')

        response = self.client.post(reverse("financeiro:posicao_diaria"), {
            "acao": "editar_entrada", "origem": "manual", "movimento_id": movimento.pk,
            "data_referencia": "2026-08-21", "valor": "31.50",
            "forma_pagamento": self.forma.pk, "conta_bancaria": self.caixa.pk,
            "data_entrada": "2026-08-20", "descricao": "Entrada corrigida",
            "justificativa": "Conta e valor informados incorretamente",
        })

        self.assertEqual(response.status_code, 302)
        movimento.refresh_from_db()
        self.assertEqual(movimento.valor, Decimal("31.50"))
        self.assertEqual(movimento.conta_bancaria, self.caixa)
        self.assertEqual(movimento.forma_pagamento, self.forma)
        self.assertEqual(movimento.data_lancamento, date(2026, 8, 20))
        self.assertEqual(movimento.historico, "Entrada corrigida")
        self.assertTrue(RegistroAuditoria.objects.filter(
            objeto_tipo="financeiro.extratobancario", objeto_id=movimento.pk,
            acao=RegistroAuditoria.Acao.AJUSTAR,
        ).exists())

    def test_entrada_manual_salva_e_exibe_classificacao_de_receita(self):
        categoria = self._categoria_receita("Emprestimo recebido")

        response = self.client.post(reverse("financeiro:posicao_diaria"), {
            "acao": "lancar_movimento", "tipo": "credito",
            "conta_destino": self.banco.pk, "data_lancamento": "2026-08-21",
            "data_referencia": "2026-08-21", "valor": "75.00",
            "historico": "Entrada classificada", "forma_pagamento": self.forma.pk,
            "plano_contas": categoria.pk,
        })

        self.assertEqual(response.status_code, 302)
        movimento = ExtratoBancario.objects.get(historico="Entrada classificada")
        self.assertEqual(movimento.plano_contas, categoria)
        posicao = PosicaoDiariaCaixaService(self.filial, timezone.localdate()).gerar()
        entrada = next(mov for mov in posicao["entradas"] if mov.registro_id == movimento.pk)
        self.assertEqual(entrada.classificacao, "Emprestimo recebido")

    def test_saida_manual_nao_pode_ser_corrigida_como_entrada(self):
        movimento = ExtratoBancario.objects.create(
            filial=self.filial, conta_bancaria=self.banco, forma_pagamento=self.forma,
            data_lancamento=date(2026, 8, 21), historico="Saida manual",
            valor=Decimal("-25.00"), origem="manual", status="importado",
        )

        response = self.client.post(reverse("financeiro:posicao_diaria"), {
            "acao": "editar_entrada", "origem": "manual", "movimento_id": movimento.pk,
            "data_referencia": "2026-08-21", "valor": "31.50",
            "forma_pagamento": self.forma.pk, "conta_bancaria": self.caixa.pk,
            "data_entrada": "2026-08-20", "descricao": "Tentativa",
            "justificativa": "Nao deve alterar",
        }, follow=True)

        movimento.refresh_from_db()
        self.assertEqual(movimento.valor, Decimal("-25.00"))
        self.assertContains(response, "Saida manual nao pode ser editada.")

    def test_saida_manual_pode_editar_todos_os_dados_e_classificacao(self):
        categoria = self._categoria_despesa("Combustivel")
        movimento = ExtratoBancario.objects.create(
            filial=self.filial, conta_bancaria=self.banco, forma_pagamento=self.forma,
            plano_contas=categoria, data_lancamento=date(2026, 8, 21),
            historico="Gasolina", documento="CUPOM-1", valor=Decimal("-25.00"),
            origem="manual", status="importado",
        )

        tela_edicao = self.client.get(reverse("financeiro:posicao_diaria"), {
            "data": "2026-08-21", "editar": movimento.pk,
        })
        self.assertContains(tela_edicao, 'name="data_lancamento"')
        self.assertContains(tela_edicao, 'value="2026-08-21"')
        self.assertContains(tela_edicao, f'value="{categoria.pk}" selected')

        response = self.client.post(reverse("financeiro:posicao_diaria"), {
            "acao": "editar_movimento", "movimento_id": movimento.pk,
            "data_referencia": "2026-08-21", "valor": "31.50",
            "forma_pagamento": self.forma.pk, "conta_bancaria": self.caixa.pk,
            "data_lancamento": "2026-08-20", "historico": "Gasolina corrigida",
            "documento": "CUPOM-2", "plano_contas": categoria.pk,
            "justificativa": "Valor e conta informados incorretamente",
        })

        self.assertEqual(response.status_code, 302)
        movimento.refresh_from_db()
        self.assertEqual(movimento.valor, Decimal("-31.50"))
        self.assertEqual(movimento.conta_bancaria, self.caixa)
        self.assertEqual(movimento.data_lancamento, date(2026, 8, 20))
        self.assertEqual(movimento.historico, "Gasolina corrigida")
        self.assertEqual(movimento.documento, "CUPOM-2")
        self.assertEqual(movimento.plano_contas, categoria)

    def test_corrige_recebimento_recalculando_taxa_liquido_e_saldo(self):
        self.forma.taxa_administrativa = Decimal("2.00")
        self.forma.taxa_fixa = Decimal("0.50")
        self.forma.save(update_fields=["taxa_administrativa", "taxa_fixa"])
        cliente = Cliente.objects.create(
            filial=self.filial, razao_social="Cliente corrigido", tipo_pessoa="F",
            cpf_cnpj="98765432100",
        )
        conta = ContaReceber.objects.create(
            filial=self.filial, cliente=cliente, valor_original=Decimal("100.00"),
            valor_final=Decimal("100.00"), valor_pago=Decimal("50.00"),
            valor_saldo=Decimal("50.00"), data_emissao=date(2026, 8, 20),
            data_vencimento=date(2026, 8, 21), data_pagamento=date(2026, 8, 21),
            data_liquidacao_prevista=date(2026, 8, 21), forma_pagamento=self.forma,
            conta_bancaria=self.banco,
        )

        response = self.client.post(reverse("financeiro:posicao_diaria"), {
            "acao": "editar_entrada", "origem": "receber", "movimento_id": conta.pk,
            "data_referencia": "2026-08-21", "valor": "80.00",
            "forma_pagamento": self.forma.pk, "conta_bancaria": self.caixa.pk,
            "data_entrada": "2026-08-22", "justificativa": "Baixa parcial corrigida",
        })

        self.assertEqual(response.status_code, 302)
        conta.refresh_from_db()
        self.assertEqual(conta.valor_pago, Decimal("80.00"))
        self.assertEqual(conta.valor_saldo, Decimal("20.00"))
        self.assertEqual(conta.valor_taxa_recebimento, Decimal("2.10"))
        self.assertEqual(conta.valor_liquido_recebido, Decimal("77.90"))
        self.assertEqual(conta.conta_bancaria, self.caixa)
        self.assertEqual(conta.data_liquidacao_prevista, date(2026, 8, 22))

    def test_corrige_entrada_de_venda_sem_duplicar_pagamento(self):
        self.forma.taxa_administrativa = Decimal("1.00")
        self.forma.save(update_fields=["taxa_administrativa"])
        venda = VendaPDV.objects.create(
            filial=self.filial, numero_venda=88, status="finalizada",
            valor_total=Decimal("120.00"), valor_pago=Decimal("80.00"), usuario=self.usuario,
            data_venda=datetime(2026, 8, 21, 12, tzinfo=timezone.get_current_timezone()),
        )
        pagamento = PagamentoVendaPDV.objects.create(
            venda_pdv=venda, forma_pagamento=self.forma, conta_bancaria=self.banco,
            valor=Decimal("80.00"),
        )

        response = self.client.post(reverse("financeiro:posicao_diaria"), {
            "acao": "editar_entrada", "origem": "venda", "movimento_id": pagamento.pk,
            "data_referencia": "2026-08-21", "valor": "100.00",
            "forma_pagamento": self.forma.pk, "conta_bancaria": self.caixa.pk,
            "data_entrada": "2026-08-22", "justificativa": "Valor recebido corrigido",
        })

        self.assertEqual(response.status_code, 302)
        pagamento.refresh_from_db()
        venda.refresh_from_db()
        self.assertEqual(PagamentoVendaPDV.objects.filter(venda_pdv=venda).count(), 1)
        self.assertEqual(pagamento.valor_bruto_recebido, Decimal("100.00"))
        self.assertEqual(pagamento.valor_taxa, Decimal("1.00"))
        self.assertEqual(pagamento.valor_liquido, Decimal("99.00"))
        self.assertEqual(pagamento.conta_bancaria, self.caixa)
        self.assertEqual(venda.valor_pago, Decimal("100.00"))
