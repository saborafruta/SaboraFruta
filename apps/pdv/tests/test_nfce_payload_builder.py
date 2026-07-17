from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.cadastros.models import Cliente
from apps.core.constants.choices import TipoPessoa
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import DadosInvalidosError
from apps.financeiro.constants.enums import TipoFormaPagamento
from apps.financeiro.models import FormaPagamento
from apps.pdv.models import ItemVendaPDV, PagamentoVendaPDV, VendaPDV
from apps.pdv.services.nfce_payload_builder import NfePayloadBuilder
from apps.produtos.models import Produto, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial


class NfePayloadBuilderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social="Empresa NFe LTDA",
            nome_fantasia="Empresa NFe",
            cnpj="72345678000191",
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa,
            razao_social="Filial NFe",
            nome_fantasia="Matriz",
            cnpj="72345678000192",
            uf="RN",
            codigo_regime_tributario=1,
        )
        cls.perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa,
            nome="Operador NFe",
            is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email="nfe-pdv@inoovated.com",
            nome="Usuario NFe",
            password="teste1234",
            empresa=cls.empresa,
            filial=cls.filial,
            perfil=cls.perfil,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa,
            sigla="UN",
            descricao="Unidade",
            tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        cls.forma = FormaPagamento.objects.create(
            empresa=cls.empresa,
            filial=cls.filial,
            descricao="Dinheiro",
            tipo=TipoFormaPagamento.DINHEIRO,
        )

    def criar_produto(self):
        produto = Produto.objects.create(
            filial=self.filial,
            unidade_medida=self.unidade,
            descricao="Coco Ralado 250g",
            codigo="51",
            ncm="07141000",
            cfop_venda_interna="5102",
            cst_csosn="102",
            cst_pis="07",
            cst_cofins="07",
            controla_lote=False,
            preco_venda=Decimal("4.00"),
            preco_custo=Decimal("2.00"),
        )
        ProdutoFilial.objects.create(produto=produto, filial=self.filial)
        return produto

    def criar_venda(self, cliente):
        produto = self.criar_produto()
        venda = VendaPDV.objects.create(
            filial=self.filial,
            numero_venda=1,
            cliente=cliente,
            status="finalizada",
            valor_subtotal=Decimal("4.00"),
            valor_total=Decimal("4.00"),
            valor_pago=Decimal("4.00"),
            usuario=self.usuario,
            data_venda=timezone.now(),
        )
        ItemVendaPDV.objects.create(
            venda_pdv=venda,
            produto=produto,
            numero_item=1,
            quantidade=Decimal("1.000"),
            unidade_medida="UN",
            valor_unitario=Decimal("4.0000"),
            valor_total=Decimal("4.00"),
        )
        PagamentoVendaPDV.objects.create(
            venda_pdv=venda,
            forma_pagamento=self.forma,
            valor=Decimal("4.00"),
        )
        return venda

    def criar_cliente(self, **kwargs):
        dados = {
            "filial": self.filial,
            "tipo_pessoa": TipoPessoa.FISICA,
            "razao_social": "THIAGO BATISTA",
            "cpf_cnpj": "04651988482",
        }
        dados.update(kwargs)
        return Cliente.objects.create(**dados)

    def test_nfe_exige_endereco_completo_do_destinatario(self):
        venda = self.criar_venda(self.criar_cliente())

        with self.assertRaisesMessage(DadosInvalidosError, "endereco completo"):
            NfePayloadBuilder.build(venda, numero_nfe=6, serie_nfe=1)

    def test_nfe_payload_leva_endereco_destinatario_e_icms_focus(self):
        cliente = self.criar_cliente(
            endereco="Rua Sao Januario",
            numero="99",
            bairro="Crespo",
            cidade="Manaus",
            uf="AM",
            cep="69073178",
            codigo_municipio_ibge="1302603",
            celular="84999998888",
            email_nfe="cliente@example.com",
        )
        venda = self.criar_venda(cliente)

        payload = NfePayloadBuilder.build(venda, numero_nfe=6, serie_nfe=1)
        item = payload["items"][0]

        self.assertEqual(payload["cpf_destinatario"], "04651988482")
        self.assertEqual(payload["logradouro_destinatario"], "Rua Sao Januario")
        self.assertEqual(payload["numero_destinatario"], "99")
        self.assertEqual(payload["bairro_destinatario"], "Crespo")
        self.assertEqual(payload["municipio_destinatario"], "Manaus")
        self.assertEqual(payload["uf_destinatario"], "AM")
        self.assertEqual(payload["cep_destinatario"], "69073178")
        self.assertEqual(payload["codigo_municipio_destinatario"], "1302603")
        self.assertEqual(payload["indicador_inscricao_estadual_destinatario"], "9")
        self.assertEqual(item["icms_situacao_tributaria"], "102")
        self.assertNotIn("icms_csosn", item)
