import ast
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial, PerfilAcesso, TenantPublicLink, Usuario
from apps.core.services.exceptions import DomainError
from apps.financeiro.constants.enums import TipoFormaPagamento
from apps.financeiro.models import (
    Boleto, ContaBancaria, ContaReceber, FormaPagamento, RetornoBancarioItem,
)
from apps.financeiro.services.boleto_bradesco import (
    digito_nosso_numero, fator_vencimento,
)
from apps.financeiro.services.boleto_pdf import gerar_pdf_boleto, validar_pdf_boleto
from apps.financeiro.services.cnab_cobranca import (
    RemessaCNAB240Service, RemessaCNAB400BradescoService, RetornoCNABService,
)


class CnabTenantTransactionTests(SimpleTestCase):
    """Impede que remessa/retorno abram transação no Banco Gerencial."""

    def test_fluxos_cnab_usam_transacao_do_tenant(self):
        caminho = Path(__file__).resolve().parents[1] / "services" / "cnab_cobranca.py"
        arvore = ast.parse(caminho.read_text(encoding="utf-8"))

        atomicos_padrao = [
            no
            for no in ast.walk(arvore)
            if isinstance(no, ast.Attribute)
            and isinstance(no.value, ast.Name)
            and no.value.id == "transaction"
            and no.attr == "atomic"
        ]
        atomicos_tenant = [
            no
            for no in ast.walk(arvore)
            if isinstance(no, ast.Name) and no.id == "tenant_atomic"
        ]
        importa_tenant_atomic = any(
            isinstance(no, ast.ImportFrom)
            and no.module == "apps.core.tenant_context"
            and any(nome.name == "tenant_atomic" for nome in no.names)
            for no in ast.walk(arvore)
        )

        self.assertFalse(atomicos_padrao)
        self.assertTrue(importa_tenant_atomic)
        self.assertEqual(len(atomicos_tenant), 3)  # duas remessas e um importador


def linha_cnab(campos):
    linha = list(" " * 240)
    for inicio, fim, valor in campos:
        texto = str(valor)
        linha[inicio - 1:fim] = texto.ljust(fim - inicio + 1)[:fim - inicio + 1]
    return "".join(linha)


def retorno_pago(nosso_numero, valor="000000000012345", data="20102026"):
    return "\r\n".join([
        linha_cnab([(1, 3, "001"), (8, 8, "0")]),
        linha_cnab([
            (1, 3, "001"), (8, 8, "3"), (14, 14, "T"), (16, 17, "06"),
            (38, 57, str(nosso_numero).rjust(20, "0")), (82, 96, valor),
        ]),
        linha_cnab([
            (1, 3, "001"), (8, 8, "3"), (14, 14, "U"),
            (18, 32, "000000000000000"), (78, 92, valor), (138, 145, data),
        ]),
        linha_cnab([(1, 3, "001"), (4, 7, "9999"), (8, 8, "9")]),
    ]) + "\r\n"


def linha_cnab400(campos):
    linha = list(" " * 400)
    for inicio, fim, valor in campos:
        texto = str(valor)
        linha[inicio - 1:fim] = texto.ljust(fim - inicio + 1)[:fim - inicio + 1]
    return "".join(linha)


def retorno_bradesco_pago(nosso_numero, valor="0000000012345", data="211026"):
    return "\r\n".join([
        linha_cnab400([(1, 1, "0"), (2, 2, "2"), (77, 79, "237")]),
        linha_cnab400([
            (1, 1, "1"), (71, 82, str(nosso_numero).rjust(12, "0")),
            (109, 110, "06"), (111, 116, data), (153, 165, valor),
            (254, 266, valor), (296, 301, data),
        ]),
        linha_cnab400([(1, 1, "9")]),
    ]) + "\r\n\x1a"


class CnabCobrancaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social="Empresa CNAB", cnpj="11222333000181",
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social="Empresa CNAB", nome_fantasia="Matriz",
            cnpj="11222333000181", uf="RN", cidade="Natal", cep="59000000",
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, tipo_pessoa="F", razao_social="Cliente Teste",
            cpf_cnpj="12345678901", endereco="Rua Um", numero="10", bairro="Centro",
            cidade="Natal", uf="RN", cep="59000001",
        )
        perfil = PerfilAcesso.objects.create(empresa=cls.empresa, nome="Admin", is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email="cnab@example.test", nome="Operador", password="teste",
            empresa=cls.empresa, filial=cls.filial, perfil=perfil,
        )
        cls.conta_bancaria = ContaBancaria.objects.create(
            filial=cls.filial, descricao="Cobrança", banco_codigo="001",
            banco_nome="Banco do Brasil", agencia="1234", agencia_digito="5",
            conta="98765", conta_digito="4", convenio="1234567",
            codigo_transmissao="1234567", carteira_cobranca="17",
        )
        cls.forma = FormaPagamento.objects.create(
            empresa=cls.empresa, filial=cls.filial, descricao="Boleto",
            tipo=TipoFormaPagamento.BOLETO, conta_bancaria_padrao=cls.conta_bancaria,
        )

    def criar_titulo(self, valor=Decimal("123.45")):
        return ContaReceber.objects.create(
            filial=self.filial, cliente=self.cliente, documento_numero="VENDA-10",
            valor_original=valor, valor_final=valor, valor_saldo=valor,
            data_emissao=date(2026, 10, 20), data_vencimento=date(2026, 11, 20),
            forma_pagamento=self.forma, conta_bancaria=self.conta_bancaria,
        )

    def conta_bradesco(self):
        return ContaBancaria.objects.create(
            filial=self.filial, descricao="Cobrança Bradesco", banco_codigo="237",
            banco_nome="Bradesco", agencia="1234", agencia_digito="0",
            conta="1234567", conta_digito="8", convenio="12345678901234567890",
            codigo_transmissao="12345678901234567890", carteira_cobranca="19",
            layout_cobranca=ContaBancaria.LayoutCobranca.CNAB400,
        )

    def conta_banco_nordeste(self):
        return ContaBancaria.objects.create(
            filial=self.filial, descricao="Cobrança BNB", banco_codigo="004",
            banco_nome="Banco do Nordeste", agencia="0123", agencia_digito="0",
            conta="0012345", conta_digito="6", convenio="1234567",
            codigo_transmissao="1234567", carteira_cobranca="21",
            layout_cobranca=ContaBancaria.LayoutCobranca.CNAB240,
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        session = self.client.session
        session["filial_ativa_id"] = self.filial.pk
        session.save()

    def test_tela_exibe_fluxo_de_remessa_e_retorno(self):
        self.criar_titulo()
        resposta = self.client.get(reverse("financeiro:boleto_comunicacao"))
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Remessa e retorno de boletos")
        self.assertContains(resposta, "Cliente Teste")

    def test_tela_filtra_titulos_por_vencimento_e_oferece_atalhos(self):
        vencido = self.criar_titulo()
        vencido.documento_numero = "VENCIDO"
        vencido.data_vencimento = date(2026, 10, 20)
        vencido.save(update_fields=["documento_numero", "data_vencimento"])
        futuro = self.criar_titulo()
        futuro.documento_numero = "FUTURO"
        futuro.data_vencimento = date(2026, 11, 20)
        futuro.save(update_fields=["documento_numero", "data_vencimento"])

        with patch("apps.financeiro.views.boletos.timezone.localdate", return_value=date(2026, 10, 21)):
            resposta = self.client.get(reverse("financeiro:boleto_comunicacao"), {
                "periodo": "vencidos",
            })
        self.assertContains(resposta, "VENCIDO")
        self.assertNotContains(resposta, "FUTURO")
        self.assertContains(resposta, "Vencem hoje")
        self.assertContains(resposta, "Próximos 7 dias")

        resposta = self.client.get(reverse("financeiro:boleto_comunicacao"), {
            "data_ini": "2026-11-20", "data_fim": "2026-11-20",
        })
        self.assertContains(resposta, "FUTURO")
        self.assertNotContains(resposta, "VENCIDO")

    def test_gera_remessa_cnab240_com_linhas_validas_e_vincula_boleto(self):
        titulo = self.criar_titulo()

        remessa = RemessaCNAB240Service.gerar(
            conta_bancaria=self.conta_bancaria, titulos=[titulo.pk], usuario=self.usuario,
        )

        linhas = remessa.arquivo_cnab.splitlines()
        self.assertEqual(len(linhas), 6)
        self.assertTrue(all(len(linha) == 240 for linha in linhas))
        self.assertEqual(linhas[0][7], "0")
        self.assertEqual(linhas[-1][7], "9")
        boleto = Boleto.objects.get(conta_receber=titulo)
        self.assertEqual(boleto.remessa, remessa)
        self.assertEqual(boleto.status, "remetido")

    def test_importa_retorno_liquida_titulo_e_impede_duplicidade(self):
        titulo = self.criar_titulo()
        remessa = RemessaCNAB240Service.gerar(
            conta_bancaria=self.conta_bancaria, titulos=[titulo.pk], usuario=self.usuario,
        )
        boleto = Boleto.objects.get(remessa=remessa)
        dados = retorno_pago(boleto.nosso_numero).encode("ascii")

        retorno = RetornoCNABService.importar(
            conta_bancaria=self.conta_bancaria, nome="RET001.RET", dados=dados,
            usuario=self.usuario,
        )

        titulo.refresh_from_db(); boleto.refresh_from_db()
        self.assertEqual(titulo.status, "pago")
        self.assertEqual(titulo.valor_saldo, Decimal("0"))
        self.assertEqual(boleto.status, "pago")
        self.assertEqual(retorno.quantidade_pagos, 1)
        self.assertEqual(RetornoBancarioItem.objects.get(retorno=retorno).status, "pago")
        with self.assertRaisesMessage(DomainError, "já foi processado"):
            RetornoCNABService.importar(
                conta_bancaria=self.conta_bancaria, nome="COPIA.RET", dados=dados,
                usuario=self.usuario,
            )

    def test_rejeita_arquivo_fora_do_tamanho_cnab(self):
        with self.assertRaisesMessage(DomainError, "240 ou 400 posições"):
            RetornoCNABService.ler("arquivo invalido")

    def test_digitos_bradesco_conferem_com_exemplos_do_manual(self):
        self.assertEqual(digito_nosso_numero("19", "00000000002"), "8")
        self.assertEqual(digito_nosso_numero("19", "00000000001"), "P")
        self.assertEqual(digito_nosso_numero("19", "00000000006"), "0")
        self.assertEqual(fator_vencimento(date(2025, 2, 21)), "9999")
        self.assertEqual(fator_vencimento(date(2025, 2, 22)), "1000")

    def test_gera_cnab400_bradesco_boleto_pdf_e_link_publico(self):
        conta = self.conta_bradesco()
        titulo = self.criar_titulo()
        titulo.conta_bancaria = conta
        titulo.save(update_fields=["conta_bancaria"])

        remessa = RemessaCNAB400BradescoService.gerar(
            conta_bancaria=conta, titulos=[titulo.pk], usuario=self.usuario,
        )

        linhas = remessa.arquivo_cnab.replace("\x1a", "").splitlines()
        self.assertEqual(len(linhas), 3)
        self.assertTrue(all(len(linha) == 400 for linha in linhas))
        self.assertEqual(linhas[0][0:11], "01REMESSA01")
        self.assertEqual(linhas[0][76:79], "237")
        self.assertEqual(linhas[1][0], "1")
        self.assertEqual(linhas[1][92], "2")
        self.assertEqual(linhas[1][108:110], "01")
        self.assertEqual(linhas[-1][0], "9")
        self.assertEqual(remessa.layout, "cnab400")
        self.assertTrue(remessa.arquivo_nome.endswith(".TST"))
        boleto = Boleto.objects.get(remessa=remessa)
        self.assertEqual(len(boleto.codigo_barras), 44)
        self.assertEqual(len("".join(c for c in boleto.linha_digitavel if c.isdigit())), 47)
        self.assertIn("/boleto/", boleto.url_boleto)
        self.assertTrue(TenantPublicLink.objects.filter(tipo="boleto").exists())

        pdf = self.client.get(reverse("financeiro:boleto_pdf", args=[boleto.pk]))
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF-"))
        historico = self.client.get(reverse("financeiro:boleto_comunicacao"), {
            "aba": "historico", "conta_bancaria": conta.pk,
        })
        self.assertContains(historico, reverse("financeiro:boleto_pdf", args=[boleto.pk]))
        self.assertContains(historico, "Baixar CNAB")
        self.client.logout()
        publico = self.client.get(reverse("boleto_publico:visualizar", args=[boleto.public_token]))
        self.assertEqual(publico.status_code, 200)
        pdf_publico = self.client.get(reverse("boleto_publico:pdf", args=[boleto.public_token]))
        self.assertEqual(pdf_publico.status_code, 200)
        self.assertTrue(pdf_publico.content.startswith(b"%PDF-"))

    def test_boleto_bradesco_antigo_sem_codigo_salvo_oferece_pdf_interno(self):
        conta = self.conta_bradesco()
        conta.layout_cobranca = ContaBancaria.LayoutCobranca.CNAB240
        conta.save(update_fields=["layout_cobranca"])
        titulo = self.criar_titulo()
        titulo.conta_bancaria = conta
        titulo.save(update_fields=["conta_bancaria"])

        remessa = RemessaCNAB240Service.gerar(
            conta_bancaria=conta, titulos=[titulo.pk], usuario=self.usuario,
        )
        boleto = Boleto.objects.get(remessa=remessa)
        self.assertEqual(boleto.codigo_barras, "")
        self.assertEqual(boleto.url_boleto, "")

        tela = self.client.get(reverse("financeiro:boleto_comunicacao"), {
            "conta_bancaria": conta.pk,
        })
        self.assertContains(tela, reverse("financeiro:boleto_pdf", args=[boleto.pk]))
        self.assertContains(tela, "Baixar PDF")
        self.assertNotContains(tela, "Copiar link")

        pdf = self.client.get(reverse("financeiro:boleto_pdf", args=[boleto.pk]))
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF-"))

    def test_boleto_banco_nordeste_oferece_pdf_multibanco(self):
        conta = self.conta_banco_nordeste()
        self.cliente.cpf_cnpj = "52998224725"
        self.cliente.save(update_fields=["cpf_cnpj"])
        titulo = self.criar_titulo(Decimal("127.50"))
        titulo.conta_bancaria = conta
        titulo.save(update_fields=["conta_bancaria"])

        remessa = RemessaCNAB240Service.gerar(
            conta_bancaria=conta, titulos=[titulo.pk], usuario=self.usuario,
        )
        boleto = Boleto.objects.get(remessa=remessa)

        tela = self.client.get(reverse("financeiro:boleto_comunicacao"), {
            "conta_bancaria": conta.pk,
        })
        self.assertContains(tela, reverse("financeiro:boleto_pdf", args=[boleto.pk]))
        self.assertContains(tela, "Baixar PDF")

        pdf = self.client.get(reverse("financeiro:boleto_pdf", args=[boleto.pk]))
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF-"))

    def test_gera_pdf_para_todos_os_bancos_suportados(self):
        self.cliente.cpf_cnpj = "52998224725"
        self.cliente.save(update_fields=["cpf_cnpj"])
        configuracoes = [
            ("001", "Banco do Brasil", {"agencia": "1234", "conta": "56789", "convenio": "1234567", "carteira_cobranca": "18"}, "123"),
            ("004", "Banco do Nordeste", {"agencia": "0016", "conta": "0001193", "conta_digito": "2", "carteira_cobranca": "21"}, "0000053"),
            ("021", "Banestes", {"agencia": "0274", "conta": "0009206981", "conta_digito": "9", "carteira_cobranca": "11", "variacao_carteira": "2"}, "90002720"),
            ("033", "Santander", {"convenio": "3300123", "carteira_cobranca": "101"}, "1234567"),
            ("041", "Banrisul", {"agencia": "1102", "conta": "12345678", "convenio": "9000150", "digito_convenio": "46", "carteira_cobranca": "2"}, "22832563"),
            ("070", "BRB", {"agencia": "082", "conta": "0000528", "incremento_cobranca": "100", "carteira_cobranca": "2"}, "000403"),
            ("077", "Inter", {"agencia": "0001", "conta": "123456", "convenio": "1234567", "carteira_cobranca": "110"}, "0004309540"),
            ("085", "Ailos", {"agencia": "0001", "conta": "1234567", "convenio": "123456", "carteira_cobranca": "01"}, "12345678"),
            ("097", "CrediSIS", {"agencia": "0001", "conta": "0000002", "convenio": "000527", "carteira_cobranca": "18"}, "000001"),
            ("104", "Caixa", {"agencia": "1234", "convenio": "123456", "carteira_cobranca": "14"}, "123"),
            ("136", "Unicred", {"agencia": "1234", "conta": "123456789", "conta_digito": "1", "carteira_cobranca": "21"}, "12345"),
            ("237", "Bradesco", {"agencia": "1234", "agencia_digito": "0", "conta": "1234567", "conta_digito": "8", "carteira_cobranca": "06"}, "00000000002"),
            ("336", "C6", {"agencia": "0001", "conta": "1234567", "convenio": "123456789012", "carteira_cobranca": "20"}, "1234567890"),
            ("341", "Itaú", {"agencia": "0057", "conta": "12345", "carteira_cobranca": "109"}, "12345678"),
            ("399", "HSBC", {"agencia": "4321", "conta": "1122334", "carteira_cobranca": "CNR"}, "12345678"),
            ("422", "Safra", {"agencia": "0811", "agencia_digito": "1", "conta": "00053678", "conta_digito": "8", "carteira_cobranca": "2"}, "12345678"),
            ("745", "Citibank", {"agencia": "0123", "conta": "1234567", "portfolio_cobranca": "172", "convenio": "0006247107", "carteira_cobranca": "3"}, "01099994022"),
            ("748", "Sicredi", {"agencia": "0165", "conta": "00623", "posto_cobranca": "05", "convenio": "00623", "byte_identificacao": "2", "carteira_cobranca": "3"}, "12345"),
            ("756", "Sicoob", {"agencia": "3007", "conta": "12345678", "convenio": "229385", "variacao_carteira": "01", "carteira_cobranca": "1"}, "246"),
        ]

        for codigo, nome, dados_conta, nosso_numero in configuracoes:
            with self.subTest(banco=codigo):
                conta = ContaBancaria.objects.create(
                    filial=self.filial,
                    descricao=f"Cobrança {nome}",
                    banco_codigo=codigo,
                    banco_nome=nome,
                    **dados_conta,
                )
                titulo = self.criar_titulo(Decimal("127.50"))
                titulo.conta_bancaria = conta
                titulo.documento_numero = f"DOC-{codigo}"
                titulo.data_emissao = date(2026, 7, 23)
                titulo.data_vencimento = date(2026, 8, 15)
                titulo.save(update_fields=[
                    "conta_bancaria", "documento_numero", "data_emissao", "data_vencimento",
                ])
                boleto = Boleto.objects.create(
                    conta_receber=titulo,
                    conta_bancaria=conta,
                    filial=self.filial,
                    nosso_numero=nosso_numero,
                    seu_numero=f"DOC-{codigo}",
                    carteira=conta.carteira_cobranca,
                    data_emissao=titulo.data_emissao,
                    data_vencimento=titulo.data_vencimento,
                    valor=titulo.valor_final,
                )

                disponivel, motivo = validar_pdf_boleto(boleto)
                self.assertTrue(disponivel, motivo)
                self.assertTrue(gerar_pdf_boleto(boleto).startswith(b"%PDF-"))

    def test_importa_retorno_cnab400_bradesco_e_liquida_titulo(self):
        conta = self.conta_bradesco()
        titulo = self.criar_titulo()
        titulo.conta_bancaria = conta
        titulo.save(update_fields=["conta_bancaria"])
        FormaPagamento.objects.filter(pk=self.forma.pk).update(conta_bancaria_padrao=conta)
        remessa = RemessaCNAB400BradescoService.gerar(
            conta_bancaria=conta, titulos=[titulo.pk], usuario=self.usuario,
        )
        boleto = Boleto.objects.get(remessa=remessa)

        retorno = RetornoCNABService.importar(
            conta_bancaria=conta, nome="CB211001.RET",
            dados=retorno_bradesco_pago(boleto.nosso_numero).encode("ascii"),
            usuario=self.usuario,
        )

        titulo.refresh_from_db(); boleto.refresh_from_db()
        self.assertEqual(retorno.layout, "cnab400")
        self.assertEqual(titulo.status, "pago")
        self.assertEqual(boleto.status, "pago")
        detalhe = self.client.get(reverse("financeiro:boleto_retorno_detalhe", args=[retorno.pk]))
        self.assertContains(detalhe, reverse("financeiro:boleto_pdf", args=[boleto.pk]))
        self.assertContains(detalhe, "Baixar PDF")
