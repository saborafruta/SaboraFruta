from datetime import date
from decimal import Decimal
from importlib import import_module
import json
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory

from django.apps import apps as django_apps
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from django.http import Http404
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.forms import FornecedorRapidoForm, FuncionarioForm
from apps.cadastros.models import Fornecedor, FornecedorFilial, Funcionario
from apps.cadastros.views.fornecedor import FornecedorAjaxCreateView
from apps.compras.models import EntradaNF
from apps.core.models import Empresa, Filial, PerfilAcesso, RegistroAuditoria, Usuario
from apps.financeiro.forms.pagar import (
    ContaPagarForm,
    DespesaPagaForm,
    PagamentoContaPagarForm,
    validar_comprovante,
)
from apps.financeiro.models import (
    ContaPagar,
    ContaBancaria,
    FormaPagamento,
    PagamentoContaPagar,
    PlanoContabil,
    PlanoContas,
)
from apps.financeiro.services.pagar_service import ContaPagarService
from apps.financeiro.views.pagar import (
    ComprovantePagamentoView,
    ContaPagaListView,
    ContaPagaRelatorioView,
    ContaPagarCreateView,
    ContaPagarRelatorioView,
    DespesaPagaCreateView,
    ContaPagarEditarValorView,
    ContaPagarExcluirView,
    ContaPagarListView,
    ContaPagarNotaFiscalLookupView,
    ContaPagarPagamentoView,
    _filtrar_contas_pagar_abertas,
)
from apps.financeiro.views.plano_contas import PlanoContasQuickCreateView


class FuncionarioContaPagarTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social="Eureka", nome_fantasia="Eureka", cnpj="50649395000126",
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social="Eureka", nome_fantasia="Eureka",
            cnpj="50649395000127", uf="RN",
        )
        cls.funcionario = Funcionario.objects.create(
            filial=cls.filial, nome="Maria Silva", cpf="12345678901",
            cargo="Costureira", salario_base=Decimal("1800.00"), chave_pix="12345678901",
        )
        cls.conta_contabil = PlanoContabil.objects.create(
            empresa=cls.empresa, codigo_referencia=8001, classificacao="3320100001",
            tipo_conta="A", descricao="SALARIOS E ORDENADOS", data_inicio=date(2015, 1, 1),
            nivel=5, ordem=1,
        )
        cls.categoria = PlanoContas.objects.create(
            empresa=cls.empresa, conta_contabil=cls.conta_contabil,
            codigo="3320100001", descricao="Salarios e Ordenados", tipo="D",
            nivel=3, aceita_lancamento=True,
        )
        cls.forma_prevista = FormaPagamento.objects.create(
            empresa=cls.empresa,
            descricao="Boleto",
            tipo="boleto",
        )
        cls.forma_pix = FormaPagamento.objects.create(
            empresa=cls.empresa,
            descricao="Pix",
            tipo="pix",
        )

    def dados_formulario(self, **extras):
        dados = {
            "descricao_despesa": "Folha de pagamento",
            "tipo_lancamento": "funcionario",
            "funcionario": self.funcionario.pk,
            "documento_numero": "FOLHA-08/2026",
            "parcela": 1,
            "total_parcelas": 1,
            "valor_original": "1800.00",
            "data_vencimento": "2026-08-30",
            "plano_contas": self.categoria.pk,
        }
        dados.update(extras)
        return dados

    def test_funcionario_exige_pessoa_se_for_pagamento_direto(self):
        form = ContaPagarForm(self.dados_formulario(funcionario=""), filial=self.filial)
        self.assertFalse(form.is_valid())
        self.assertIn("funcionario", form.errors)

    def test_encargo_geral_nao_exige_funcionario(self):
        form = ContaPagarForm(
            self.dados_formulario(tipo_lancamento="encargo", funcionario=""),
            filial=self.filial,
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_data_emissao_nao_e_exibida_nem_obrigatoria(self):
        form = ContaPagarForm(
            self.dados_formulario(data_vencimento="2020-01-10"),
            filial=self.filial,
        )

        self.assertNotIn("data_emissao", form.fields)
        self.assertTrue(form.is_valid(), form.errors)

    def test_cadastro_rapido_cria_fornecedor_vinculado_a_filial(self):
        request = RequestFactory().post(
            "/cadastros/fornecedores/ajax-create/",
            {
                "tipo_pessoa": "J",
                "razao_social": "Fornecedor Modal Ltda",
                "nome_fantasia": "Fornecedor Modal",
                "cpf_cnpj": "11.222.333/0001-81",
                "cep": "59022540",
                "cidade": "Natal",
                "uf": "RN",
                "codigo_municipio_ibge": "2408102",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        request.user = SimpleNamespace(
            is_authenticated=True,
            tem_permissao=lambda modulo, acao: True,
        )
        request.filial_ativa = self.filial

        response = FornecedorAjaxCreateView.as_view()(request)
        payload = json.loads(response.content)
        fornecedor = Fornecedor.objects.get(pk=payload["id"])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["label"], "Fornecedor Modal")
        self.assertEqual(fornecedor.cpf_cnpj, "11222333000181")
        self.assertEqual(fornecedor.codigo_municipio_ibge, "2408102")
        self.assertTrue(
            Fornecedor.objects.for_filial(self.filial).filter(pk=fornecedor.pk).exists()
        )

    def test_cadastro_rapido_rejeita_cnpj_duplicado_na_filial(self):
        existente = Fornecedor.objects.create(
            filial=self.filial,
            tipo_pessoa="J",
            razao_social="Fornecedor Existente",
            cpf_cnpj="11222333000181",
        )
        from apps.cadastros.services.replicacao_service import ReplicacaoCadastrosService
        ReplicacaoCadastrosService.sincronizar_fornecedor(existente)
        request = RequestFactory().post(
            "/cadastros/fornecedores/ajax-create/",
            {
                "tipo_pessoa": "J",
                "razao_social": "Fornecedor Repetido",
                "cpf_cnpj": "11.222.333/0001-81",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        request.user = SimpleNamespace(
            is_authenticated=True,
            tem_permissao=lambda modulo, acao: True,
        )
        request.filial_ativa = self.filial

        response = FornecedorAjaxCreateView.as_view()(request)
        payload = json.loads(response.content)

        self.assertEqual(response.status_code, 400)
        self.assertIn("cpf_cnpj", payload["errors"])

    def test_cadastro_rapido_aceita_cpf_com_mascara_e_valida_digitos(self):
        valido = FornecedorRapidoForm(
            {
                "tipo_pessoa": "F",
                "razao_social": "Pessoa Física",
                "cpf_cnpj": "529.982.247-25",
            },
            filial=self.filial,
        )
        invalido = FornecedorRapidoForm(
            {
                "tipo_pessoa": "F",
                "razao_social": "Pessoa Inválida",
                "cpf_cnpj": "529.982.247-24",
            },
            filial=self.filial,
        )

        self.assertTrue(valido.is_valid(), valido.errors)
        self.assertEqual(valido.cleaned_data["cpf_cnpj"], "52998224725")
        self.assertFalse(invalido.is_valid())
        self.assertIn("CPF inválido", invalido.errors["cpf_cnpj"][0])

    def test_chave_nfe_preenche_numero_e_fica_guardada_no_formulario(self):
        chave = "24260811222333000181550010000001231000001234"
        form = ContaPagarForm(
            self.dados_formulario(
                nota_fiscal_fornecedor=chave,
                chave_acesso_nfe="",
            ),
            filial=self.filial,
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["chave_acesso_nfe"], chave)
        self.assertEqual(form.cleaned_data["nota_fiscal_fornecedor"], "123")

    def test_consulta_chave_nao_cadastrada_retorna_numero_da_nota(self):
        chave = "24260811222333000181550010000001231000001234"
        request = RequestFactory().get(
            "/financeiro/pagar/nfe/consultar/",
            {"chave": chave},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        request.user = SimpleNamespace(
            is_authenticated=True,
            tem_permissao=lambda modulo, acao: True,
        )
        request.filial_ativa = self.filial

        response = ContaPagarNotaFiscalLookupView.as_view()(request)
        payload = json.loads(response.content)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(payload["encontrada"])
        self.assertEqual(payload["numero_nf"], "123")

    def test_consulta_chave_cadastrada_retorna_fornecedor_e_valor(self):
        chave = "24260811222333000181550010000001231000001234"
        fornecedor = Fornecedor.objects.create(
            filial=self.filial,
            tipo_pessoa="J",
            razao_social="Fornecedor da Nota Ltda",
            nome_fantasia="Fornecedor da Nota",
            cpf_cnpj="11222333000181",
        )
        perfil = PerfilAcesso.objects.create(
            empresa=self.empresa,
            nome="Operador de Compras",
        )
        usuario = Usuario.objects.create_user(
            email="nota@example.com",
            nome="Operador da Nota",
            password="teste",
            empresa=self.empresa,
            perfil=perfil,
        )
        EntradaNF.objects.create(
            filial=self.filial,
            fornecedor=fornecedor,
            numero_nf="123",
            serie_nf="1",
            chave_acesso_nf=chave,
            data_emissao_nf=date(2026, 8, 20),
            data_entrada=timezone.now(),
            valor_total=Decimal("987.65"),
            usuario=usuario,
        )
        request = RequestFactory().get(
            "/financeiro/pagar/nfe/consultar/",
            {"chave": chave},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        request.user = SimpleNamespace(
            is_authenticated=True,
            tem_permissao=lambda modulo, acao: True,
        )
        request.filial_ativa = self.filial

        response = ContaPagarNotaFiscalLookupView.as_view()(request)
        payload = json.loads(response.content)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["encontrada"])
        self.assertEqual(payload["fornecedor"]["id"], fornecedor.pk)
        self.assertEqual(payload["valor_total"], "987.65")

    def test_servico_grava_funcionario_categoria_e_conta_contabil(self):
        conta = ContaPagarService.criar(
            filial=self.filial, funcionario=self.funcionario, tipo_lancamento="funcionario",
            valor_original=Decimal("1800.00"), data_emissao=date(2026, 8, 20),
            data_vencimento=date(2026, 8, 30), plano_contas=self.categoria,
        )
        self.assertEqual(conta.funcionario, self.funcionario)
        self.assertIsNone(conta.fornecedor)
        self.assertEqual(conta.conta_contabil, self.conta_contabil)
        self.assertEqual(conta.beneficiario_nome, "Maria Silva")

    def test_cpf_duplicado_na_filial_e_bloqueado(self):
        form = FuncionarioForm({"nome": "Outra Maria", "cpf": "123.456.789-01", "salario_base": "0"}, filial=self.filial)
        self.assertFalse(form.is_valid())
        self.assertIn("cpf", form.errors)

    def test_migration_cria_tres_niveis_da_categoria_de_pessoal(self):
        migration = import_module("apps.financeiro.migrations.0018_funcionario_conta_pagar_categorias_pessoal")
        migration.criar_categorias_pessoal(django_apps, None)
        categoria = PlanoContas.objects.get(empresa=self.empresa, codigo="3320100001")
        self.assertEqual(categoria.conta_pai.codigo, "33201")
        self.assertEqual(categoria.conta_pai.conta_pai.codigo, "332")
        self.assertEqual(categoria.conta_contabil, self.conta_contabil)

    def test_migration_separa_despesas_com_pessoal_de_despesas_pessoais(self):
        grupo_funcionarios = PlanoContas.objects.create(
            empresa=self.empresa,
            codigo="33201",
            descricao="Despesas com Pessoal",
            tipo="D",
            nivel=2,
            aceita_lancamento=False,
            despesa_pessoal=True,
        )
        diaria = PlanoContas.objects.create(
            empresa=self.empresa,
            conta_pai=grupo_funcionarios,
            codigo="3320100017",
            descricao="Diária",
            tipo="D",
            nivel=3,
            despesa_pessoal=True,
        )
        hora_extra = PlanoContas.objects.create(
            empresa=self.empresa,
            conta_pai=grupo_funcionarios,
            codigo="3320100018",
            descricao="Hora extra",
            tipo="D",
            nivel=3,
            despesa_pessoal=True,
        )
        gasto_pessoal = PlanoContas.objects.create(
            empresa=self.empresa,
            codigo="3900100001",
            descricao="Compras pessoais",
            tipo="D",
            nivel=3,
            despesa_pessoal=True,
        )

        migration = import_module(
            "apps.financeiro.migrations.0061_corrigir_classificacao_despesas_com_pessoal"
        )
        migration.corrigir_classificacao(django_apps, None)

        grupo_funcionarios.refresh_from_db()
        diaria.refresh_from_db()
        hora_extra.refresh_from_db()
        gasto_pessoal.refresh_from_db()
        self.assertFalse(grupo_funcionarios.despesa_pessoal)
        self.assertFalse(diaria.despesa_pessoal)
        self.assertFalse(hora_extra.despesa_pessoal)
        self.assertTrue(gasto_pessoal.despesa_pessoal)

    def test_recorrencia_mensal_cria_titulos_com_datas_validas(self):
        contas = ContaPagarService.criar_recorrencia(
            filial=self.filial,
            funcionario=self.funcionario,
            tipo_lancamento="funcionario",
            valor_original=Decimal("1800.00"),
            data_emissao=date(2026, 8, 20),
            data_vencimento=date(2026, 8, 31),
            data_competencia=date(2026, 8, 1),
            plano_contas=self.categoria,
            frequencia="mensal",
            quantidade=3,
        )

        self.assertEqual([conta.data_vencimento for conta in contas], [
            date(2026, 8, 31), date(2026, 9, 30), date(2026, 10, 31),
        ])
        self.assertEqual([conta.data_competencia for conta in contas], [
            date(2026, 8, 1), date(2026, 9, 1), date(2026, 10, 1),
        ])
        self.assertEqual([conta.parcela for conta in contas], [1, 2, 3])
        self.assertTrue(all(conta.total_parcelas == 3 for conta in contas))
        self.assertEqual(len({conta.grupo_recorrencia for conta in contas}), 1)
        self.assertIsNotNone(contas[0].grupo_recorrencia)

    def test_recorrencia_mensal_aplica_primeiro_e_ultimo_dia(self):
        dados = {
            "filial": self.filial,
            "funcionario": self.funcionario,
            "tipo_lancamento": "funcionario",
            "valor_original": Decimal("100.00"),
            "data_emissao": date(2026, 8, 1),
            "data_vencimento": date(2026, 8, 15),
            "plano_contas": self.categoria,
            "frequencia": "mensal",
            "quantidade": 2,
        }

        primeiros = ContaPagarService.criar_recorrencia(
            **dados,
            regra_vencimento_mensal=ContaPagar.RegraVencimentoMensal.PRIMEIRO_DIA,
        )
        ultimos = ContaPagarService.criar_recorrencia(
            **dados,
            regra_vencimento_mensal=ContaPagar.RegraVencimentoMensal.ULTIMO_DIA,
        )

        self.assertEqual(
            [conta.data_vencimento for conta in primeiros],
            [date(2026, 8, 1), date(2026, 9, 1)],
        )
        self.assertEqual(
            [conta.data_vencimento for conta in ultimos],
            [date(2026, 8, 31), date(2026, 9, 30)],
        )

    def test_recorrencia_mensal_dia_fixo_respeita_mes_curto(self):
        contas = ContaPagarService.criar_recorrencia(
            filial=self.filial,
            funcionario=self.funcionario,
            tipo_lancamento="funcionario",
            valor_original=Decimal("100.00"),
            data_emissao=date(2026, 8, 1),
            data_vencimento=date(2026, 8, 31),
            plano_contas=self.categoria,
            frequencia="mensal",
            quantidade=2,
            regra_vencimento_mensal=ContaPagar.RegraVencimentoMensal.DIA_FIXO,
            dia_vencimento_mensal=31,
        )

        self.assertEqual(
            [conta.data_vencimento for conta in contas],
            [date(2026, 8, 31), date(2026, 9, 30)],
        )
        self.assertTrue(all(conta.dia_vencimento_mensal == 31 for conta in contas))

    def test_recorrencia_mensal_quinto_dia_util(self):
        contas = ContaPagarService.criar_recorrencia(
            filial=self.filial,
            funcionario=self.funcionario,
            tipo_lancamento="funcionario",
            valor_original=Decimal("100.00"),
            data_emissao=date(2026, 8, 1),
            data_vencimento=date(2026, 8, 15),
            plano_contas=self.categoria,
            frequencia="mensal",
            quantidade=2,
            regra_vencimento_mensal=ContaPagar.RegraVencimentoMensal.QUINTO_DIA_UTIL,
        )

        self.assertEqual(
            [conta.data_vencimento for conta in contas],
            [date(2026, 8, 7), date(2026, 9, 8)],
        )

    def test_migration_cria_insumos_como_categoria_final(self):
        migration = import_module("apps.financeiro.migrations.0048_criar_categoria_insumos")
        migration.criar_insumos(django_apps, None)

        categoria = PlanoContas.objects.get(empresa=self.empresa, descricao="Insumos", nivel=3)
        self.assertTrue(categoria.aceita_lancamento)
        self.assertEqual(categoria.conta_pai.descricao, "Mercadorias e Insumos")
        self.assertEqual(categoria.conta_contabil.descricao, "Insumos")

    def test_recorrencia_diaria_cria_um_titulo_por_dia(self):
        contas = ContaPagarService.criar_recorrencia(
            filial=self.filial,
            funcionario=self.funcionario,
            tipo_lancamento="funcionario",
            valor_original=Decimal("100.00"),
            data_emissao=date(2026, 8, 20),
            data_vencimento=date(2026, 8, 24),
            plano_contas=self.categoria,
            frequencia="diaria",
            quantidade=3,
        )

        self.assertEqual(
            [conta.data_vencimento for conta in contas],
            [date(2026, 8, 24), date(2026, 8, 25), date(2026, 8, 26)],
        )

    def test_recorrencia_diaria_em_dias_uteis_nao_repete_vencimentos(self):
        contas = ContaPagarService.criar_recorrencia(
            filial=self.filial,
            funcionario=self.funcionario,
            tipo_lancamento="funcionario",
            valor_original=Decimal("100.00"),
            data_emissao=date(2026, 9, 1),
            data_vencimento=date(2026, 9, 4),
            plano_contas=self.categoria,
            frequencia="diaria",
            quantidade=5,
            ajustar_vencimento_dia_util=True,
        )

        vencimentos = [conta.data_vencimento for conta in contas]
        self.assertEqual(len(vencimentos), len(set(vencimentos)))
        self.assertEqual(vencimentos, [
            date(2026, 9, 4), date(2026, 9, 5), date(2026, 9, 8),
            date(2026, 9, 9), date(2026, 9, 10),
        ])

    def test_recorrencia_aceita_limite_de_365_ocorrencias(self):
        contas = ContaPagarService.criar_recorrencia(
            filial=self.filial,
            funcionario=self.funcionario,
            tipo_lancamento="funcionario",
            valor_original=Decimal("10.00"),
            data_emissao=date(2026, 1, 1),
            data_vencimento=date(2026, 1, 1),
            plano_contas=self.categoria,
            frequencia="diaria",
            quantidade=365,
        )

        self.assertEqual(len(contas), 365)
        self.assertTrue(all(conta.total_parcelas == 365 for conta in contas))

    def test_titulo_unico_pode_ser_transformado_em_recorrencia(self):
        conta = ContaPagarService.criar(
            filial=self.filial,
            funcionario=self.funcionario,
            tipo_lancamento="funcionario",
            valor_original=Decimal("100.00"),
            data_emissao=date(2026, 8, 20),
            data_vencimento=date(2026, 8, 24),
            plano_contas=self.categoria,
        )

        contas = ContaPagarService.reprogramar_recorrencia(
            conta=conta,
            quantidade=3,
            frequencia="semanal",
            data_vencimento=date(2026, 8, 24),
            dias_semana=["0", "2", "4"],
        )

        self.assertEqual(
            [item.data_vencimento for item in contas],
            [date(2026, 8, 24), date(2026, 8, 26), date(2026, 8, 28)],
        )
        self.assertEqual(len({item.grupo_recorrencia for item in contas}), 1)
        self.assertTrue(all(item.total_parcelas == 3 for item in contas))

    def test_recorrencia_personalizada_respeita_intervalo_em_dias(self):
        contas = ContaPagarService.criar_recorrencia(
            filial=self.filial,
            funcionario=self.funcionario,
            tipo_lancamento="funcionario",
            valor_original=Decimal("100.00"),
            data_emissao=date(2026, 8, 20),
            data_vencimento=date(2026, 8, 24),
            plano_contas=self.categoria,
            frequencia="personalizada",
            intervalo_dias=3,
            quantidade=3,
        )

        self.assertEqual(
            [conta.data_vencimento for conta in contas],
            [date(2026, 8, 24), date(2026, 8, 27), date(2026, 8, 30)],
        )
        self.assertTrue(all(conta.intervalo_recorrencia_dias == 3 for conta in contas))

    def test_imposto_pode_vencer_no_dia_util_anterior(self):
        categoria_imposto = PlanoContas.objects.create(
            empresa=self.empresa,
            conta_contabil=self.conta_contabil,
            codigo="9990100001",
            descricao="Impostos municipais",
            tipo="D",
            nivel=3,
            aceita_lancamento=True,
        )

        conta = ContaPagarService.criar(
            filial=self.filial,
            funcionario=self.funcionario,
            tipo_lancamento="funcionario",
            valor_original=Decimal("100.00"),
            data_emissao=date(2026, 8, 20),
            data_vencimento=date(2026, 8, 30),
            plano_contas=categoria_imposto,
            ajustar_vencimento_dia_util=True,
            antecipar_vencimento_dia_util=True,
        )

        self.assertEqual(conta.data_vencimento, date(2026, 8, 28))

    def test_dia_nao_util_avanca_quando_nao_antecipa(self):
        conta = ContaPagarService.criar(
            filial=self.filial,
            funcionario=self.funcionario,
            tipo_lancamento="funcionario",
            valor_original=Decimal("100.00"),
            data_emissao=date(2026, 8, 20),
            data_vencimento=date(2026, 8, 30),
            plano_contas=self.categoria,
            ajustar_vencimento_dia_util=True,
            antecipar_vencimento_dia_util=False,
        )

        self.assertEqual(conta.data_vencimento, date(2026, 8, 31))

    def test_sem_calendario_de_dias_uteis_preserva_a_data(self):
        conta = ContaPagarService.criar(
            filial=self.filial,
            funcionario=self.funcionario,
            tipo_lancamento="funcionario",
            valor_original=Decimal("100.00"),
            data_emissao=date(2026, 8, 20),
            data_vencimento=date(2026, 8, 30),
            plano_contas=self.categoria,
            ajustar_vencimento_dia_util=False,
            antecipar_vencimento_dia_util=True,
        )

        self.assertEqual(conta.data_vencimento, date(2026, 8, 30))

    def test_forma_prevista_nao_quita_o_titulo(self):
        conta = ContaPagarService.criar(
            filial=self.filial,
            funcionario=self.funcionario,
            tipo_lancamento="funcionario",
            valor_original=Decimal("1800.00"),
            data_emissao=date(2026, 8, 20),
            data_vencimento=date(2026, 8, 30),
            plano_contas=self.categoria,
            forma_pagamento_prevista=self.forma_prevista,
        )

        self.assertEqual(conta.forma_pagamento_prevista, self.forma_prevista)
        self.assertIsNone(conta.forma_pagamento)
        self.assertEqual(conta.valor_pago, Decimal("0"))
        self.assertFalse(conta.pagamentos.exists())

    def test_criar_e_quitar_registra_pagamento_integral(self):
        conta = ContaPagarService.criar_e_quitar(
            filial=self.filial,
            funcionario=self.funcionario,
            tipo_lancamento="funcionario",
            valor_original=Decimal("1800.00"),
            data_emissao=date(2026, 8, 20),
            data_vencimento=date(2026, 8, 30),
            plano_contas=self.categoria,
            forma_pagamento_prevista=self.forma_prevista,
            data_pagamento=date(2026, 8, 20),
            forma_pagamento_utilizada=self.forma_pix,
        )

        self.assertEqual(conta.status, "pago")
        self.assertEqual(conta.valor_pago, Decimal("1800.00"))
        self.assertEqual(conta.valor_saldo, Decimal("0"))
        self.assertEqual(conta.forma_pagamento, self.forma_pix)
        pagamento = PagamentoContaPagar.objects.get(conta_pagar=conta)
        self.assertEqual(pagamento.valor_pago, Decimal("1800.00"))
        self.assertEqual(pagamento.forma_pagamento, self.forma_pix)

    def test_contas_pagas_lista_somente_quitadas_com_link_para_detalhes(self):
        paga = ContaPagarService.criar_e_quitar(
            filial=self.filial,
            funcionario=self.funcionario,
            tipo_lancamento="funcionario",
            documento_numero="PAGO-001",
            valor_original=Decimal("1800.00"),
            data_emissao=date(2026, 8, 20),
            data_vencimento=date(2026, 8, 30),
            plano_contas=self.categoria,
            data_pagamento=date(2026, 8, 20),
            forma_pagamento_utilizada=self.forma_pix,
        )
        ContaPagarService.criar(
            filial=self.filial,
            funcionario=self.funcionario,
            tipo_lancamento="funcionario",
            documento_numero="ABERTO-001",
            valor_original=Decimal("500.00"),
            data_emissao=date(2026, 8, 20),
            data_vencimento=date(2026, 8, 30),
            plano_contas=self.categoria,
        )
        request = RequestFactory().get("/financeiro/pagar/pagas/")
        request.user = SimpleNamespace(
            is_authenticated=True,
            tem_permissao=lambda modulo, acao: True,
        )
        request.filial_ativa = self.filial

        response = ContaPagaListView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "PAGO-001")
        self.assertNotContains(response, "ABERTO-001")
        self.assertContains(response, f'/financeiro/pagar/{paga.pk}/')
        self.assertContains(response, "Nova despesa paga")
        self.assertContains(response, "Análise por categoria financeira")
        self.assertContains(response, "Maior categoria")
        self.assertContains(response, "Gasto por fornecedor")
        self.assertContains(response, "R$ 1800,00")

    def test_despesa_paga_tem_formulario_curto_e_exige_beneficiario(self):
        form = DespesaPagaForm(filial=self.filial)

        self.assertNotIn('documento_numero', form.fields)
        self.assertNotIn('data_vencimento', form.fields)
        self.assertNotIn('recorrente', form.fields)
        self.assertIn('valor_original', form.fields)
        self.assertIn('forma_pagamento_utilizada', form.fields)

        invalido = DespesaPagaForm({
            'tipo_lancamento': 'funcionario',
            'valor_original': '50.00',
            'plano_contas': self.categoria.pk,
            'forma_pagamento_utilizada': self.forma_pix.pk,
        }, filial=self.filial)
        self.assertFalse(invalido.is_valid())
        self.assertIn('funcionario', invalido.errors)

    def test_despesa_paga_nasce_quitada_com_data_de_hoje(self):
        perfil = PerfilAcesso.objects.create(
            empresa=self.empresa, nome='Admin despesa paga', is_admin=True,
        )
        usuario = Usuario.objects.create_user(
            email='despesa-paga@teste.com', nome='Admin', password='teste',
            empresa=self.empresa, filial=self.filial, perfil=perfil,
        )
        get_request = RequestFactory().get('/financeiro/pagar/despesa-paga/nova/')
        get_request.user = usuario
        get_request.filial_ativa = self.filial
        get_response = DespesaPagaCreateView.as_view()(get_request)
        self.assertEqual(get_response.status_code, 200)
        self.assertContains(get_response, 'Registrar despesa paga')
        self.assertContains(get_response, 'Novo fornecedor')
        self.assertContains(get_response, reverse('cadastros:fornecedor-ajax-create'))
        self.assertContains(get_response, 'Hoje,')
        self.assertNotContains(get_response, 'Data de vencimento')
        self.assertNotContains(get_response, 'Título recorrente')

        request = RequestFactory().post('/financeiro/pagar/despesa-paga/nova/?modal=1', {
            'descricao_despesa': 'Adiantamento salarial',
            'tipo_lancamento': 'funcionario',
            'funcionario': self.funcionario.pk,
            'valor_original': '75.50',
            'plano_contas': self.categoria.pk,
            'forma_pagamento_utilizada': self.forma_pix.pk,
        })
        request.user = usuario
        request.filial_ativa = self.filial
        request.session = {}
        request._messages = FallbackStorage(request)

        response = DespesaPagaCreateView.as_view()(request)
        conta = ContaPagar.objects.order_by('-pk').first()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(conta.status, 'pago')
        self.assertEqual(conta.data_emissao, timezone.localdate())
        self.assertEqual(conta.data_vencimento, timezone.localdate())
        self.assertEqual(conta.data_pagamento, timezone.localdate())
        self.assertEqual(conta.total_parcelas, 1)
        self.assertEqual(conta.pagamentos.count(), 1)

    def test_criacao_rapida_mantem_os_tres_niveis_e_vinculo_contabil(self):
        perfil = PerfilAcesso.objects.create(
            empresa=self.empresa, nome='Admin categorias rapidas', is_admin=True,
        )
        usuario = Usuario.objects.create_user(
            email='categoria-rapida@teste.com', nome='Admin', password='teste',
            empresa=self.empresa, filial=self.filial, perfil=perfil,
        )

        def criar(nivel, descricao, **dados):
            request = RequestFactory().post('/financeiro/categorias-financeiras/criar-rapida/', {
                'nivel': nivel, 'descricao': descricao, **dados,
            })
            request.user = usuario
            request.filial_ativa = self.filial
            response = PlanoContasQuickCreateView.as_view()(request)
            self.assertEqual(response.status_code, 200, response.content)
            return json.loads(response.content)['categoria']

        grupo = criar(1, 'Despesas de teste')
        subgrupo = criar(2, 'Tipo de teste', conta_pai=grupo['id'])
        categoria = criar(
            3, 'Categoria de teste', conta_pai=subgrupo['id'],
            conta_contabil=self.conta_contabil.pk,
        )
        conta = PlanoContas.objects.get(pk=categoria['id'])

        self.assertEqual(conta.conta_pai_id, subgrupo['id'])
        self.assertEqual(conta.conta_contabil, self.conta_contabil)
        self.assertTrue(conta.aceita_lancamento)

    def test_relatorio_de_contas_pagas_filtra_pela_data_do_pagamento(self):
        ContaPagarService.criar_e_quitar(
            filial=self.filial,
            funcionario=self.funcionario,
            tipo_lancamento="funcionario",
            documento_numero="PAGO-AGOSTO",
            valor_original=Decimal("100.00"),
            data_emissao=date(2026, 8, 20),
            data_vencimento=date(2026, 9, 10),
            plano_contas=self.categoria,
            data_pagamento=date(2026, 8, 20),
            forma_pagamento_utilizada=self.forma_pix,
        )
        request = RequestFactory().get(
            "/financeiro/pagar/pagas/relatorio/",
            {"data_ini": "2026-08-01", "data_fim": "2026-08-31"},
        )
        request.user = SimpleNamespace(
            is_authenticated=True,
            tem_permissao=lambda modulo, acao: True,
        )
        request.filial_ativa = self.filial

        response = ContaPagaRelatorioView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "PAGO-AGOSTO")
        self.assertContains(response, "Relatório de Contas Pagas")

    def test_nova_conta_paga_abre_com_quitacao_preselecionada(self):
        request = RequestFactory().get("/financeiro/pagar/novo/?quitar=1", {"quitar": "1"})
        request.user = SimpleNamespace(
            is_authenticated=True,
            tem_permissao=lambda modulo, acao: True,
        )
        request.filial_ativa = self.filial

        response = ContaPagarCreateView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nova Conta Paga")
        self.assertContains(response, "quitarAgora:true")

    def test_contas_a_pagar_filtra_pelos_tres_niveis_da_categoria(self):
        grupo = PlanoContas.objects.create(
            empresa=self.empresa, codigo="300", descricao="Despesas Operacionais",
            tipo="D", nivel=1, aceita_lancamento=False,
        )
        subgrupo = PlanoContas.objects.create(
            empresa=self.empresa, conta_pai=grupo, codigo="30001",
            descricao="Despesas com Pessoal", tipo="D", nivel=2,
            aceita_lancamento=False,
        )
        categoria_folha = PlanoContas.objects.create(
            empresa=self.empresa, conta_pai=subgrupo, conta_contabil=self.conta_contabil,
            codigo="300010001", descricao="Folha de Pagamento", tipo="D", nivel=3,
            aceita_lancamento=True,
        )
        categoria_beneficio = PlanoContas.objects.create(
            empresa=self.empresa, conta_pai=subgrupo, conta_contabil=self.conta_contabil,
            codigo="300010002", descricao="Benefícios", tipo="D", nivel=3,
            aceita_lancamento=True,
        )
        ContaPagarService.criar(
            filial=self.filial, funcionario=self.funcionario, tipo_lancamento="funcionario",
            documento_numero="FOLHA-FILTRO", valor_original=Decimal("1800.00"),
            data_emissao=date(2026, 8, 20), data_vencimento=date(2026, 8, 30),
            plano_contas=categoria_folha,
        )
        ContaPagarService.criar(
            filial=self.filial, funcionario=self.funcionario, tipo_lancamento="funcionario",
            documento_numero="BENEFICIO-FILTRO", valor_original=Decimal("300.00"),
            data_emissao=date(2026, 8, 20), data_vencimento=date(2026, 8, 30),
            plano_contas=categoria_beneficio,
        )
        request = RequestFactory().get(
            "/financeiro/pagar/",
            {
                "categoria_grupo": grupo.pk,
                "categoria_subgrupo": subgrupo.pk,
                "categoria_financeira": categoria_folha.pk,
            },
        )
        request.user = SimpleNamespace(
            is_authenticated=True,
            tem_permissao=lambda modulo, acao: True,
        )
        request.filial_ativa = self.filial

        response = ContaPagarListView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "FOLHA-FILTRO")
        self.assertNotContains(response, "BENEFICIO-FILTRO")
        self.assertContains(response, "Grupo da despesa")
        self.assertContains(response, "Tipo de gasto")
        self.assertContains(response, "Categoria específica")

    def test_contas_pagas_filtra_por_grupo_financeiro(self):
        grupo = PlanoContas.objects.create(
            empresa=self.empresa, codigo="400", descricao="Custos de Produção",
            tipo="D", nivel=1, aceita_lancamento=False,
        )
        subgrupo = PlanoContas.objects.create(
            empresa=self.empresa, conta_pai=grupo, codigo="40001",
            descricao="Materiais", tipo="D", nivel=2, aceita_lancamento=False,
        )
        categoria = PlanoContas.objects.create(
            empresa=self.empresa, conta_pai=subgrupo, conta_contabil=self.conta_contabil,
            codigo="400010001", descricao="Matéria-prima", tipo="D", nivel=3,
            aceita_lancamento=True,
        )
        ContaPagarService.criar_e_quitar(
            filial=self.filial, funcionario=self.funcionario, tipo_lancamento="funcionario",
            documento_numero="MATERIAL-PAGO", valor_original=Decimal("450.00"),
            data_emissao=date(2026, 8, 20), data_vencimento=date(2026, 8, 30),
            plano_contas=categoria, data_pagamento=date(2026, 8, 20),
            forma_pagamento_utilizada=self.forma_pix,
        )
        ContaPagarService.criar_e_quitar(
            filial=self.filial, funcionario=self.funcionario, tipo_lancamento="funcionario",
            documento_numero="OUTRA-CATEGORIA-PAGA", valor_original=Decimal("120.00"),
            data_emissao=date(2026, 8, 20), data_vencimento=date(2026, 8, 30),
            plano_contas=self.categoria, data_pagamento=date(2026, 8, 20),
            forma_pagamento_utilizada=self.forma_pix,
        )
        request = RequestFactory().get(
            "/financeiro/pagar/pagas/", {"categoria_grupo": grupo.pk},
        )
        request.user = SimpleNamespace(
            is_authenticated=True,
            tem_permissao=lambda modulo, acao: True,
        )
        request.filial_ativa = self.filial

        response = ContaPagaListView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "MATERIAL-PAGO")
        self.assertNotContains(response, "OUTRA-CATEGORIA-PAGA")
        self.assertContains(response, "Filtro de classificação ativo")
        self.assertContains(response, "R$ 450,00 no período selecionado e na classificação filtrada")

    def test_pagamentos_parciais_preservam_formas_utilizadas(self):
        conta = ContaPagarService.criar(
            filial=self.filial,
            funcionario=self.funcionario,
            tipo_lancamento="funcionario",
            valor_original=Decimal("1800.00"),
            data_emissao=date(2026, 8, 20),
            data_vencimento=date(2026, 8, 30),
            plano_contas=self.categoria,
            forma_pagamento_prevista=self.forma_prevista,
        )
        ContaPagarService.registrar_pagamento(
            conta=conta,
            data_pagamento=date(2026, 8, 20),
            valor_pago=Decimal("800.00"),
            forma_pagamento=self.forma_pix,
            usuario=None,
        )
        ContaPagarService.registrar_pagamento(
            conta=conta,
            data_pagamento=date(2026, 8, 21),
            valor_pago=Decimal("1000.00"),
            forma_pagamento=self.forma_prevista,
            usuario=None,
        )

        pagamentos = list(conta.pagamentos.order_by("data_pagamento"))
        self.assertEqual(len(pagamentos), 2)
        self.assertEqual(pagamentos[0].forma_pagamento, self.forma_pix)
        self.assertEqual(pagamentos[1].forma_pagamento, self.forma_prevista)
        self.assertEqual(conta.status, "pago")

    def test_baixa_bloqueia_valor_acima_do_saldo_atualizado(self):
        conta = ContaPagarService.criar(
            filial=self.filial,
            funcionario=self.funcionario,
            tipo_lancamento="funcionario",
            valor_original=Decimal("100.00"),
            data_emissao=date(2026, 8, 20),
            data_vencimento=date(2026, 8, 30),
            plano_contas=self.categoria,
        )
        form = PagamentoContaPagarForm(
            {
                "data_pagamento": "2026-08-20",
                "valor_pago": "111.00",
                "valor_juros": "10.00",
                "valor_multa": "0",
                "valor_desconto": "0",
                "forma_pagamento": self.forma_pix.pk,
            },
            filial=self.filial,
            conta=conta,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("valor_pago", form.errors)

    def test_baixa_guarda_referencia_da_transacao(self):
        conta = ContaPagarService.criar(
            filial=self.filial,
            funcionario=self.funcionario,
            tipo_lancamento="funcionario",
            valor_original=Decimal("100.00"),
            data_emissao=date(2026, 8, 20),
            data_vencimento=date(2026, 8, 30),
            plano_contas=self.categoria,
        )
        ContaPagarService.registrar_pagamento(
            conta=conta,
            data_pagamento=date(2026, 8, 20),
            valor_pago=Decimal("100.00"),
            forma_pagamento=self.forma_pix,
            referencia_pagamento="PIX-E2E-123",
            usuario=None,
        )

        pagamento = conta.pagamentos.get()
        self.assertEqual(pagamento.referencia_pagamento, "PIX-E2E-123")

    def test_tela_de_baixa_exibe_resumo_e_opcao_parcial(self):
        conta = ContaPagarService.criar(
            filial=self.filial,
            funcionario=self.funcionario,
            tipo_lancamento="funcionario",
            valor_original=Decimal("100.00"),
            data_emissao=date(2026, 8, 20),
            data_vencimento=date(2026, 8, 30),
            plano_contas=self.categoria,
            forma_pagamento_prevista=self.forma_prevista,
        )
        request = RequestFactory().get(f"/financeiro/pagar/{conta.pk}/pagar/")
        request.user = SimpleNamespace(
            is_authenticated=True,
            tem_permissao=lambda modulo, acao: True,
        )
        request.filial_ativa = self.filial

        response = ContaPagarPagamentoView.as_view()(request, pk=conta.pk)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Resumo da baixa")
        self.assertContains(response, "Pagamento parcial")
        self.assertContains(response, "Referência da transação")
        self.assertContains(response, "Tarifa cobrada pelo banco")
        self.assertContains(
            response,
            f'value="{timezone.localdate().isoformat()}"',
        )

    def test_baixa_preseleciona_conta_bancaria_vinculada_a_forma(self):
        conta_bancaria = ContaBancaria.objects.create(
            filial=self.filial,
            descricao="ORENDA",
            banco_codigo="001",
        )
        self.forma_prevista.conta_bancaria_padrao = conta_bancaria
        self.forma_prevista.save(update_fields=["conta_bancaria_padrao"])
        conta = ContaPagarService.criar(
            filial=self.filial,
            funcionario=self.funcionario,
            tipo_lancamento="funcionario",
            valor_original=Decimal("100.00"),
            data_emissao=date(2026, 8, 20),
            data_vencimento=date(2026, 8, 30),
            plano_contas=self.categoria,
            forma_pagamento_prevista=self.forma_prevista,
        )

        form = PagamentoContaPagarForm(filial=self.filial, conta=conta)

        self.assertEqual(form.fields["conta_bancaria"].initial, conta_bancaria.pk)

    def test_baixa_sugere_tarifa_da_forma_mas_aceita_zero(self):
        self.forma_prevista.tarifa_pagamento_fixa = Decimal("0.50")
        self.forma_prevista.save(update_fields=["tarifa_pagamento_fixa"])
        conta = ContaPagarService.criar(
            filial=self.filial,
            funcionario=self.funcionario,
            tipo_lancamento="funcionario",
            valor_original=Decimal("100.00"),
            data_emissao=date(2026, 8, 20),
            data_vencimento=date(2026, 8, 30),
            plano_contas=self.categoria,
            forma_pagamento_prevista=self.forma_prevista,
        )

        form_inicial = PagamentoContaPagarForm(filial=self.filial, conta=conta)
        self.assertEqual(form_inicial.fields["tarifa_bancaria"].initial, Decimal("0.50"))

        form = PagamentoContaPagarForm(
            {
                "data_pagamento": "2026-08-20",
                "valor_pago": "100.00",
                "forma_pagamento": self.forma_prevista.pk,
                "tarifa_bancaria": "0.00",
            },
            filial=self.filial,
            conta=conta,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["tarifa_bancaria"], Decimal("0.00"))

    def test_baixa_aplica_conta_vinculada_quando_nao_informada(self):
        conta_bancaria = ContaBancaria.objects.create(
            filial=self.filial,
            descricao="ORENDA",
            banco_codigo="001",
        )
        self.forma_pix.conta_bancaria_padrao = conta_bancaria
        self.forma_pix.save(update_fields=["conta_bancaria_padrao"])
        conta = ContaPagarService.criar(
            filial=self.filial,
            funcionario=self.funcionario,
            tipo_lancamento="funcionario",
            valor_original=Decimal("100.00"),
            data_emissao=date(2026, 8, 20),
            data_vencimento=date(2026, 8, 30),
            plano_contas=self.categoria,
        )
        form = PagamentoContaPagarForm(
            {
                "data_pagamento": "2026-08-20",
                "valor_pago": "100.00",
                "forma_pagamento": self.forma_pix.pk,
            },
            filial=self.filial,
            conta=conta,
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["conta_bancaria"], conta_bancaria)

    def test_comprovante_valida_tipo_e_tamanho(self):
        invalido = SimpleUploadedFile(
            "programa.exe", b"conteudo", content_type="application/octet-stream",
        )
        grande = SimpleUploadedFile(
            "comprovante.pdf", b"x" * (10 * 1024 * 1024 + 1),
            content_type="application/pdf",
        )

        with self.assertRaisesMessage(ValidationError, "foto ou PDF"):
            validar_comprovante(invalido)
        with self.assertRaisesMessage(ValidationError, "máximo 10 MB"):
            validar_comprovante(grande)

    def test_comprovante_fica_guardado_e_download_exige_a_filial(self):
        with TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=Path(media_root)):
            conta = ContaPagarService.criar(
                filial=self.filial,
                funcionario=self.funcionario,
                tipo_lancamento="funcionario",
                valor_original=Decimal("1800.00"),
                data_emissao=date(2026, 8, 20),
                data_vencimento=date(2026, 8, 30),
                plano_contas=self.categoria,
            )
            arquivo = SimpleUploadedFile(
                "recibo agosto.pdf", b"%PDF-1.4 comprovante",
                content_type="application/pdf",
            )
            ContaPagarService.registrar_pagamento(
                conta=conta,
                data_pagamento=date(2026, 8, 20),
                valor_pago=Decimal("1800.00"),
                forma_pagamento=self.forma_pix,
                comprovante=arquivo,
                usuario=None,
            )
            pagamento = conta.pagamentos.get()
            self.assertEqual(pagamento.comprovante_nome_original, "recibo agosto.pdf")
            self.assertTrue(pagamento.comprovante_arquivo.storage.exists(
                pagamento.comprovante_arquivo.name,
            ))

            request = RequestFactory().get('/comprovante/?download=1')
            request.user = SimpleNamespace(
                is_authenticated=True,
                tem_permissao=lambda modulo, acao: True,
            )
            request.filial_ativa = self.filial
            response = ComprovantePagamentoView.as_view()(
                request, pk=conta.pk, pagamento_pk=pagamento.pk,
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn('attachment;', response['Content-Disposition'])
            self.assertEqual(b''.join(response.streaming_content), b"%PDF-1.4 comprovante")
            response.close()

            outra_filial = Filial.objects.create(
                empresa=self.empresa,
                razao_social="Outra filial",
                nome_fantasia="Outra filial",
                cnpj="50649395000128",
                uf="RN",
            )
            request_outra = RequestFactory().get('/comprovante/')
            request_outra.user = request.user
            request_outra.filial_ativa = outra_filial
            with self.assertRaises(Http404):
                ComprovantePagamentoView.as_view()(
                    request_outra, pk=conta.pk, pagamento_pk=pagamento.pk,
                )

    def test_admin_edita_lancamento_pago_e_registra_log_completo(self):
        perfil = PerfilAcesso.objects.create(
            empresa=self.empresa, nome="Administrador financeiro", is_admin=True,
        )
        usuario = Usuario.objects.create_user(
            email="admin-financeiro@eureka.com", nome="Administrador financeiro",
            password="teste1234", empresa=self.empresa, filial=self.filial, perfil=perfil,
        )
        fornecedor_anterior = Fornecedor.objects.create(
            filial=self.filial, tipo_pessoa="J", razao_social="Fornecedor anterior",
            cpf_cnpj="11222333000144",
        )
        fornecedor_novo = Fornecedor.objects.create(
            filial=self.filial, tipo_pessoa="J", razao_social="Fornecedor novo",
            cpf_cnpj="22333444000155",
        )
        FornecedorFilial.objects.bulk_create([
            FornecedorFilial(fornecedor=fornecedor_anterior, filial=self.filial),
            FornecedorFilial(fornecedor=fornecedor_novo, filial=self.filial),
        ])
        conta_anterior = ContaBancaria.objects.create(
            filial=self.filial, descricao="Conta anterior", banco_codigo="001",
        )
        conta_nova = ContaBancaria.objects.create(
            filial=self.filial, descricao="Conta nova", banco_codigo="260",
        )
        conta_contabil_nova = PlanoContabil.objects.create(
            empresa=self.empresa, codigo_referencia=8002, classificacao="3320200001",
            tipo_conta="A", descricao="SERVICOS ADMINISTRATIVOS",
            data_inicio=date(2015, 1, 1), nivel=5, ordem=2,
        )
        categoria_nova = PlanoContas.objects.create(
            empresa=self.empresa, conta_contabil=conta_contabil_nova,
            codigo="3320200001", descricao="Servicos Administrativos", tipo="D",
            nivel=3, aceita_lancamento=True,
        )
        conta = ContaPagarService.criar(
            filial=self.filial, fornecedor=fornecedor_anterior,
            tipo_lancamento=ContaPagar.TipoLancamento.FORNECEDOR,
            valor_original=Decimal("100.00"), data_emissao=date(2026, 8, 20),
            data_vencimento=date(2026, 8, 30), plano_contas=self.categoria,
            forma_pagamento_prevista=self.forma_prevista,
        )
        ContaPagarService.registrar_pagamento(
            conta=conta, data_pagamento=date(2026, 8, 20),
            valor_pago=Decimal("100.00"), forma_pagamento=self.forma_pix,
            conta_bancaria=conta_anterior, usuario=usuario,
        )
        conta_anterior.saldo_atual = Decimal("-100.00")
        conta_anterior.save(update_fields=["saldo_atual"])

        request = RequestFactory().post(
            f"/financeiro/pagar/{conta.pk}/editar-valor/",
            {
                "descricao_despesa": "Servicos administrativos",
                "fornecedor": fornecedor_novo.pk,
                "valor_original": "125.50",
                "data_vencimento": "2026-09-15",
                "data_competencia": "2026-09-01",
                "forma_pagamento_prevista": self.forma_pix.pk,
                "plano_contas": categoria_nova.pk,
                "data_pagamento": "2026-08-21",
                "forma_pagamento": self.forma_prevista.pk,
                "conta_bancaria": conta_nova.pk,
                "observacao": "Valor e dados conferidos com o fornecedor.",
                "motivo": "Correcao conforme comprovante bancario.",
            },
        )
        request.user = usuario
        request.filial_ativa = self.filial

        response = ContaPagarEditarValorView.as_view()(request, pk=conta.pk)

        self.assertEqual(response.status_code, 200, response.content)
        conta.refresh_from_db()
        pagamento = conta.pagamentos.get()
        self.assertEqual(conta.fornecedor, fornecedor_novo)
        self.assertEqual(conta.valor_original, Decimal("125.50"))
        self.assertEqual(conta.valor_pago, Decimal("125.50"))
        self.assertEqual(conta.valor_saldo, Decimal("0.00"))
        self.assertEqual(conta.data_vencimento, date(2026, 9, 15))
        self.assertEqual(conta.data_competencia, date(2026, 9, 1))
        self.assertEqual(conta.forma_pagamento_prevista, self.forma_pix)
        self.assertEqual(conta.plano_contas, categoria_nova)
        self.assertEqual(conta.conta_contabil, conta_contabil_nova)
        self.assertEqual(conta.observacao, "Valor e dados conferidos com o fornecedor.")
        self.assertEqual(pagamento.valor_pago, Decimal("125.50"))
        self.assertEqual(pagamento.data_pagamento, date(2026, 8, 21))
        self.assertEqual(pagamento.forma_pagamento, self.forma_prevista)
        self.assertEqual(pagamento.conta_bancaria, conta_nova)
        conta_anterior.refresh_from_db()
        conta_nova.refresh_from_db()
        self.assertEqual(conta_anterior.saldo_atual, Decimal("0.00"))
        self.assertEqual(conta_nova.saldo_atual, Decimal("-125.50"))
        log = RegistroAuditoria.objects.get(
            objeto_tipo=conta._meta.label_lower,
            objeto_id=conta.pk,
            acao=RegistroAuditoria.Acao.AJUSTAR,
        )
        self.assertEqual(log.usuario, usuario)
        self.assertEqual(log.justificativa, "Correcao conforme comprovante bancario.")
        self.assertEqual(
            set(log.metadados["contas_envolvidas"]),
            {conta_anterior.pk, conta_nova.pk},
        )
        self.assertEqual(log.dados_anteriores["fornecedor"], "Fornecedor anterior")
        self.assertEqual(log.dados_novos["fornecedor"], "Fornecedor novo")
        self.assertEqual(log.dados_anteriores["plano_contas"], "Salarios e Ordenados")
        self.assertEqual(log.dados_novos["plano_contas"], "Servicos Administrativos")

    def test_recorrencia_semanal_exibe_os_dias_em_vez_de_rotulo_generico(self):
        conta = ContaPagarService.criar_recorrencia(
            filial=self.filial,
            funcionario=self.funcionario,
            tipo_lancamento="funcionario",
            valor_original=Decimal("100.00"),
            data_emissao=date(2026, 8, 20),
            data_vencimento=date(2026, 8, 24),
            plano_contas=self.categoria,
            frequencia="semanal",
            quantidade=5,
            dias_semana=["0", "1", "2", "3", "4"],
        )[0]
        self.assertEqual(conta.recorrencia_exibicao, "Segunda a sexta")

        conta.dias_semana_recorrencia = "5"
        self.assertEqual(conta.recorrencia_exibicao, "Sábado")

    def test_filtro_beneficiario_separa_funcionario_da_descricao(self):
        outra = Funcionario.objects.create(
            filial=self.filial, nome="Joana Souza", cpf="98765432100",
            cargo="Costureira", salario_base=Decimal("1800.00"),
        )
        for funcionario in (self.funcionario, outra):
            ContaPagarService.criar(
                filial=self.filial, funcionario=funcionario,
                tipo_lancamento="funcionario", descricao_despesa="DIÁRIA",
                valor_original=Decimal("100.00"), data_emissao=date(2026, 8, 20),
                data_vencimento=date(2026, 9, 1), plano_contas=self.categoria,
            )
        request = RequestFactory().get("/financeiro/pagar/", {
            "status": "todos", "beneficiario": "Joana Souza",
        })
        request.filial_ativa = self.filial

        contas, _, filtros = _filtrar_contas_pagar_abertas(request)

        self.assertEqual(list(contas.values_list("funcionario__nome", flat=True)), ["Joana Souza"])
        self.assertEqual(filtros["beneficiario"], "Joana Souza")

    def test_relatorio_contas_pagar_usa_impressao_tabular_e_os_mesmos_filtros(self):
        conta = ContaPagarService.criar(
            filial=self.filial, funcionario=self.funcionario,
            tipo_lancamento='funcionario', descricao_despesa='DIÁRIA DE PRODUÇÃO',
            documento_numero='FOLHA-09/2026', valor_original=Decimal('180.50'),
            data_emissao=date(2026, 9, 1), data_vencimento=date(2026, 9, 10),
            plano_contas=self.categoria, forma_pagamento_prevista=self.forma_prevista,
        )
        request = RequestFactory().get('/financeiro/pagar/relatorio/', {
            'status': 'todos', 'beneficiario': 'Maria Silva',
            'data_ini': '2026-09-01', 'data_fim': '2026-09-30',
        })
        request.user = SimpleNamespace(
            is_authenticated=True,
            tem_permissao=lambda modulo, acao: True,
        )
        request.filial_ativa = self.filial

        response = ContaPagarRelatorioView.as_view()(request)

        self.assertContains(response, 'Imprimir / Salvar PDF')
        self.assertContains(response, 'DIÁRIA DE PRODUÇÃO')
        self.assertContains(response, 'Maria Silva')
        self.assertContains(response, 'FOLHA-09/2026')
        self.assertContains(response, 'R$ 180,50')
        self.assertContains(response, f'#{conta.pk}')
        self.assertNotContains(response, 'html2pdf')
        self.assertNotContains(response, 'forn-card')

    def test_admin_escolhe_editar_somente_um_ou_todos_os_restantes(self):
        perfil = PerfilAcesso.objects.create(
            empresa=self.empresa, nome="Admin recorrências", is_admin=True,
        )
        usuario = Usuario.objects.create_user(
            email="admin-recorrencias@eureka.com", nome="Admin recorrências",
            password="teste1234", empresa=self.empresa, filial=self.filial, perfil=perfil,
        )
        contas = ContaPagarService.criar_recorrencia(
            filial=self.filial, funcionario=self.funcionario,
            tipo_lancamento="funcionario", descricao_despesa="DIÁRIA",
            valor_original=Decimal("100.00"), data_emissao=date(2026, 8, 20),
            data_vencimento=date(2026, 8, 24), plano_contas=self.categoria,
            frequencia="semanal", quantidade=3, dias_semana=["0"],
        )
        dados_base = {
            "descricao_despesa": "DIÁRIA AJUSTADA",
            "valor_original": "120.00",
            "data_vencimento": "2026-08-24",
            "plano_contas": self.categoria.pk,
            "motivo": "Correção da recorrência.",
        }
        request = RequestFactory().post(
            f"/financeiro/pagar/{contas[0].pk}/editar-valor/",
            {**dados_base, "escopo_edicao": "somente"},
        )
        request.user = usuario
        request.filial_ativa = self.filial
        response = ContaPagarEditarValorView.as_view()(request, pk=contas[0].pk)
        self.assertEqual(response.status_code, 200, response.content)
        contas[0].refresh_from_db()
        contas[1].refresh_from_db()
        self.assertEqual(contas[0].valor_original, Decimal("120.00"))
        self.assertEqual(contas[1].valor_original, Decimal("100.00"))

        request = RequestFactory().post(
            f"/financeiro/pagar/{contas[1].pk}/editar-valor/",
            {
                **dados_base,
                "data_vencimento": "2026-08-31",
                "escopo_edicao": "restantes",
                "frequencia_recorrencia": "semanal",
                "quantidade_recorrencias": "2",
                "dias_semana_recorrencia": ["0"],
                "regra_vencimento_mensal": "data_informada",
            },
        )
        request.user = usuario
        request.filial_ativa = self.filial
        response = ContaPagarEditarValorView.as_view()(request, pk=contas[1].pk)
        self.assertEqual(response.status_code, 200, response.content)
        contas[0].refresh_from_db()
        contas[1].refresh_from_db()
        contas[2].refresh_from_db()
        self.assertEqual(contas[0].valor_original, Decimal("120.00"))
        self.assertEqual(contas[1].valor_original, Decimal("120.00"))
        self.assertEqual(contas[2].valor_original, Decimal("120.00"))

    def test_admin_exclui_este_titulo_e_todos_os_proximos(self):
        perfil = PerfilAcesso.objects.create(
            empresa=self.empresa, nome="Admin exclusão recorrência", is_admin=True,
        )
        usuario = Usuario.objects.create_user(
            email="admin-exclusao-recorrencia@eureka.com", nome="Admin exclusão",
            password="teste1234", empresa=self.empresa, filial=self.filial, perfil=perfil,
        )
        contas = ContaPagarService.criar_recorrencia(
            filial=self.filial, funcionario=self.funcionario,
            tipo_lancamento="funcionario", descricao_despesa="DIÁRIA",
            valor_original=Decimal("100.00"), data_emissao=date(2026, 8, 20),
            data_vencimento=date(2026, 8, 24), plano_contas=self.categoria,
            frequencia="semanal", quantidade=3, dias_semana=["0"],
        )
        request = RequestFactory().post(
            f"/financeiro/pagar/{contas[1].pk}/excluir/",
            {"motivo": "Série lançada incorretamente.", "escopo_recorrencia": "restantes"},
        )
        request.user = usuario
        request.filial_ativa = self.filial

        response = ContaPagarExcluirView.as_view()(request, pk=contas[1].pk)

        self.assertEqual(response.status_code, 200, response.content)
        for conta in contas:
            conta.refresh_from_db()
        self.assertIsNone(contas[0].excluido_em)
        self.assertIsNotNone(contas[1].excluido_em)
        self.assertIsNotNone(contas[2].excluido_em)
