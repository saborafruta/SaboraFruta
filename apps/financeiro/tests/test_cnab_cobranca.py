from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import DomainError
from apps.financeiro.constants.enums import TipoFormaPagamento
from apps.financeiro.models import (
    Boleto, ContaBancaria, ContaReceber, FormaPagamento, RetornoBancarioItem,
)
from apps.financeiro.services.cnab_cobranca import RemessaCNAB240Service, RetornoCNABService


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
        with self.assertRaisesMessage(DomainError, "240 posições"):
            RetornoCNABService.ler("arquivo invalido")
