from datetime import date
from decimal import Decimal
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory

from django.apps import apps as django_apps
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from django.http import Http404
from django.test import RequestFactory, TestCase

from apps.cadastros.forms import FuncionarioForm
from apps.cadastros.models import Funcionario
from apps.core.models import Empresa, Filial
from apps.financeiro.forms.pagar import ContaPagarForm, validar_comprovante
from apps.financeiro.models import (
    ContaPagar,
    FormaPagamento,
    PagamentoContaPagar,
    PlanoContabil,
    PlanoContas,
)
from apps.financeiro.services.pagar_service import ContaPagarService
from apps.financeiro.views.pagar import ComprovantePagamentoView


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
            "data_emissao": "2026-08-20",
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
