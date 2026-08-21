from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.financeiro.constants.enums import TipoFormaPagamento
from apps.financeiro.models import ContaBancaria, FormaPagamento
from apps.financeiro.models.extrato import ExtratoBancario
from apps.pdv.models import PagamentoVendaPDV, VendaPDV


class ContasBancariasViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social="Empresa Banco LTDA",
            nome_fantasia="Empresa Banco",
            cnpj="42345678000191",
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa,
            razao_social="Filial Banco",
            nome_fantasia="Matriz",
            cnpj="42345678000192",
            uf="RN",
        )
        cls.perfil = PerfilAcesso.objects.create(empresa=cls.empresa, nome="Admin", is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email="bancos@inoovated.com",
            nome="Usuario Bancos",
            password="teste1234",
            empresa=cls.empresa,
            filial=cls.filial,
            perfil=cls.perfil,
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        session = self.client.session
        session["filial_ativa_id"] = self.filial.pk
        session.save()

    def test_cria_conta_bancaria_na_filial_ativa(self):
        response = self.client.post(reverse("financeiro:contas_bancarias"), {
            "acao": "salvar_conta",
            "descricao": "Conta Principal",
            "banco_codigo": "001",
            "banco_nome": "Banco do Brasil",
            "agencia": "1234",
            "agencia_digito": "5",
            "conta": "98765",
            "conta_digito": "4",
            "tipo_conta": "corrente",
            "saldo_inicial": "100.00",
            "ativo": "on",
        })

        self.assertEqual(response.status_code, 302)
        conta = ContaBancaria.objects.get(descricao="Conta Principal")
        self.assertEqual(conta.filial, self.filial)
        self.assertEqual(conta.saldo_atual, Decimal("100.00"))

    def test_cria_conta_bancaria_apenas_com_apelido(self):
        response = self.client.post(reverse("financeiro:contas_bancarias"), {
            "acao": "salvar_conta",
            "descricao": "Orenda",
            "saldo_inicial": "0.00",
            "ativo": "on",
        })

        self.assertEqual(response.status_code, 302)
        conta = ContaBancaria.objects.get(descricao="Orenda")
        self.assertEqual(conta.filial, self.filial)
        self.assertEqual(conta.banco_codigo, "")
        self.assertEqual(conta.saldo_atual, Decimal("0.00"))

    def test_movimento_manual_altera_saldo_calculado(self):
        conta = ContaBancaria.objects.create(
            filial=self.filial,
            descricao="Carteira",
            banco_codigo="000",
            banco_nome="Manual",
            saldo_inicial=Decimal("50.00"),
            saldo_atual=Decimal("50.00"),
        )

        response = self.client.post(reverse("financeiro:contas_bancarias"), {
            "acao": "lancar_movimento",
            "tipo": "credito",
            "conta_destino": str(conta.pk),
            "data_lancamento": "2026-08-20",
            "valor": "25.00",
            "historico": "Aporte",
        })

        self.assertEqual(response.status_code, 302)
        conta.refresh_from_db()
        self.assertEqual(conta.saldo_atual, Decimal("75.00"))
        self.assertTrue(ExtratoBancario.objects.filter(conta_bancaria=conta, valor=Decimal("25.00")).exists())

    def test_lista_venda_pdv_quando_forma_tem_conta_padrao(self):
        conta = ContaBancaria.objects.create(
            filial=self.filial,
            descricao="Banco Pix",
            banco_codigo="341",
            banco_nome="Itau",
            saldo_inicial=Decimal("0.00"),
            saldo_atual=Decimal("0.00"),
        )
        forma = FormaPagamento.objects.create(
            empresa=self.empresa,
            filial=self.filial,
            descricao="PIX",
            tipo=TipoFormaPagamento.PIX,
            codigo_sefaz="17",
            conta_bancaria_padrao=conta,
        )
        venda = VendaPDV.objects.create(
            filial=self.filial,
            numero_venda=1,
            status="finalizada",
            valor_total=Decimal("80.00"),
            valor_pago=Decimal("80.00"),
            usuario=self.usuario,
            data_venda=timezone.datetime(2026, 8, 20, 12, 0, tzinfo=timezone.get_current_timezone()),
        )
        PagamentoVendaPDV.objects.create(venda_pdv=venda, forma_pagamento=forma, valor=Decimal("80.00"))

        response = self.client.get(reverse("financeiro:contas_bancarias"), {
            "data_ini": "2026-08-01",
            "data_fim": "2026-08-31",
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Venda PDV #1")
        self.assertContains(response, "R$ 80,00")
