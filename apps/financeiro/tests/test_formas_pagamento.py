from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.financeiro.constants.enums import TipoFormaPagamento
from apps.financeiro.models import ContaBancaria, FormaPagamento, TaxaParcelamento
from apps.pdv.models import PagamentoVendaPDV, VendaPDV


class FormasPagamentoFinanceiroTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social="Empresa Financeiro LTDA",
            nome_fantasia="Empresa Financeiro",
            cnpj="72345678000191",
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa,
            razao_social="Filial Financeiro",
            nome_fantasia="Matriz",
            cnpj="72345678000192",
            uf="RN",
        )
        cls.filial_destino = Filial.objects.create(
            empresa=cls.empresa,
            razao_social="Filial Destino",
            nome_fantasia="Destino",
            cnpj="72345678000193",
            uf="RN",
        )
        cls.perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa,
            nome="Admin Financeiro",
            is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email="financeiro-formas@inoovated.com",
            nome="Usuario Financeiro",
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

    def test_salva_forma_de_pagamento_na_filial_ativa(self):
        response = self.client.post(reverse("financeiro:formas_pagamento"), {
            "acao": "salvar",
            "descricao": "PIX",
            "tipo": TipoFormaPagamento.PIX,
            "codigo_sefaz": "17",
            "prazo_liquidacao_dias": "0",
            "taxa_administrativa": "0.00",
            "taxa_fixa": "0.35",
            "ativo": "on",
        })

        self.assertEqual(response.status_code, 302)
        forma = FormaPagamento.objects.get(descricao="PIX")
        self.assertEqual(forma.filial, self.filial)
        self.assertEqual(forma.empresa, self.empresa)
        self.assertEqual(forma.taxa_fixa, Decimal("0.35"))

    def test_vincula_forma_a_conta_pelo_nome_sem_ambiguidade(self):
        orenda = ContaBancaria.objects.create(
            filial=self.filial, descricao="ORENDA", banco_nome="ORENDA",
            banco_codigo="001",
        )
        nubank = ContaBancaria.objects.create(
            filial=self.filial, descricao="NUBANK (KARLA)", banco_nome="NUBANK",
            banco_codigo="260",
        )

        pix_orenda = FormaPagamento.objects.create(
            empresa=self.empresa, filial=self.filial,
            descricao="PIX MAQUININHA (ORENDA)", tipo=TipoFormaPagamento.PIX,
        )
        pix_karla = FormaPagamento.objects.create(
            empresa=self.empresa, filial=self.filial,
            descricao="PIX (KARLA)", tipo=TipoFormaPagamento.PIX,
        )

        pix_orenda.refresh_from_db()
        pix_karla.refresh_from_db()
        self.assertEqual(pix_orenda.conta_bancaria_padrao_id, orenda.pk)
        self.assertEqual(pix_karla.conta_bancaria_padrao_id, nubank.pk)

    def test_nao_vincula_forma_generica_sem_evidencia(self):
        ContaBancaria.objects.create(
            filial=self.filial, descricao="ORENDA", banco_nome="ORENDA",
            banco_codigo="001",
        )
        forma = FormaPagamento.objects.create(
            empresa=self.empresa, filial=self.filial,
            descricao="CARTAO CREDITO", tipo=TipoFormaPagamento.CARTAO_CREDITO,
        )
        forma.refresh_from_db()
        self.assertIsNone(forma.conta_bancaria_padrao_id)

    def test_replicar_forma_de_pagamento_para_outra_filial(self):
        forma = FormaPagamento.objects.create(
            empresa=self.empresa,
            filial=self.filial,
            descricao="Cartão de Crédito",
            tipo=TipoFormaPagamento.CARTAO_CREDITO,
            codigo_sefaz="03",
            requer_tef=True,
            taxa_administrativa=Decimal("2.50"),
            taxa_fixa=Decimal("0.40"),
        )

        response = self.client.post(reverse("financeiro:formas_pagamento"), {
            "acao": "replicar",
            "id": forma.pk,
            "filiais_destino": [str(self.filial_destino.pk)],
        })

        self.assertEqual(response.status_code, 302)
        replica = FormaPagamento.objects.get(
            filial=self.filial_destino,
            descricao="Cartão de Crédito",
        )
        self.assertEqual(replica.tipo, TipoFormaPagamento.CARTAO_CREDITO)
        self.assertTrue(replica.requer_tef)
        self.assertEqual(replica.taxa_administrativa, Decimal("2.50"))
        self.assertEqual(replica.taxa_fixa, Decimal("0.40"))

    def test_pagamento_congela_taxa_e_valor_liquido(self):
        forma = FormaPagamento.objects.create(
            empresa=self.empresa,
            filial=self.filial,
            descricao="Cartao 3x",
            tipo=TipoFormaPagamento.CARTAO_CREDITO,
            taxa_administrativa=Decimal("2.00"),
            taxa_fixa=Decimal("0.50"),
        )
        TaxaParcelamento.objects.create(
            forma_pagamento=forma,
            parcelas=3,
            taxa=Decimal("3.00"),
        )
        venda = VendaPDV.objects.create(
            filial=self.filial,
            numero_venda=901,
            status="finalizada",
            valor_total=Decimal("100.00"),
            valor_pago=Decimal("100.00"),
            usuario=self.usuario,
            data_venda=timezone.now(),
        )

        pagamento = PagamentoVendaPDV.objects.create(
            venda_pdv=venda,
            forma_pagamento=forma,
            valor=Decimal("100.00"),
            numero_parcelas=3,
        )

        self.assertEqual(pagamento.taxa_percentual_aplicada, Decimal("3.00"))
        self.assertEqual(pagamento.taxa_fixa_aplicada, Decimal("0.50"))
        self.assertEqual(pagamento.valor_taxa, Decimal("3.50"))
        self.assertEqual(pagamento.valor_liquido, Decimal("96.50"))
        forma.taxa_administrativa = Decimal("9.00")
        forma.save(update_fields=["taxa_administrativa"])
        pagamento.refresh_from_db()
        self.assertEqual(pagamento.valor_liquido, Decimal("96.50"))
