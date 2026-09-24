from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from django.template.loader import get_template
from django.test import RequestFactory, SimpleTestCase

from apps.financeiro.views.pagar import _limite_ranking, _resumo_categorias_pagas


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
        self.assertIn("clique para ver o último nível", template)

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
        self.assertIn("conic-gradient", resumo["grafico_categorias"]["gradiente"])

    def test_limite_ranking_comeca_em_dez_e_aceita_mais_ou_todos(self):
        factory = RequestFactory()

        self.assertEqual(_limite_ranking(factory.get("/"), "limite"), 10)
        self.assertEqual(_limite_ranking(factory.get("/?limite=20"), "limite"), 20)
        self.assertIsNone(_limite_ranking(factory.get("/?limite=todos"), "limite"))
