from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from django.template.loader import get_template
from django.test import RequestFactory, SimpleTestCase

from apps.financeiro.views.pagar import (
    _contexto_despesas_pessoais,
    _limite_ranking,
    _resumo_categorias_pagas,
)


class ContaPagarTemplateRegressionTests(SimpleTestCase):
    def test_dias_semana_nao_aplica_widget_tweaks_em_boundwidget_tag(self):
        template = Path(
            "apps/financeiro/templates/financeiro/pagar/form.html"
        ).read_text(encoding="utf-8")

        self.assertNotIn("checkbox.tag|attr", template)
        self.assertIn("{{ checkbox.tag }}", template)
        self.assertIn("configurarRecorrenciaSemanalIntuitiva", template)

    def test_edicao_em_lote_usa_busca_de_categoria_no_modal(self):
        template = Path(
            "apps/financeiro/templates/financeiro/pagar/list.html"
        ).read_text(encoding="utf-8")

        self.assertIn('name="plano_contas" :value="loteCategoriaId"', template)
        self.assertIn("loteCategoriaQuery", template)
        self.assertNotIn("bulk_edit_form.plano_contas|add_class", template)

    def test_conferencia_mostra_dias_uteis_e_antecipacao(self):
        template = Path(
            "apps/financeiro/templates/financeiro/pagar/form.html"
        ).read_text(encoding="utf-8")

        self.assertIn("Considera dias úteis?", template)
        self.assertIn("conferenciaResumo.antecipacao", template)

    def test_lista_tem_filtro_proprio_de_beneficiario(self):
        template = Path(
            "apps/financeiro/templates/financeiro/pagar/list.html"
        ).read_text(encoding="utf-8")

        self.assertIn('name="beneficiario" value="{{ beneficiario_filtro }}"', template)

    def test_edicao_e_exclusao_oferecem_escopo_da_recorrencia(self):
        template = Path(
            "apps/financeiro/templates/financeiro/_detalhes_conta_modal.html"
        ).read_text(encoding="utf-8")

        self.assertIn('name="escopo_edicao" value="restantes"', template)
        self.assertIn('name="escopo_recorrencia" value="restantes"', template)
        self.assertIn("Este e todos os próximos", template)

    def test_exclusao_trata_resposta_html_sem_tentar_ler_como_json(self):
        template = Path(
            "apps/financeiro/templates/financeiro/pagar/_editar_valor_script.html"
        ).read_text(encoding="utf-8")

        self.assertIn("tipo.includes('application/json')", template)
        self.assertIn("Sua sessão expirou ou você não possui permissão", template)
        self.assertIn("Recarregue a página e tente novamente", template)

    def test_contas_pagas_compila_com_rankings_e_graficos(self):
        get_template("financeiro/pagar/pagas.html")
        template = Path(
            "apps/financeiro/templates/financeiro/pagar/pagas.html"
        ).read_text(encoding="utf-8")

        self.assertIn("Ver mais 10", template)
        self.assertIn("Ver tudo", template)
        self.assertIn("grafico_subgrupos", template)
        self.assertIn("grafico_categorias_finais", template)
        self.assertIn("grafico_fornecedores", template)
        self.assertIn("grafico_despesas_pessoais_beneficiarios", template)
        self.assertNotIn("grafico_despesas_pessoais_subgrupos", template)
        self.assertNotIn("grafico_despesas_pessoais_categorias_finais", template)
        self.assertNotIn("Últimos lançamentos pessoais", template)
        self.assertIn("clique para ver o último nível", template)

        grafico = Path(
            "apps/financeiro/templates/financeiro/pagar/_grafico_pizza_despesas.html"
        ).read_text(encoding="utf-8")
        self.assertIn("paid-donut-slice", grafico)
        self.assertIn("<title>{{ fatia.nome }}", grafico)
        self.assertIn("grafico.total|moeda", grafico)
        self.assertIn("fatia.valor|moeda", grafico)
        self.assertNotIn("truncate", grafico)

    def test_resumo_categorias_monta_os_tres_niveis_e_graficos(self):
        grupo = SimpleNamespace(pk=1, descricao="Operacional", conta_pai=None, conta_pai_id=None)
        subgrupo = SimpleNamespace(pk=2, descricao="Manutenção", conta_pai=grupo, conta_pai_id=1)
        categoria = SimpleNamespace(pk=3, descricao="Máquinas", conta_pai=subgrupo, conta_pai_id=2)
        contas = [
            SimpleNamespace(valor_pago=Decimal("150.00"), plano_contas=categoria),
            SimpleNamespace(valor_pago=Decimal("50.00"), plano_contas=categoria),
        ]

        resumo = _resumo_categorias_pagas(contas, Decimal("1000.00"))

        grupo_resumo = resumo["categorias_resumo"][0]
        self.assertEqual(grupo_resumo["nome"], "Operacional")
        self.assertEqual(grupo_resumo["subgrupos"][0]["nome"], "Manutenção")
        self.assertEqual(
            grupo_resumo["subgrupos"][0]["categorias"][0]["nome"], "Máquinas",
        )
        self.assertEqual(resumo["grafico_categorias"]["total"], Decimal("200.00"))
        self.assertEqual(resumo["grafico_subgrupos"]["total"], Decimal("200.00"))
        self.assertEqual(resumo["grafico_categorias_finais"]["total"], Decimal("200.00"))
        self.assertEqual(
            resumo["grafico_categorias"]["fatias"][0]["percentual_svg"], "100.0000",
        )

    def test_limite_ranking_comeca_em_dez_e_aceita_mais_ou_todos(self):
        factory = RequestFactory()

        self.assertEqual(_limite_ranking(factory.get("/"), "limite"), 10)
        self.assertEqual(_limite_ranking(factory.get("/?limite=20"), "limite"), 20)
        self.assertIsNone(_limite_ranking(factory.get("/?limite=todos"), "limite"))

    def test_contexto_despesas_pessoais_resume_por_beneficiario(self):
        grupo = SimpleNamespace(pk=1, descricao="Pessoais", conta_pai=None, conta_pai_id=None)
        subgrupo = SimpleNamespace(pk=2, descricao="Sócios", conta_pai=grupo, conta_pai_id=1)
        categoria = SimpleNamespace(
            pk=3, descricao="Retiradas", conta_pai=subgrupo, conta_pai_id=2,
            despesa_pessoal=True,
        )
        conta = SimpleNamespace(
            pk=9, valor_pago=Decimal("250.00"), plano_contas=categoria,
            beneficiario_nome="Sócio A", data_pagamento=None,
        )

        contexto = _contexto_despesas_pessoais([conta], Decimal("1000.00"))

        self.assertEqual(contexto["despesas_pessoais_total"], Decimal("250.00"))
        self.assertEqual(contexto["despesas_pessoais_percentual_total"], Decimal("25.00"))
        self.assertEqual(
            contexto["grafico_despesas_pessoais_beneficiarios"]["fatias"][0]["nome"],
            "Sócio A",
        )
