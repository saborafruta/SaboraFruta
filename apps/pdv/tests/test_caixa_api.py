import json
import re
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.financeiro.constants.enums import TipoFormaPagamento
from apps.financeiro.models import FormaPagamento
from apps.pdv.models import Caixa, SessaoPDV


class CaixaPDVApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social="Empresa Caixa LTDA",
            nome_fantasia="Empresa Caixa",
            cnpj="62345678000191",
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa,
            razao_social="Filial Caixa",
            nome_fantasia="Matriz",
            cnpj="62345678000192",
            uf="RN",
        )
        cls.outra_filial = Filial.objects.create(
            empresa=cls.empresa,
            razao_social="Filial Caixa Dois",
            nome_fantasia="Filial Dois",
            cnpj="62345678000193",
            uf="RN",
        )
        cls.perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa,
            nome="Operador Caixa",
            is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email="caixa-api@inoovated.com",
            nome="Usuario Caixa",
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

    def post_json(self, name, payload):
        return self.client.post(
            reverse(name),
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_cria_primeiro_caixa_da_filial(self):
        response = self.post_json("pdv:api_caixa_criar", {})

        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertTrue(data["ok"])
        caixa = Caixa.objects.get(filial=self.filial)
        self.assertEqual(caixa.numero, 1)
        self.assertEqual(caixa.descricao, "Caixa 1")
        self.assertEqual(data["caixa"]["id"], caixa.pk)

    def test_cria_proximo_numero_de_caixa(self):
        Caixa.objects.create(filial=self.filial, numero=3, descricao="Caixa 3")

        response = self.post_json("pdv:api_caixa_criar", {})

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["caixa"]["numero"], 4)

    def test_abrir_caixa_exige_selecao(self):
        response = self.post_json("pdv:api_caixa_abrir", {"valor_abertura": "0"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["erro"], "Selecione um caixa.")
        self.assertFalse(SessaoPDV.objects.exists())

    def test_favoritos_do_pdv_incluem_submenu_do_menu_completo(self):
        caminho = reverse('estoque:ajuste-rapido')
        self.usuario.menu_favoritos = [caminho]
        self.usuario.save(update_fields=['menu_favoritos'])
        response = self.client.get(reverse('core:menu-favoritos'))
        self.assertEqual(response.status_code, 200)
        self.assertIn(caminho, [item['caminho'] for item in response.json()['itens']])

    def test_pdv_embute_os_nove_favoritos_com_nomes_e_ordem_do_dashboard(self):
        self.empresa.modulos_extras = ['moda']
        self.empresa.save(update_fields=['modulos_extras'])
        caminhos = [
            '/financeiro/posicao-diaria/', '/financeiro/pagar/',
            '/financeiro/receber/', '/financeiro/pagar/pagas/',
            '/financeiro/formas-pagamento/', '/moda/comercial/',
            '/produtos/', '/estoque/ajuste-rapido/', '/pdv/',
        ]
        self.usuario.menu_favoritos = caminhos
        self.usuario.save(update_fields=['menu_favoritos'])
        response = self.client.get(reverse('pdv:home'))
        self.assertEqual(response.status_code, 200)
        itens = json.loads(re.search(
            r'<script id="sidebar-favorites-records" type="application/json">(.*?)</script>',
            response.content.decode(), re.S,
        ).group(1))
        self.assertEqual([item['caminho'] for item in itens], caminhos)
        self.assertEqual([item['nome'] for item in itens], [
            'Posição Diária de Caixa', 'Contas a Pagar', 'Contas a Receber',
            'Contas Pagas', 'Formas de Pagamento', 'Comercial', 'Produtos',
            'Ajuste de Estoque', 'PDV — Ponto de Venda',
        ])
        api = self.client.get(reverse('core:menu-favoritos'))
        self.assertEqual(api.json()['itens'], itens)
        self.assertEqual(api.json()['favoritos'], caminhos)
        self.assertEqual(api['Cache-Control'], 'private, no-store')

    def test_favorito_de_modulo_desativado_nao_e_exposto_no_pdv(self):
        self.filial.modulos_desativados = ['moda']
        self.filial.save(update_fields=['modulos_desativados'])
        self.usuario.menu_favoritos = ['/moda/comercial/', '/produtos/']
        self.usuario.save(update_fields=['menu_favoritos'])
        response = self.client.get(reverse('pdv:home'))
        itens = json.loads(re.search(
            r'<script id="sidebar-favorites-records" type="application/json">(.*?)</script>',
            response.content.decode(), re.S,
        ).group(1))
        self.assertEqual([item['caminho'] for item in itens], ['/produtos/'])
        self.usuario.refresh_from_db()
        self.assertEqual(self.usuario.menu_favoritos, ['/moda/comercial/', '/produtos/'])

    def test_abrir_caixa_rejeita_valor_negativo(self):
        caixa = Caixa.objects.create(filial=self.filial, numero=1, descricao="Caixa 1")

        response = self.post_json(
            "pdv:api_caixa_abrir",
            {"caixa_id": caixa.pk, "valor_abertura": "-1"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["erro"], "Informe um valor de abertura válido.")
        self.assertFalse(SessaoPDV.objects.exists())

    def test_abre_caixa_recém_criado(self):
        criar = self.post_json("pdv:api_caixa_criar", {}).json()
        caixa_id = criar["caixa"]["id"]

        response = self.post_json(
            "pdv:api_caixa_abrir",
            {"caixa_id": caixa_id, "valor_abertura": "12.50"},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        sessao = SessaoPDV.objects.get(pk=data["sessao_id"])
        self.assertEqual(sessao.caixa_id, caixa_id)
        self.assertEqual(sessao.valor_abertura, Decimal("12.50"))

    def test_estado_lista_as_formas_padrao_da_filial(self):
        """
        Quem cria as formas e' a criacao da filial, nao esta consulta -- ver
        `financeiro/tests/test_formas_pagamento_padrao.py`. Aqui interessa que
        o caixa as enxergue.
        """
        response = self.client.get(reverse("pdv:api_estado"))

        self.assertEqual(response.status_code, 200)
        formas = response.json()["formas_pagamento"]
        descricoes = {forma["descricao"] for forma in formas}
        tipos = {forma["tipo"] for forma in formas}
        self.assertIn("Dinheiro", descricoes)
        self.assertIn("PIX", descricoes)
        self.assertIn(TipoFormaPagamento.CARTAO_CREDITO, tipos)
        self.assertGreaterEqual(
            FormaPagamento.objects.filter(filial=self.filial, ativo=True).count(),
            6,
        )

    def test_estado_ignora_formas_de_pagamento_de_outra_filial(self):
        FormaPagamento.objects.create(
            empresa=self.empresa,
            filial=self.outra_filial,
            descricao="Vale Loja Outra Filial",
            tipo=TipoFormaPagamento.VALE,
            codigo_sefaz="99",
        )

        response = self.client.get(reverse("pdv:api_estado"))

        self.assertEqual(response.status_code, 200)
        descricoes = {forma["descricao"] for forma in response.json()["formas_pagamento"]}
        self.assertNotIn("Vale Loja Outra Filial", descricoes)
        self.assertTrue(
            FormaPagamento.objects.filter(filial=self.filial, descricao="Dinheiro").exists()
        )
