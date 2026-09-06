from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Empresa, Filial, PerfilAcesso, RegistroAuditoria, Usuario
from apps.cadastros.models import Cliente, Fornecedor, FornecedorFilial, Funcionario
from apps.financeiro.constants.enums import StatusContaPagar, StatusContaReceber, TipoFormaPagamento
from apps.financeiro.models import ContaBancaria, FormaPagamento, PlanoContabil, PlanoContas
from apps.financeiro.models.extrato import ExtratoBancario
from apps.financeiro.models.formas_pagamento import TaxaParcelamento
from apps.financeiro.models.receber_pagar import (
    ContaPagar, ContaReceber, PagamentoContaPagar, PagamentoContaReceber,
)
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
            tipo_lancamento="transferencia",
        )
        ExtratoBancario.objects.create(
            filial=self.filial, conta_bancaria=self.caixa, data_lancamento=date(2026, 8, 21),
            historico="Transferencia do banco", valor=Decimal("30.00"), origem="manual",
            tipo_lancamento="transferencia",
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
        self.assertEqual(posicao["total_entradas"], Decimal("80.00"))
        self.assertEqual(posicao["total_saidas"], Decimal("40.00"))
        self.assertEqual(posicao["variacao_dia"], Decimal("40.00"))
        self.assertEqual(posicao["total_fechamento"], Decimal("210.00"))
        saldos = {conta.descricao: conta.posicao_fechamento for conta in posicao["contas"]}
        self.assertEqual(saldos["Banco principal"], Decimal("170.00"))
        self.assertEqual(saldos["Dinheiro em caixa"], Decimal("40.00"))
        self.assertNotIn(
            "Transferencia para caixa",
            {movimento.descricao for movimento in posicao["extrato"]},
        )
        self.assertNotIn(
            "Transferencia do banco",
            {movimento.descricao for movimento in posicao["extrato"]},
        )
        self.assertTrue(posicao["possui_caixa_dinheiro"])
        self.assertNotIn(
            "Sem forma vinculada",
            {item["nome"] for item in posicao["totais_forma_entrada"]},
        )
        self.assertNotIn(
            "Sem forma vinculada",
            {item["nome"] for item in posicao["totais_forma_saida"]},
        )

    def test_comprovante_usa_venda_de_origem_e_nao_id_do_pagamento(self):
        self._criar_cenario()
        venda = VendaPDV.objects.get(filial=self.filial)
        pagamento = PagamentoVendaPDV.objects.create(
            venda_pdv=venda, forma_pagamento=self.forma, conta_bancaria=self.banco,
            valor=Decimal("20.00"),
        )
        self.assertNotEqual(pagamento.pk, venda.pk)
        response = self.client.get(reverse("financeiro:posicao_diaria"), {
            "data": "2026-08-21", "origem": "venda", "movimento": pagamento.pk,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["detalhe"].venda_pdv_id, venda.pk)
        self.assertContains(response, "Comprovante da venda")
        self.assertContains(response, f"visualizarComprovante({venda.pk})")
        self.assertNotContains(response, f"visualizarComprovante({pagamento.pk})")

    def test_movimento_manual_nao_oferece_comprovante_de_venda(self):
        self._criar_cenario()
        movimento = ExtratoBancario.objects.get(historico="Transferencia para caixa")
        response = self.client.get(reverse("financeiro:posicao_diaria"), {
            "data": "2026-08-21", "origem": "manual", "movimento": movimento.pk,
        })
        self.assertIsNone(response.context["detalhe"])
        self.assertNotContains(response, "Comprovante da venda")

    def test_tela_exibe_entradas_saidas_e_atalhos(self):
        self._criar_cenario()
        response = self.client.get(reverse("financeiro:posicao_diaria"), {"data": "2026-08-21"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Posição Diária de Caixa")
        self.assertContains(response, "Imprimir relatórios")
        self.assertContains(response, reverse("financeiro:posicao_diaria_relatorio"))
        self.assertContains(response, reverse("financeiro:pagar_relatorio"))
        self.assertContains(response, reverse("financeiro:receber_relatorio"))
        self.assertContains(response, reverse("financeiro:pagar_pagas_relatorio"))
        for opcao in (
            "Hoje", "Ontem", "Esta semana", "7 dias", "15 dias", "30 dias",
            "Este mês", "Personalizado", "Data inicial", "Data final",
        ):
            self.assertContains(response, opcao)
        self.assertContains(response, "Venda #1")
        self.assertContains(response, "Compra de material de limpeza")
        self.assertContains(response, "Contas a receber")
        self.assertContains(response, "Adicionar entrada manual")
        self.assertContains(response, "Adicionar conta a pagar")
        self.assertContains(response, reverse("financeiro:pagar_criar") + "?modal=1")
        self.assertContains(response, reverse("financeiro:despesa_paga_criar") + "?modal=1")
        self.assertContains(response, "Transferir entre contas")
        self.assertNotContains(response, "Transferencia para caixa")
        self.assertNotContains(response, "Transferencia do banco")
        self.assertEqual(len(response.context["dias_mes"]), 31)
        self.assertContains(response, 'aria-label="Ver dias anteriores"')
        self.assertContains(response, 'aria-label="Ver dias posteriores"')
        self.assertContains(response, "tituloPagarModal")
        self.assertContains(response, "abrirPagamentoTitulo")
        self.assertContains(response, "enviarPagamentoTitulo")
        self.assertContains(response, "pc-reconcile")
        self.assertContains(response, "Agrupar por forma de pagamento")
        self.assertContains(
            response,
            "if (resposta.ok) { window.location.reload(); return; }",
            count=2,
        )

    def test_relatorio_imprimivel_reune_movimentos_resumo_e_saldos(self):
        self._criar_cenario()

        response = self.client.get(
            reverse("financeiro:posicao_diaria_relatorio"),
            {"data": "2026-08-21", "periodo": "hoje"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "financeiro/posicao_diaria_relatorio.html")
        for texto in (
            "Relatório da Posição de Caixa", "21/08/2026", "Entradas", "Saídas",
            "Venda #1", "Compra de material de limpeza", "Forma / conta", "saídas e taxas",
            "Saldo inicial", "Resultado do dia", "Saldo final", "Saldos das contas bancárias",
            "Banco principal", "Dinheiro em caixa", "Exportar PDF",
        ):
            self.assertContains(response, texto)
        self.assertNotContains(response, "Transferencia para caixa")
        self.assertEqual(response.context["posicao"]["total_abertura"], Decimal("170.00"))
        self.assertEqual(response.context["posicao"]["total_fechamento"], Decimal("210.00"))
        self.assertContains(response, "size:A4 portrait")
        self.assertContains(response, "orientation:'portrait'")
        self.assertContains(response, "cr-pdf-exporting")
        self.assertContains(response, "margin: 5")
        self.assertContains(response, "avoid:['.cr-day-table thead','.cr-line','.cr-summary','.cr-accounts']")
        self.assertNotContains(response, 'class="cr-fees"')

    def test_relatorio_separa_dias_ordena_e_exibe_taxas_como_despesas(self):
        for data_movimento, historico, valor in (
            (date(2026, 8, 20), "Entrada quinta", Decimal("25.00")),
            (date(2026, 8, 21), "Saída sexta", Decimal("-10.00")),
        ):
            ExtratoBancario.objects.create(
                filial=self.filial,
                conta_bancaria=self.banco,
                data_lancamento=data_movimento,
                historico=historico,
                valor=valor,
                origem="manual",
            )

        response = self.client.get(reverse("financeiro:posicao_diaria_relatorio"), {
            "data": "2026-08-21",
            "periodo": "personalizado",
            "data_inicio": "2026-08-20",
            "data_fim": "2026-08-21",
            "ordem": "conta",
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [dia["data"] for dia in response.context["dias_relatorio"]],
            [date(2026, 8, 21), date(2026, 8, 20)],
        )
        self.assertContains(response, "Sexta-feira · 21/08/2026")
        self.assertContains(response, "Quinta-feira · 20/08/2026")
        self.assertNotContains(response, "Resumo das taxas", html=False)
        self.assertContains(response, '<table class="cr-day-table"', count=2)
        self.assertContains(response, "table-header-group")
        self.assertNotContains(response, "continuação")

    def test_relatorio_filtra_saida_por_categoria_fornecedor_e_funcionario(self):
        categoria = self._categoria_despesa("Uniformes da equipe")
        fornecedor = Fornecedor.objects.create(
            filial=self.filial,
            tipo_pessoa="J",
            razao_social="Fornecedor selecionado",
            cpf_cnpj="11222333000181",
        )
        FornecedorFilial.objects.create(fornecedor=fornecedor, filial=self.filial)
        funcionario = Funcionario.objects.create(
            filial=self.filial,
            nome="Funcionário selecionado",
            cpf="12345678901",
        )
        conta = ContaPagar.objects.create(
            filial=self.filial,
            fornecedor=fornecedor,
            funcionario=funcionario,
            plano_contas=categoria,
            descricao_despesa="Despesa que deve aparecer",
            valor_original=Decimal("31.00"),
            valor_final=Decimal("31.00"),
            valor_pago=Decimal("31.00"),
            valor_saldo=Decimal("0.00"),
            data_emissao=date(2026, 8, 21),
            data_vencimento=date(2026, 8, 21),
            data_pagamento=date(2026, 8, 21),
            status=StatusContaPagar.PAGO,
            usuario=self.usuario,
        )
        PagamentoContaPagar.objects.create(
            filial=self.filial,
            conta_pagar=conta,
            data_pagamento=date(2026, 8, 21),
            valor_pago=Decimal("31.00"),
            forma_pagamento=self.forma,
            conta_bancaria=self.banco,
            usuario=self.usuario,
        )
        outra = ContaPagar.objects.create(
            filial=self.filial,
            descricao_despesa="Despesa que deve ficar fora",
            valor_original=Decimal("12.00"),
            valor_final=Decimal("12.00"),
            valor_pago=Decimal("12.00"),
            valor_saldo=Decimal("0.00"),
            data_emissao=date(2026, 8, 21),
            data_vencimento=date(2026, 8, 21),
            data_pagamento=date(2026, 8, 21),
            status=StatusContaPagar.PAGO,
            usuario=self.usuario,
        )
        PagamentoContaPagar.objects.create(
            filial=self.filial,
            conta_pagar=outra,
            data_pagamento=date(2026, 8, 21),
            valor_pago=Decimal("12.00"),
            forma_pagamento=self.forma,
            conta_bancaria=self.banco,
            usuario=self.usuario,
        )

        response = self.client.get(reverse("financeiro:posicao_diaria_relatorio"), {
            "data": "2026-08-21",
            "periodo": "hoje",
            "categoria": categoria.pk,
            "fornecedor": fornecedor.pk,
            "funcionario": funcionario.pk,
        })

        self.assertContains(response, "Despesa que deve aparecer")
        self.assertNotContains(response, "Despesa que deve ficar fora")
        self.assertContains(response, "Categoria: Uniformes da equipe")
        self.assertContains(response, "Fornecedor: Fornecedor selecionado")
        self.assertContains(response, "Funcionário: Funcionário selecionado")
        self.assertEqual(response.context["posicao"]["total_saidas"], Decimal("31.00"))

    def test_quatro_relatorios_usam_folha_a4_vertical(self):
        templates = Path(__file__).resolve().parents[1] / "templates" / "financeiro"
        arquivos = (
            templates / "posicao_diaria_relatorio.html",
            templates / "pagar" / "relatorio.html",
            templates / "receber" / "relatorio.html",
            templates / "pagar" / "relatorio_pagas.html",
        )
        for arquivo in arquivos:
            with self.subTest(arquivo=arquivo.name):
                conteudo = arquivo.read_text(encoding="utf-8")
                self.assertIn("A4 portrait", conteudo)
                self.assertNotIn("A4 landscape", conteudo)

        posicao = (templates / "posicao_diaria_relatorio.html").read_text(encoding="utf-8")
        contas_pagas = (templates / "pagar" / "relatorio_pagas.html").read_text(encoding="utf-8")
        self.assertIn("#cash-report table tr:hover", posicao)
        self.assertIn("#relatorio-contas-pagas table tr:hover", contas_pagas)
        self.assertIn("#relatorio-contas-pagas .text-gray-500", contas_pagas)
        self.assertIn("color:#1f2937 !important", contas_pagas)

    def test_relatorio_contas_pagas_consolida_taxas_automaticas(self):
        dados_base = {
            "filial": self.filial,
            "data_emissao": date(2026, 9, 4),
            "data_vencimento": date(2026, 9, 4),
            "data_pagamento": date(2026, 9, 4),
            "status": StatusContaPagar.PAGO,
            "valor_saldo": Decimal("0.00"),
            "forma_pagamento": self.forma,
            "conta_bancaria": self.banco,
            "usuario": self.usuario,
        }
        for indice, valor in enumerate((Decimal("3.47"), Decimal("0.79")), start=1):
            ContaPagar.objects.create(
                **dados_base,
                descricao_despesa=f"Taxa automática {indice}",
                documento_tipo="taxa_extrato",
                documento_id=indice,
                valor_original=valor,
                valor_final=valor,
                valor_pago=valor,
            )
        ContaPagar.objects.create(
            **dados_base,
            descricao_despesa="Compra comum",
            valor_original=Decimal("20.00"),
            valor_final=Decimal("20.00"),
            valor_pago=Decimal("20.00"),
        )

        response = self.client.get(reverse("financeiro:pagar_pagas_relatorio"), {
            "data_ini": "2026-09-04",
            "data_fim": "2026-09-04",
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "TAXAS E TARIFAS CONSOLIDADAS", count=1)
        self.assertContains(response, "2 cobranças automáticas")
        self.assertContains(response, "R$ 4,26")
        self.assertNotContains(response, "Taxa automática 1")
        self.assertNotContains(response, "Taxa automática 2")
        self.assertContains(response, "Compra comum")
        self.assertEqual(response.context["totais"]["quantidade_exibida"], 2)

    def test_transferencia_com_taxa_fica_fora_do_extrato_e_reduz_saldo(self):
        forma = FormaPagamento.objects.create(
            empresa=self.empresa,
            filial=self.filial,
            descricao="TED tarifada",
            tipo=TipoFormaPagamento.TED,
            taxa_administrativa=Decimal("2.00"),
            taxa_fixa=Decimal("0.50"),
        )

        response = self.client.post(reverse("financeiro:contas_bancarias"), {
            "acao": "lancar_movimento",
            "tipo": "transferencia",
            "conta_origem": self.banco.pk,
            "conta_destino": self.caixa.pk,
            "data_lancamento": "2026-08-21",
            "valor": "100.00",
            "forma_pagamento": forma.pk,
        })

        self.assertEqual(response.status_code, 302)
        posicao = PosicaoDiariaCaixaService(self.filial, date(2026, 8, 21)).gerar()
        self.assertEqual(posicao["total_entradas"], Decimal("0.00"))
        self.assertEqual(posicao["total_saidas_bancarias"], Decimal("0.00"))
        self.assertEqual(posicao["total_taxas_entradas"], Decimal("2.50"))
        self.assertEqual(posicao["total_saidas"], Decimal("2.50"))
        self.assertEqual(posicao["variacao_dia"], Decimal("-2.50"))
        self.assertEqual(posicao["total_fechamento"], Decimal("147.50"))
        self.assertFalse(posicao["extrato"])

    def test_previsoes_de_receber_e_pagar_abrem_em_hoje_antes_dos_saldos(self):
        hoje = date(2026, 8, 24)
        conta_atrasada = ContaPagar.objects.create(
            filial=self.filial,
            descricao_despesa="Conta atrasada",
            valor_original=Decimal("60.00"),
            valor_final=Decimal("60.00"),
            valor_pago=Decimal("0.00"),
            valor_saldo=Decimal("60.00"),
            data_emissao=date(2026, 8, 20),
            data_vencimento=date(2026, 8, 23),
            status=StatusContaPagar.VENCIDO,
            forma_pagamento_prevista=self.forma,
            conta_bancaria=self.banco,
            usuario=self.usuario,
        )
        conta_hoje = ContaPagar.objects.create(
            filial=self.filial,
            descricao_despesa="Conta prevista hoje",
            valor_original=Decimal("120.00"),
            valor_final=Decimal("120.00"),
            valor_pago=Decimal("0.00"),
            valor_saldo=Decimal("120.00"),
            data_emissao=hoje,
            data_vencimento=hoje,
            status=StatusContaPagar.ABERTO,
            forma_pagamento_prevista=self.forma,
            conta_bancaria=self.banco,
            usuario=self.usuario,
        )
        conta_amanha = ContaPagar.objects.create(
            filial=self.filial,
            descricao_despesa="Conta prevista amanhã",
            valor_original=Decimal("80.00"),
            valor_final=Decimal("80.00"),
            valor_pago=Decimal("0.00"),
            valor_saldo=Decimal("80.00"),
            data_emissao=hoje,
            data_vencimento=date(2026, 8, 25),
            status=StatusContaPagar.ABERTO,
            usuario=self.usuario,
        )

        response = self.client.get(
            reverse("financeiro:posicao_diaria"), {"data": hoje.isoformat()},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["previsao_periodo"], "30")
        self.assertEqual(response.context["pagar_previsao_periodo"], "hoje")
        self.assertEqual(
            [conta.pk for conta in response.context["contas_pagar_previstas"]],
            [conta_atrasada.pk, conta_hoje.pk],
        )
        self.assertContains(response, "Atrasada")
        self.assertContains(response, "Pesquisar por descrição ou nome", count=2)
        self.assertContains(response, "posicionarDiaSelecionado")
        self.assertContains(response, ".normalize('NFD')")
        self.assertContains(response, ".replace(/[\\u0300-\\u036f]/g, '')")
        self.assertContains(response, f"abrirTituloPagar('{reverse('financeiro:pagar_detail', args=[conta_hoje.pk])}')")
        self.assertContains(response, "atualizarPrevisoes($el.href)")
        self.assertContains(response, "pc-payables-forecast mt-5")
        html = response.content.decode()
        self.assertLess(html.index("Recebimentos previstos"), html.index("Contas a pagar previstas"))
        self.assertLess(html.index("Contas a pagar previstas"), html.index("Saldos por conta"))

        response = self.client.get(
            reverse("financeiro:posicao_diaria"),
            {"data": hoje.isoformat(), "pagar_previsao": "7"},
        )
        self.assertEqual(
            {conta.pk for conta in response.context["contas_pagar_previstas"]},
            {conta_atrasada.pk, conta_hoje.pk, conta_amanha.pk},
        )

        parcial = self.client.get(
            reverse("financeiro:posicao_diaria"),
            {"data": hoje.isoformat(), "pagar_previsao": "7", "partial": "previsoes"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(parcial.status_code, 200)
        self.assertContains(parcial, "Conta prevista hoje")
        self.assertContains(parcial, "Conta prevista amanhã")
        self.assertContains(parcial, "Conta atrasada")
        self.assertNotContains(parcial, "Saldos por conta")

    def test_recebimentos_padrao_incluem_atrasados_e_proximos_trinta_dias(self):
        referencia = date(2026, 8, 24)
        cliente = Cliente.objects.create(
            filial=self.filial, razao_social="Cliente previsões", tipo_pessoa="F",
            cpf_cnpj="12345678933",
        )
        atrasada = ContaReceber.objects.create(
            filial=self.filial, cliente=cliente,
            valor_original=Decimal("90.00"), valor_final=Decimal("90.00"),
            valor_saldo=Decimal("90.00"), data_emissao=date(2026, 8, 1),
            data_vencimento=date(2026, 8, 20), status=StatusContaReceber.VENCIDO,
        )
        futura = ContaReceber.objects.create(
            filial=self.filial, cliente=cliente,
            valor_original=Decimal("110.00"), valor_final=Decimal("110.00"),
            valor_saldo=Decimal("110.00"), data_emissao=referencia,
            data_vencimento=date(2026, 9, 10), status=StatusContaReceber.ABERTO,
        )
        fora_periodo = ContaReceber.objects.create(
            filial=self.filial, cliente=cliente,
            valor_original=Decimal("130.00"), valor_final=Decimal("130.00"),
            valor_saldo=Decimal("130.00"), data_emissao=referencia,
            data_vencimento=date(2026, 9, 30), status=StatusContaReceber.ABERTO,
        )

        response = self.client.get(
            reverse("financeiro:posicao_diaria"), {"data": referencia.isoformat()},
        )

        ids = {
            item["registro_id"] for item in response.context["posicao"]["previsoes"]
            if item["origem_codigo"] == "receber"
        }
        self.assertIn(atrasada.pk, ids)
        self.assertIn(futura.pk, ids)
        self.assertNotIn(fora_periodo.pk, ids)
        self.assertContains(response, "Atrasado")

    def test_pagamento_de_titulo_abre_e_valida_no_modal(self):
        conta = ContaPagar.objects.create(
            filial=self.filial,
            descricao_despesa="Conta para pagar no modal",
            valor_original=Decimal("90.00"),
            valor_final=Decimal("90.00"),
            valor_saldo=Decimal("90.00"),
            data_emissao=date(2026, 8, 24),
            data_vencimento=date(2026, 8, 25),
            status=StatusContaPagar.ABERTO,
            usuario=self.usuario,
            forma_pagamento_prevista=self.forma,
            conta_bancaria=self.banco,
        )

        url = reverse("financeiro:pagar_pagar", args=[conta.pk])
        response = self.client.get(url, {"modal": "1"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Registrar pagamento")
        self.assertContains(response, '@submit.prevent="enviando=true; enviarPagamentoTitulo($event)"')
        self.assertContains(response, reverse("financeiro:pagar_detail", args=[conta.pk]))

        response = self.client.post(
            f"{url}?modal=1",
            {},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Registrar pagamento", status_code=400)

    def test_recebimento_de_titulo_abre_e_valida_no_modal(self):
        cliente = Cliente.objects.create(
            filial=self.filial,
            razao_social="Cliente modal receber",
            tipo_pessoa="F",
            cpf_cnpj="12345678906",
        )
        conta = ContaReceber.objects.create(
            filial=self.filial,
            cliente=cliente,
            valor_original=Decimal("180.00"),
            valor_final=Decimal("180.00"),
            valor_saldo=Decimal("180.00"),
            data_emissao=date(2026, 8, 22),
            data_vencimento=date(2026, 8, 25),
            status=StatusContaReceber.ABERTO,
            forma_pagamento=self.forma,
            conta_bancaria=self.banco,
        )

        detalhe = self.client.get(
            reverse("financeiro:receber_detail", args=[conta.pk]),
            {"modal": "1"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(detalhe.status_code, 200)
        self.assertContains(detalhe, "abrirRecebimentoTitulo")

        url = reverse("financeiro:receber_baixar", args=[conta.pk])
        response = self.client.get(url, {"modal": "1"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Registrar recebimento")
        self.assertContains(response, "Receber tudo")
        self.assertContains(response, "Parcial")
        self.assertContains(response, '@submit.prevent="enviando=true; enviarRecebimentoTitulo($event)"')

        response = self.client.post(
            f"{url}?modal=1",
            {},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Registrar recebimento", status_code=400)

        pagina = self.client.get(url)
        self.assertEqual(pagina.status_code, 200)
        self.assertContains(pagina, "Receber tudo")
        self.assertNotContains(pagina, "?modal=1")

    def test_lista_receber_mostra_adiado_e_abre_baixa_em_modal(self):
        cliente = Cliente.objects.create(
            filial=self.filial,
            razao_social="Cliente adiado",
            tipo_pessoa="F",
            cpf_cnpj="12345678905",
        )
        ContaReceber.objects.create(
            filial=self.filial,
            cliente=cliente,
            valor_original=Decimal("120.00"),
            valor_final=Decimal("120.00"),
            valor_saldo=Decimal("120.00"),
            data_emissao=date(2026, 8, 20),
            data_vencimento=date(2026, 8, 30),
            status=StatusContaReceber.NEGOCIADO,
            forma_pagamento=self.forma,
            conta_bancaria=self.banco,
        )

        response = self.client.get(reverse("financeiro:receber_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Adiado")
        self.assertNotContains(response, "Negociado")
        self.assertContains(response, "abrirRecebimentoTitulo($el.href)")

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

    def test_posicao_organiza_blocos_e_oferece_edicao_em_sobreposicao(self):
        response = self.client.get(reverse("financeiro:posicao_diaria"), {"data": "2026-08-21"})
        conteudo = response.content.decode()

        self.assertLess(conteudo.index('id="previsoes-posicao-diaria"'), conteudo.index("Saldos por conta"))
        self.assertLess(conteudo.index("Saldos por conta"), conteudo.index("Conferência e somatórios"))
        self.assertIn("async abrirEdicaoTitulo(url)", conteudo)
        self.assertIn("async enviarEdicaoTitulo(event)", conteudo)
        self.assertIn("async enviarPagamentoTitulo(event)", conteudo)
        self.assertIn("async enviarRecebimentoTitulo(event)", conteudo)
        self.assertIn("querySelector(`[name='${nome}']`)", conteudo)
        self.assertNotIn('querySelector(`[name="${nome}"]`)', conteudo)

    def test_posicao_mostra_dez_entradas_cinco_saidas_e_botao_ver_mais(self):
        for indice in range(11):
            ExtratoBancario.objects.create(
                filial=self.filial,
                conta_bancaria=self.banco,
                data_lancamento=date(2026, 8, 21),
                historico=f"Entrada recente {indice}",
                valor=Decimal("10.00"),
                origem="manual",
            )
            ExtratoBancario.objects.create(
                filial=self.filial,
                conta_bancaria=self.banco,
                data_lancamento=date(2026, 8, 21),
                historico=f"Saída recente {indice}",
                valor=Decimal("-5.00"),
                origem="manual",
            )

        response = self.client.get(reverse("financeiro:posicao_diaria"), {"data": "2026-08-21"})

        self.assertContains(response, "Ver mais entradas (")
        self.assertContains(response, "Ver mais saídas (")
        self.assertContains(response, 'x-show="entradasExpandidas"', count=1)
        self.assertContains(response, 'x-show="saidasExpandidas"', count=6)
        self.assertContains(response, "Mostrar somente as 5 mais recentes")

    def test_saida_exibe_fornecedor_sem_abrir_o_card(self):
        fornecedor = Fornecedor.objects.create(
            filial=self.filial,
            tipo_pessoa="J",
            razao_social="Fornecedor visível no card",
            cpf_cnpj="11222333000181",
        )
        conta = ContaPagar.objects.create(
            filial=self.filial,
            fornecedor=fornecedor,
            descricao_despesa="Compra com beneficiário",
            valor_original=Decimal("25.00"),
            valor_final=Decimal("25.00"),
            valor_pago=Decimal("25.00"),
            valor_saldo=Decimal("0.00"),
            data_emissao=date(2026, 8, 21),
            data_vencimento=date(2026, 8, 21),
            data_pagamento=date(2026, 8, 21),
            status=StatusContaPagar.PAGO,
            usuario=self.usuario,
        )
        PagamentoContaPagar.objects.create(
            filial=self.filial,
            conta_pagar=conta,
            data_pagamento=date(2026, 8, 21),
            valor_pago=Decimal("25.00"),
            forma_pagamento=self.forma,
            conta_bancaria=self.banco,
            usuario=self.usuario,
        )

        response = self.client.get(reverse("financeiro:posicao_diaria"), {"data": "2026-08-21"})

        self.assertContains(response, "Fornecedor/funcionário: Fornecedor visível no card")

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
        self.assertEqual(posicao["total_entradas"], Decimal("77.90"))
        self.assertEqual(posicao["total_saidas"], Decimal("42.10"))
        self.assertEqual(posicao["total_saidas_bancarias"], Decimal("40.00"))
        self.assertEqual(posicao["variacao_dia"], Decimal("37.90"))
        self.assertEqual(posicao["total_fechamento"], Decimal("207.90"))
        self.assertEqual(posicao["total_taxas_entradas"], Decimal("2.10"))
        self.assertEqual(posicao["total_liquido_entradas"], Decimal("77.90"))
        self.assertEqual(posicao["taxas_por_forma"][0]["nome"], self.forma.descricao)

        response = self.client.get(reverse("financeiro:posicao_diaria"), {"data": "2026-08-21"})
        self.assertContains(response, "Original: R$ 80,00")
        self.assertContains(response, "Taxa: R$ 2,10 (2,00%)")
        self.assertContains(response, "Taxas por transação (Cartão, PIX e boleto)")
        self.assertContains(response, "Detalhamento das taxas")
        self.assertContains(response, "Taxas por transação")
        self.assertNotContains(response, 'class="pc-fee-summary')
        self.assertContains(response, "R$ 2,10")

        relatorio = self.client.get(
            reverse("financeiro:posicao_diaria_relatorio"), {"data": "2026-08-21"}
        )
        self.assertContains(relatorio, "Taxas financeiras", count=1)
        self.assertContains(relatorio, "Taxas de entradas:", count=1)
        self.assertContains(relatorio, "Taxas de saídas:", count=1)
        self.assertContains(relatorio, "Total das taxas:", count=1)
        self.assertNotContains(relatorio, 'class="cr-fees"')
        self.assertEqual(relatorio.context["total_entradas_relatorio"], Decimal("80.00"))
        self.assertEqual(relatorio.context["total_saidas_relatorio"], Decimal("42.10"))
        taxas = [
            linha["saida"]
            for dia in relatorio.context["dias_relatorio"]
            for linha in dia["linhas"]
            if linha["saida"] and getattr(linha["saida"], "tipo_taxa", "")
        ]
        self.assertEqual(len(taxas), 1)
        self.assertEqual(taxas[0].taxa_recebimentos, Decimal("2.10"))
        self.assertEqual(taxas[0].taxa_pagamentos, Decimal("0"))
        self.assertEqual(taxas[0].saida, Decimal("2.10"))

    def test_pagamento_com_juros_nao_soma_o_juros_duas_vezes(self):
        conta = ContaPagar.objects.create(
            filial=self.filial,
            valor_original=Decimal("90.00"),
            valor_juros=Decimal("2.00"),
            valor_final=Decimal("92.00"),
            valor_pago=Decimal("92.00"),
            valor_saldo=Decimal("0.00"),
            descricao_despesa="Ajuda de custo",
            data_emissao=date(2026, 8, 24),
            data_vencimento=date(2026, 8, 24),
            data_pagamento=date(2026, 8, 24),
            status=StatusContaPagar.PAGO,
            usuario=self.usuario,
        )
        PagamentoContaPagar.objects.create(
            filial=self.filial,
            conta_pagar=conta,
            data_pagamento=date(2026, 8, 24),
            valor_pago=Decimal("92.00"),
            valor_juros=Decimal("2.00"),
            forma_pagamento=self.forma,
            conta_bancaria=self.banco,
            usuario=self.usuario,
        )

        posicao = PosicaoDiariaCaixaService(self.filial, date(2026, 8, 24)).gerar()
        movimento = next(mov for mov in posicao["saidas"] if mov.descricao == "Ajuda de custo")

        self.assertEqual(movimento.saida, Decimal("92.00"))
        self.assertEqual(posicao["total_saidas_bancarias"], Decimal("92.00"))

    def test_tarifa_de_pagamento_reduz_caixa_sem_duplicar_lancamento(self):
        self.forma.tarifa_pagamento_fixa = Decimal("0.50")
        self.forma.save(update_fields=["tarifa_pagamento_fixa"])
        conta = ContaPagar.objects.create(
            filial=self.filial,
            valor_original=Decimal("100.00"),
            valor_final=Decimal("100.00"),
            valor_pago=Decimal("100.00"),
            valor_saldo=Decimal("0.00"),
            descricao_despesa="Fornecedor teste",
            data_emissao=date(2026, 8, 24),
            data_vencimento=date(2026, 8, 24),
            data_pagamento=date(2026, 8, 24),
            status=StatusContaPagar.PAGO,
            usuario=self.usuario,
        )
        PagamentoContaPagar.objects.create(
            filial=self.filial,
            conta_pagar=conta,
            data_pagamento=date(2026, 8, 24),
            valor_pago=Decimal("100.00"),
            forma_pagamento=self.forma,
            conta_bancaria=self.banco,
            usuario=self.usuario,
        )

        posicao = PosicaoDiariaCaixaService(self.filial, date(2026, 8, 24)).gerar()
        principal = next(mov for mov in posicao["saidas"] if mov.origem_codigo == "pagar")

        self.assertEqual(principal.saida, Decimal("100.00"))
        self.assertEqual(principal.valor_bruto, Decimal("100.00"))
        self.assertEqual(principal.valor_taxa, Decimal("0.50"))
        self.assertEqual(principal.valor_final_taxa, Decimal("100.00"))
        self.assertTrue(principal.taxa_em_pagamento)
        self.assertFalse(any(mov.origem_codigo == "taxa_pagamento" for mov in posicao["saidas"]))
        self.assertEqual(posicao["total_saidas_bancarias"], Decimal("100.00"))
        self.assertEqual(posicao["total_taxas_pagamentos"], Decimal("0.50"))
        self.assertEqual(posicao["total_taxas_transacoes"], Decimal("0.50"))
        self.assertEqual(posicao["total_saidas"], Decimal("100.50"))
        self.assertEqual(posicao["variacao_dia"], Decimal("-100.50"))
        self.assertEqual(posicao["total_fechamento"], Decimal("49.50"))

        conta.refresh_from_db()
        self.assertEqual(conta.valor_pago, Decimal("100.00"))
        self.assertEqual(conta.valor_saldo, Decimal("0.00"))
        # A TARIFA E' LANCADA COMO DESPESA PROPRIA, para ter plano de contas e
        # conta contabil -- quem evita a saida em dobro e' a posicao diaria,
        # que deixa os `taxa_*` de fora das saidas e soma o valor dentro do
        # pagamento principal. Cobrar que o lancamento nao exista testava um
        # desenho que o sistema deixou de ter.
        tarifa = ContaPagar.all_objects.get(
            documento_tipo="taxa_pagamento",
            documento_id=conta.pagamentos.first().pk,
        )
        self.assertEqual(tarifa.valor_pago, Decimal("0.50"))
        self.assertFalse(
            any(mov.registro_id == tarifa.pk and mov.origem_codigo == "pagar"
                for mov in posicao["saidas"]),
            "a tarifa entrou como saida propria e dobrou o valor do dia",
        )

        response = self.client.get(reverse("financeiro:posicao_diaria"), {"data": "2026-08-24"})
        self.assertContains(response, "Taxas em pagamentos")
        self.assertContains(response, "Pago")
        self.assertContains(response, "Fornecedor teste")
        self.assertContains(response, "Taxa: R$ 0,50")
        self.assertContains(response, "R$ 100,50")
        self.assertNotContains(response, "Tarifa bancaria")

        relatorio = self.client.get(
            reverse("financeiro:posicao_diaria_relatorio"), {"data": "2026-08-24"}
        )
        self.assertContains(relatorio, "Taxas financeiras", count=1)
        self.assertContains(relatorio, "Taxas de entradas:", count=1)
        self.assertContains(relatorio, "Taxas de saídas:", count=1)
        self.assertContains(relatorio, "Total das taxas:", count=1)
        self.assertNotContains(relatorio, "Taxa adicional")
        self.assertEqual(relatorio.context["total_saidas_relatorio"], Decimal("100.50"))
        taxas = [
            linha["saida"]
            for dia in relatorio.context["dias_relatorio"]
            for linha in dia["linhas"]
            if linha["saida"] and getattr(linha["saida"], "tipo_taxa", "")
        ]
        self.assertEqual(len(taxas), 1)
        self.assertEqual(taxas[0].taxa_recebimentos, Decimal("0"))
        self.assertEqual(taxas[0].taxa_pagamentos, Decimal("0.50"))
        self.assertEqual(taxas[0].saida, Decimal("0.50"))

        dia_seguinte = PosicaoDiariaCaixaService(self.filial, date(2026, 8, 25)).gerar()
        saldo_banco = next(item for item in dia_seguinte["contas"] if item.pk == self.banco.pk)
        self.assertEqual(saldo_banco.posicao_abertura, Decimal("-0.50"))

    def test_tarifa_informada_como_zero_nao_e_cobrada(self):
        self.forma.tarifa_pagamento_fixa = Decimal("0.50")
        self.forma.save(update_fields=["tarifa_pagamento_fixa"])
        conta = ContaPagar.objects.create(
            filial=self.filial,
            valor_original=Decimal("100.00"),
            valor_final=Decimal("100.00"),
            valor_pago=Decimal("100.00"),
            valor_saldo=Decimal("0.00"),
            descricao_despesa="Pagamento sem tarifa",
            data_emissao=date(2026, 8, 24),
            data_vencimento=date(2026, 8, 24),
            data_pagamento=date(2026, 8, 24),
            status=StatusContaPagar.PAGO,
            usuario=self.usuario,
        )
        pagamento = PagamentoContaPagar.objects.create(
            filial=self.filial,
            conta_pagar=conta,
            data_pagamento=date(2026, 8, 24),
            valor_pago=Decimal("100.00"),
            tarifa_bancaria=Decimal("0.00"),
            forma_pagamento=self.forma,
            conta_bancaria=self.banco,
            usuario=self.usuario,
        )

        posicao = PosicaoDiariaCaixaService(self.filial, date(2026, 8, 24)).gerar()

        self.assertEqual(posicao["total_taxas_pagamentos"], Decimal("0.00"))
        self.assertEqual(posicao["total_saidas"], Decimal("100.00"))
        self.assertEqual(posicao["total_fechamento"], Decimal("50.00"))
        self.assertFalse(ContaPagar.all_objects.filter(
            documento_tipo="taxa_pagamento", documento_id=pagamento.pk,
        ).exists())

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

        self.assertNotContains(response, "TAXA 2,50% + R$ 0,30")
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
        segunda = PosicaoDiariaCaixaService(self.filial, date(2026, 8, 24)).gerar(
            incluir_previstos=True,
            previsao_inicio=date(2026, 8, 24),
            previsao_fim=date(2026, 8, 24),
        )
        self.assertEqual(sexta["total_entradas"], Decimal("0"))
        self.assertEqual(sexta["total_previsto"], Decimal("100.00"))
        conta_sexta = next(conta for conta in sexta["contas"] if conta.pk == self.banco.pk)
        self.assertEqual(conta_sexta.posicao_prevista_entrada, Decimal("100.00"))
        self.assertEqual(
            conta_sexta.posicao_saldo_projetado,
            conta_sexta.posicao_fechamento + Decimal("100.00"),
        )
        self.assertEqual(segunda["total_entradas"], Decimal("100.00"))
        self.assertEqual(segunda["total_previsto"], Decimal("0"))
        self.assertEqual(segunda["previsoes"], [])

    def test_venda_paga_sem_conta_nao_desaparece_da_entrada(self):
        self.forma.conta_bancaria_padrao = None
        self.forma.prazo_compensacao_dias_uteis = 1
        self.forma.save(update_fields=["conta_bancaria_padrao", "prazo_compensacao_dias_uteis"])
        venda = VendaPDV.objects.create(
            filial=self.filial, numero_venda=3, status="finalizada",
            valor_total=Decimal("95.00"), valor_pago=Decimal("95.00"), usuario=self.usuario,
            data_venda=datetime(2026, 8, 24, 7, 33, tzinfo=timezone.get_current_timezone()),
        )
        pagamento = PagamentoVendaPDV.objects.create(
            venda_pdv=venda, forma_pagamento=self.forma, valor=Decimal("95.00"),
        )

        posicao_venda = PosicaoDiariaCaixaService(self.filial, date(2026, 8, 24)).gerar()
        posicao_liquidacao = PosicaoDiariaCaixaService(self.filial, date(2026, 8, 25)).gerar()

        self.assertEqual(posicao_venda["total_entradas"], Decimal("0"))
        entrada = next(
            item for item in posicao_liquidacao["entradas"]
            if item.registro_id == pagamento.pk
        )
        self.assertIsNone(entrada.conta)
        self.assertEqual(entrada.entrada, Decimal("95.00"))
        self.assertEqual(posicao_liquidacao["total_entradas"], Decimal("95.00"))

    def test_recebimento_usa_conta_padrao_da_forma_quando_baixa_nao_informa_conta(self):
        cliente = Cliente.objects.create(
            filial=self.filial, razao_social="Cliente sem conta direta", tipo_pessoa="F",
            cpf_cnpj="12345678908",
        )
        # A BAIXA E' QUEM PAGA. A posicao diaria e' montada a partir dos
        # registros de pagamento, e nao do status do titulo -- e' o pagamento
        # que carrega conta, forma, taxa e data. Marcar a conta como PAGA na
        # mao monta um estado que o sistema nao produz (a migration 0050 ate'
        # criou os pagamentos dos titulos antigos justamente por isso).
        conta = ContaReceber.objects.create(
            filial=self.filial, cliente=cliente, valor_original=Decimal("70.00"),
            valor_final=Decimal("70.00"), valor_saldo=Decimal("70.00"),
            data_emissao=date(2026, 8, 24), data_vencimento=date(2026, 8, 24),
            forma_pagamento=self.forma, status=StatusContaReceber.ABERTO,
        )
        # Sem `conta_bancaria`: e' exatamente o caso do teste -- a baixa nao
        # informa a conta, e a forma tem que emprestar a dela.
        ContaReceberService.registrar_baixa(
            conta, date(2026, 8, 24), Decimal("70.00"), self.forma, self.usuario,
        )

        posicao = PosicaoDiariaCaixaService(self.filial, date(2026, 8, 24)).gerar()

        entrada = next(item for item in posicao["entradas"] if item.registro_id == conta.pk)
        self.assertEqual(entrada.conta, self.banco)
        self.assertEqual(entrada.entrada, Decimal("70.00"))

    def test_op_paga_entra_na_posicao_diaria(self):
        cliente = Cliente.objects.create(
            filial=self.filial, razao_social="Cliente OP", tipo_pessoa="F",
            cpf_cnpj="12345678907",
        )
        conta = ContaReceber.objects.create(
            filial=self.filial, cliente=cliente, valor_original=Decimal("150.00"),
            valor_final=Decimal("150.00"), valor_saldo=Decimal("150.00"),
            data_emissao=date(2026, 8, 24), data_vencimento=date(2026, 8, 24),
            data_liquidacao_prevista=date(2026, 8, 24), forma_pagamento=self.forma,
            status=StatusContaReceber.ABERTO, documento_tipo="pedido_moda", documento_id=123,
        )
        ContaReceberService.registrar_baixa(
            conta, date(2026, 8, 24), Decimal("150.00"), self.forma, self.usuario,
            conta_bancaria=self.banco,
        )

        posicao = PosicaoDiariaCaixaService(self.filial, date(2026, 8, 24)).gerar()

        entrada = next(item for item in posicao["entradas"] if item.registro_id == conta.pk)
        self.assertEqual(entrada.entrada, Decimal("150.00"))
        self.assertEqual(entrada.origem, "Venda")
        self.assertEqual(entrada.classificacao, "Venda de OP")
        self.assertEqual(entrada.op_url, reverse("moda:op2-detail", args=[123]))

    def test_recebimento_de_op_exibe_venda_e_abre_op_em_nova_aba(self):
        hoje = timezone.localdate()
        cliente = Cliente.objects.create(
            filial=self.filial, razao_social="Cliente venda OP", tipo_pessoa="F",
            cpf_cnpj="12345678906",
        )
        conta = ContaReceber.objects.create(
            filial=self.filial, cliente=cliente, valor_original=Decimal("100.00"),
            valor_final=Decimal("100.00"), valor_saldo=Decimal("100.00"),
            data_emissao=hoje, data_vencimento=hoje, forma_pagamento=self.forma,
            status=StatusContaReceber.ABERTO, documento_tipo="pedido_moda",
            documento_id=3, documento_numero="3",
        )
        ContaReceberService.registrar_baixa(
            conta, hoje, Decimal("100.00"), self.forma, self.usuario,
            conta_bancaria=self.banco,
        )

        posicao = PosicaoDiariaCaixaService(self.filial, hoje).gerar()
        movimento = next(item for item in posicao["entradas"] if item.registro_id == conta.pk)
        op_url = reverse("moda:op2-detail", args=[3])

        self.assertEqual(movimento.origem, "Venda")
        self.assertEqual(movimento.classificacao, "Venda de OP")
        self.assertEqual(movimento.descricao, "Venda OP #000003 - Cliente venda OP")
        self.assertEqual(movimento.op_url, op_url)

        response = self.client.get(reverse("financeiro:posicao_diaria"), {
            "data": hoje.isoformat(), "origem": "receber", "movimento": conta.pk,
        })
        self.assertContains(response, "Ver mais informações da OP")
        self.assertContains(response, f'href="{op_url}" target="_blank"')

    def test_venda_consulta_prazo_atual_da_forma_ao_registrar_pagamento(self):
        self.forma.prazo_compensacao_dias_uteis = 1
        self.forma.save(update_fields=["prazo_compensacao_dias_uteis"])
        forma_desatualizada = FormaPagamento.objects.get(pk=self.forma.pk)
        FormaPagamento.objects.filter(pk=self.forma.pk).update(
            prazo_compensacao_dias_uteis=0,
        )
        venda = VendaPDV.objects.create(
            filial=self.filial, numero_venda=90, status="finalizada",
            valor_total=Decimal("95.00"), valor_pago=Decimal("95.00"), usuario=self.usuario,
            data_venda=datetime(2026, 8, 24, 12, tzinfo=timezone.get_current_timezone()),
        )

        pagamento = PagamentoVendaPDV.objects.create(
            venda_pdv=venda, forma_pagamento=forma_desatualizada,
            conta_bancaria=self.banco, valor=Decimal("95.00"),
        )

        self.assertEqual(pagamento.prazo_compensacao_aplicado, 0)
        self.assertEqual(pagamento.data_liquidacao_prevista, date(2026, 8, 24))
        self.assertTrue(any(
            item.registro_id == pagamento.pk
            for item in PosicaoDiariaCaixaService(self.filial, date(2026, 8, 24)).gerar()["entradas"]
        ))

    def test_baixa_de_boleto_entra_no_dia_e_sai_das_previsoes(self):
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
            Decimal("80.00"),
        )
        self.assertEqual(
            PosicaoDiariaCaixaService(self.filial, date(2026, 8, 24)).gerar()["total_entradas"],
            Decimal("0"),
        )
        response = self.client.get(reverse("financeiro:posicao_diaria"), {
            "data": "2026-08-21", "previsao": "7d",
        })
        self.assertNotContains(response, "Conta a receber - Cliente boleto")
        posicao = PosicaoDiariaCaixaService(
            self.filial, date(2026, 8, 21),
        ).gerar(
            incluir_previstos=True,
            previsao_inicio=date(2026, 8, 21),
            previsao_fim=date(2026, 8, 28),
        )
        self.assertFalse(any(
            item["origem_codigo"] == "receber" and item["registro_id"] == conta.pk
            for item in posicao["previsoes"]
        ))

    def test_baixa_parcial_nao_antecipa_taxa_sobre_saldo_a_receber(self):
        self.forma.tipo = TipoFormaPagamento.BOLETO
        self.forma.taxa_fixa = Decimal("4.50")
        self.forma.save(update_fields=["tipo", "taxa_fixa"])
        cliente = Cliente.objects.create(
            filial=self.filial, razao_social="Cliente OP parcial", tipo_pessoa="F",
            cpf_cnpj="12345678903",
        )
        conta_recebida = ContaReceber.objects.create(
            filial=self.filial, cliente=cliente, valor_original=Decimal("100.00"),
            valor_final=Decimal("100.00"), valor_saldo=Decimal("100.00"),
            data_emissao=date(2026, 8, 27), data_vencimento=date(2026, 8, 27),
            forma_pagamento=self.forma, status=StatusContaReceber.ABERTO,
            documento_tipo="pedido_moda", documento_id=321, parcela=1, total_parcelas=2,
        )
        conta_restante = ContaReceber.objects.create(
            filial=self.filial, cliente=cliente, valor_original=Decimal("100.00"),
            valor_final=Decimal("100.00"), valor_saldo=Decimal("100.00"),
            data_emissao=date(2026, 8, 27), data_vencimento=date(2026, 8, 31),
            forma_pagamento=self.forma, status=StatusContaReceber.ABERTO,
            documento_tipo="pedido_moda", documento_id=321, parcela=2, total_parcelas=2,
        )
        ContaReceberService.registrar_baixa(
            conta_recebida, date(2026, 8, 27), Decimal("100.00"), self.forma, self.usuario,
            conta_bancaria=self.banco,
        )
        conta_recebida.refresh_from_db()

        posicao = PosicaoDiariaCaixaService(self.filial, date(2026, 8, 27)).gerar(
            incluir_previstos=True,
            previsao_inicio=date(2026, 8, 31),
            previsao_fim=date(2026, 8, 31),
        )
        previsao = next(
            item for item in posicao["previsoes"]
            if item["registro_id"] == conta_restante.pk
        )

        self.assertEqual(conta_recebida.valor_taxa_recebimento, Decimal("4.50"))
        self.assertEqual(conta_recebida.valor_liquido_recebido, Decimal("95.50"))
        self.assertEqual(conta_restante.valor_saldo, Decimal("100.00"))
        self.assertEqual(previsao["valor_bruto"], Decimal("100.00"))
        self.assertEqual(previsao["valor_taxa"], Decimal("0.00"))
        self.assertEqual(previsao["valor_liquido"], Decimal("100.00"))
        self.assertEqual(posicao["total_previsto"], Decimal("100.00"))

    def test_baixa_parcial_receber_cria_status_historico_e_conta_padrao(self):
        cliente = Cliente.objects.create(
            filial=self.filial, razao_social="Cliente parcial", tipo_pessoa="F", cpf_cnpj="12345678902",
        )
        conta = ContaReceber.objects.create(
            filial=self.filial, cliente=cliente, valor_original=Decimal("500.00"),
            valor_final=Decimal("500.00"), valor_saldo=Decimal("500.00"),
            data_emissao=date(2026, 8, 20), data_vencimento=date(2026, 8, 26),
        )

        ContaReceberService.registrar_baixa(
            conta, date(2026, 8, 25), Decimal("250.00"), self.forma, self.usuario,
        )
        conta.refresh_from_db()

        self.assertEqual(conta.status, StatusContaReceber.PAGO_PARCIAL)
        self.assertEqual(conta.valor_saldo, Decimal("250.00"))
        self.assertEqual(conta.conta_bancaria, self.banco)
        historico = PagamentoContaReceber.objects.get(conta_receber=conta)
        self.assertEqual(historico.valor_pago, Decimal("250.00"))
        self.assertEqual(historico.valor_liquido, Decimal("250.00"))
        self.assertEqual(historico.conta_bancaria, self.banco)

    def test_recebimentos_parciais_entram_na_posicao_por_baixa(self):
        cliente = Cliente.objects.create(
            filial=self.filial, razao_social="Cliente duas baixas", tipo_pessoa="F", cpf_cnpj="12345678912",
        )
        conta = ContaReceber.objects.create(
            filial=self.filial, cliente=cliente, valor_original=Decimal("500.00"),
            valor_final=Decimal("500.00"), valor_saldo=Decimal("500.00"),
            data_emissao=date(2026, 8, 20), data_vencimento=date(2026, 8, 25),
        )

        ContaReceberService.registrar_baixa(
            conta, date(2026, 8, 25), Decimal("250.00"), self.forma, self.usuario,
        )
        conta.refresh_from_db()
        ContaReceberService.registrar_baixa(
            conta, date(2026, 8, 26), Decimal("250.00"), self.forma, self.usuario,
        )

        self.assertEqual(
            PosicaoDiariaCaixaService(self.filial, date(2026, 8, 25)).gerar()["total_entradas"],
            Decimal("250.00"),
        )
        self.assertEqual(
            PosicaoDiariaCaixaService(self.filial, date(2026, 8, 26)).gerar()["total_entradas"],
            Decimal("250.00"),
        )

    def test_editar_e_excluir_baixa_recalcula_resumo_e_taxas(self):
        self.forma.taxa_fixa = Decimal("2.00")
        self.forma.save(update_fields=["taxa_fixa"])
        cliente = Cliente.objects.create(
            filial=self.filial, razao_social="Cliente recalculo", tipo_pessoa="F", cpf_cnpj="12345678913",
        )
        conta = ContaReceber.objects.create(
            filial=self.filial, cliente=cliente, valor_original=Decimal("500.00"),
            valor_final=Decimal("500.00"), valor_saldo=Decimal("500.00"),
            data_emissao=date(2026, 8, 20), data_vencimento=date(2026, 8, 25),
        )

        ContaReceberService.registrar_baixa(
            conta, date(2026, 8, 25), Decimal("250.00"), self.forma, self.usuario,
        )
        ContaReceberService.registrar_baixa(
            conta, date(2026, 8, 26), Decimal("250.00"), self.forma, self.usuario,
        )
        primeira, segunda = list(PagamentoContaReceber.objects.filter(conta_receber=conta).order_by("data_pagamento"))

        ContaReceberService.editar_baixa(
            primeira, date(2026, 8, 25), Decimal("200.00"), self.forma, self.usuario,
        )
        conta.refresh_from_db()
        self.assertEqual(conta.status, StatusContaReceber.PAGO_PARCIAL)
        self.assertEqual(conta.valor_pago, Decimal("450.00"))
        self.assertEqual(conta.valor_saldo, Decimal("50.00"))
        self.assertEqual(conta.valor_taxa_recebimento, Decimal("4.00"))
        self.assertEqual(conta.valor_liquido_recebido, Decimal("446.00"))

        segunda.refresh_from_db()
        ContaReceberService.excluir_baixa(segunda, "teste", self.usuario)
        conta.refresh_from_db()
        self.assertEqual(conta.status, StatusContaReceber.PAGO_PARCIAL)
        self.assertEqual(conta.valor_pago, Decimal("200.00"))
        self.assertEqual(conta.valor_saldo, Decimal("300.00"))
        self.assertEqual(conta.valor_taxa_recebimento, Decimal("2.00"))

    def test_baixa_de_cartao_usa_bandeira_e_parcelas_da_operacao(self):
        self.forma.tipo = TipoFormaPagamento.CARTAO_CREDITO
        self.forma.prazo_compensacao_dias_uteis = 1
        self.forma.save(update_fields=["tipo", "prazo_compensacao_dias_uteis"])
        TaxaParcelamento.objects.create(
            forma_pagamento=self.forma, parcelas=3, bandeira="mastercard", taxa=Decimal("3.22"),
        )
        cliente = Cliente.objects.create(
            filial=self.filial, razao_social="Cliente cartao", tipo_pessoa="F", cpf_cnpj="12345678909",
        )
        conta = ContaReceber.objects.create(
            filial=self.filial, cliente=cliente, valor_original=Decimal("100.00"),
            valor_final=Decimal("100.00"), valor_saldo=Decimal("100.00"),
            data_emissao=date(2026, 8, 20), data_vencimento=date(2026, 8, 21),
        )

        ContaReceberService.registrar_baixa(
            conta, date(2026, 8, 21), Decimal("100.00"), self.forma, self.usuario,
            conta_bancaria=self.banco, bandeira="Mastercard", numero_parcelas=3,
        )
        conta.refresh_from_db()

        self.assertEqual(conta.bandeira_recebimento, "mastercard")
        self.assertEqual(conta.parcelas_recebimento, 3)
        self.assertEqual(conta.taxa_percentual_aplicada, Decimal("3.2200"))
        self.assertEqual(conta.valor_taxa_recebimento, Decimal("3.22"))
        self.assertEqual(conta.valor_liquido_recebido, Decimal("96.78"))
        self.assertEqual(conta.data_liquidacao_prevista, date(2026, 8, 24))

    def test_entrada_manual_debito_calcula_taxa_da_bandeira_e_compensacao(self):
        self.forma.tipo = TipoFormaPagamento.CARTAO_DEBITO
        self.forma.prazo_compensacao_dias_uteis = 1
        self.forma.save(update_fields=["tipo", "prazo_compensacao_dias_uteis"])
        TaxaParcelamento.objects.create(
            forma_pagamento=self.forma, parcelas=1, bandeira="visa", taxa=Decimal("1.11"),
        )
        movimento = ExtratoBancario(
            filial=self.filial, conta_bancaria=self.banco, forma_pagamento=self.forma,
            data_lancamento=date(2026, 8, 21), historico="Debito Visa",
            valor=Decimal("100.00"), origem="manual", bandeira="visa", numero_parcelas=1,
        )
        movimento.recalcular_recebimento()
        movimento.save()

        self.assertEqual(movimento.taxa_percentual_aplicada, Decimal("1.11"))
        self.assertEqual(movimento.valor_taxa, Decimal("1.11"))
        self.assertEqual(movimento.valor_liquido, Decimal("98.89"))
        self.assertEqual(movimento.data_credito, date(2026, 8, 24))
        taxa_paga = ContaPagar.objects.get(
            documento_tipo="taxa_extrato", documento_id=movimento.pk,
        )
        self.assertEqual(taxa_paga.valor_pago, Decimal("1.11"))
        self.assertEqual(taxa_paga.data_pagamento, date(2026, 8, 24))
        self.assertEqual(taxa_paga.plano_contas.descricao, "Taxas por transacao")
        self.assertEqual(taxa_paga.conta_bancaria_id, self.banco.pk)
        pagamento_taxa = taxa_paga.pagamentos.get()
        self.assertEqual(pagamento_taxa.conta_bancaria_id, self.banco.pk)

        posicao = PosicaoDiariaCaixaService(self.filial, date(2026, 8, 24)).gerar()
        self.assertNotIn(taxa_paga.descricao_despesa, [item.descricao for item in posicao["saidas"]])
        self.assertFalse(posicao["sem_conta"])
        sexta = PosicaoDiariaCaixaService(self.filial, date(2026, 8, 21)).gerar()
        self.assertEqual(sexta["total_entradas"], Decimal("0"))
        self.assertFalse(any(m.registro_id == movimento.pk for m in sexta["entradas"]))
        self.assertEqual(sexta["total_fechamento"], Decimal("150.00"))
        segunda = PosicaoDiariaCaixaService(self.filial, date(2026, 8, 24)).gerar()
        self.assertEqual(segunda["total_entradas"], Decimal("98.89"))
        entrada = next(m for m in segunda["entradas"] if m.registro_id == movimento.pk)
        self.assertEqual(entrada.data, date(2026, 8, 24))
        self.assertEqual(segunda["total_fechamento"], Decimal("248.89"))

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
            "conta_destino": self.banco.pk, "data_lancamento": "2026-08-21",
            "data_referencia": "2026-08-21", "valor": "35.50", "historico": "Credito manual",
        })
        debito = self.client.post(url, {
            "acao": "lancar_movimento", "tipo": "debito",
            "conta_origem": self.banco.pk, "data_lancamento": "2026-08-21",
            "data_referencia": "2026-08-21", "valor": "10.25", "historico": "Debito manual",
        })

        self.assertEqual(credito.status_code, 302)
        self.assertEqual(debito.status_code, 302)
        self.assertEqual(credito.url, url + "?data=2026-08-21")
        self.assertEqual(debito.url, url + "?data=2026-08-21")
        valores = list(ExtratoBancario.objects.filter(
            filial=self.filial, historico__in=("Credito manual", "Debito manual"),
        ).order_by("historico").values_list("valor", flat=True))
        self.assertEqual(valores, [Decimal("35.50"), Decimal("-10.25")])
        self.assertFalse(ExtratoBancario.objects.filter(
            filial=self.filial, historico__in=("Credito manual", "Debito manual"),
        ).exclude(data_lancamento=date(2026, 8, 21)).exists())

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
        self.assertEqual(movimento.data_lancamento, date(2026, 8, 21))
        posicao = PosicaoDiariaCaixaService(self.filial, date(2026, 8, 21)).gerar()
        entrada = next(mov for mov in posicao["entradas"] if mov.registro_id == movimento.pk)
        self.assertEqual(entrada.classificacao, "Emprestimo recebido")

    def test_cards_exibem_apenas_ultima_categoria_financeira(self):
        grupo = PlanoContas.objects.create(
            empresa=self.empresa, codigo="310", descricao="Receitas financeiras",
            tipo="R", nivel=1, aceita_lancamento=False,
        )
        tipo = PlanoContas.objects.create(
            empresa=self.empresa, conta_pai=grupo, codigo="31001",
            descricao="Capital e emprestimos", tipo="R", nivel=2,
            aceita_lancamento=False,
        )
        categoria = self._categoria_receita("Emprestimos recebidos")
        categoria.conta_pai = tipo
        categoria.save(update_fields=["conta_pai"])
        movimento = ExtratoBancario.objects.create(
            filial=self.filial, conta_bancaria=self.banco, forma_pagamento=self.forma,
            plano_contas=categoria, data_lancamento=date(2026, 8, 21),
            historico="Emprestimo", valor=Decimal("100.00"), origem="manual",
        )

        posicao = PosicaoDiariaCaixaService(self.filial, date(2026, 8, 21)).gerar()
        entrada = next(item for item in posicao["entradas"] if item.registro_id == movimento.pk)

        self.assertEqual(entrada.classificacao, "Emprestimos recebidos")
        self.assertNotIn("Receitas financeiras", entrada.classificacao)

    def test_cartao_debito_ignora_parcelas_e_credito_respeita_maximo_cadastrado(self):
        from apps.financeiro.forms.cadastros import MovimentoContaBancariaForm

        debito = FormaPagamento.objects.create(
            empresa=self.empresa, filial=self.filial, descricao="Debito",
            tipo=TipoFormaPagamento.CARTAO_DEBITO,
        )
        credito = FormaPagamento.objects.create(
            empresa=self.empresa, filial=self.filial, descricao="Credito",
            tipo=TipoFormaPagamento.CARTAO_CREDITO, gera_parcelas=True,
        )
        TaxaParcelamento.objects.create(
            forma_pagamento=credito, parcelas=6, bandeira="visa", taxa=Decimal("4.00"),
        )
        categoria = self._categoria_receita("Venda manual")
        base = {
            "tipo": "credito", "conta_destino": self.banco.pk,
            "data_lancamento": "2026-08-21", "valor": "100.00",
            "historico": "Cartao", "plano_contas": categoria.pk, "bandeira": "visa",
        }

        form_debito = MovimentoContaBancariaForm(
            {**base, "forma_pagamento": debito.pk, "numero_parcelas": "12"},
            filial=self.filial,
        )
        self.assertTrue(form_debito.is_valid(), form_debito.errors)
        self.assertEqual(form_debito.cleaned_data["numero_parcelas"], 1)

        form_credito = MovimentoContaBancariaForm(
            {**base, "forma_pagamento": credito.pk, "numero_parcelas": "7"},
            filial=self.filial,
        )
        self.assertFalse(form_credito.is_valid())
        self.assertIn("numero_parcelas", form_credito.errors)

    def test_forma_pagamento_manual_renderiza_opcoes_e_metadados_de_cartao(self):
        from apps.financeiro.forms.cadastros import MovimentoContaBancariaForm

        debito = FormaPagamento.objects.create(
            empresa=self.empresa, filial=self.filial, descricao="Cartao debito",
            tipo=TipoFormaPagamento.CARTAO_DEBITO,
        )
        credito = FormaPagamento.objects.create(
            empresa=self.empresa, filial=self.filial, descricao="Cartao credito",
            tipo=TipoFormaPagamento.CARTAO_CREDITO, gera_parcelas=True,
        )
        TaxaParcelamento.objects.create(
            forma_pagamento=credito, parcelas=6, bandeira="visa", taxa=Decimal("4.00"),
        )

        html = str(MovimentoContaBancariaForm(filial=self.filial)["forma_pagamento"])

        self.assertIn(">PIX - Banco principal<", html)
        self.assertIn(">Cartao debito<", html)
        self.assertIn('data-tipo="cartao_debito"', html)
        self.assertIn(">Cartao credito<", html)
        self.assertIn('data-max-parcelas="6"', html)

    def test_edicao_de_entrada_manual_persiste_bandeira_do_cartao(self):
        categoria = self._categoria_receita("Venda no cartao")
        debito = FormaPagamento.objects.create(
            empresa=self.empresa, filial=self.filial, descricao="Debito Visa",
            tipo=TipoFormaPagamento.CARTAO_DEBITO,
        )
        movimento = ExtratoBancario.objects.create(
            filial=self.filial, conta_bancaria=self.banco, forma_pagamento=self.forma,
            plano_contas=categoria, data_lancamento=date(2026, 8, 22),
            historico="Entrada de ontem", valor=Decimal("100.00"), origem="manual",
        )

        response = self.client.post(reverse("financeiro:posicao_diaria"), {
            "acao": "editar_entrada", "origem": "manual", "movimento_id": movimento.pk,
            "data_referencia": "2026-08-22", "valor": "100.00",
            "forma_pagamento": debito.pk, "conta_bancaria": self.banco.pk,
            "data_entrada": "2026-08-22", "descricao": "Entrada de ontem",
            "plano_contas": categoria.pk, "bandeira": "visa", "numero_parcelas": "1",
            "justificativa": "Informar bandeira do cartao",
        })

        self.assertEqual(response.status_code, 302)
        movimento.refresh_from_db()
        self.assertEqual(movimento.forma_pagamento, debito)
        self.assertEqual(movimento.bandeira, "visa")
        self.assertEqual(movimento.numero_parcelas, 1)

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
