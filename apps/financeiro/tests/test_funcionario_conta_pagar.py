from datetime import date
from decimal import Decimal
from importlib import import_module
import json
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory

from django.apps import apps as django_apps
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from django.http import Http404
from django.test import RequestFactory, TestCase
from django.utils import timezone

from apps.cadastros.forms import FornecedorRapidoForm, FuncionarioForm
from apps.cadastros.models import Fornecedor, Funcionario
from apps.cadastros.views.fornecedor import FornecedorAjaxCreateView
from apps.compras.models import EntradaNF
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.financeiro.forms.pagar import (
    ContaPagarForm,
    PagamentoContaPagarForm,
    validar_comprovante,
)
from apps.financeiro.models import (
    ContaPagar,
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
    ContaPagarNotaFiscalLookupView,
    ContaPagarPagamentoView,
)


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
        self.assertContains(response, "Nova conta paga")

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
        self.assertContains(response, 'value="2026-08-20"')

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
