from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.messages.storage.fallback import FallbackStorage
from django.http import Http404
from django.test import RequestFactory, TestCase

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario, RegistroAuditoria
from apps.core.models.parametros import ParametrosSistema
from apps.core.services.exceptions import DomainError
from apps.financeiro.forms.receber import ContaReceberForm, ReferenciaContaReceberForm
from apps.financeiro.models.receber_pagar import ContaReceber, PagamentoContaReceber
from apps.financeiro.services.receber_service import ContaReceberService
from apps.financeiro.views.receber import (
    ContaReceberCreateView, ContaReceberDetailView, ContaReceberListView,
    ContaReceberReferenciaView, ContaReceberRelatorioView,
)


class ReceberEntregaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        empresa = Empresa.objects.create(
            razao_social='Eureka teste', cnpj='50649395000126',
            regime_tributario='simples_nacional', codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=empresa, razao_social='Eureka', cnpj=empresa.cnpj, uf='RN',
        )
        cls.outra = Filial.objects.create(
            empresa=empresa, razao_social='Outra filial', cnpj='11222333000181', uf='RN',
        )
        cls.params = ParametrosSistema.objects.create(
            filial=cls.filial, controlar_entrega_contas_receber=True,
        )
        cls.cliente = Cliente.objects.create(filial=cls.filial, razao_social='Cliente teste')
        perfil = PerfilAcesso.objects.create(empresa=empresa, nome='Admin', is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email='entrega@example.test', nome='Operador', password='teste',
            empresa=empresa, filial=cls.filial, perfil=perfil, is_superuser=True,
        )
        cls.conta = ContaReceberService.criar(
            filial=cls.filial, cliente=cls.cliente, valor_original=Decimal('1100.00'),
            data_emissao=date(2026, 8, 31), data_vencimento=date(2026, 9, 20),
            documento_numero='Pedido - AABB ACTIVE - RICARDO', usuario=cls.usuario,
            status_entrega='prevista', previsao_entrega_complemento='Outubro/2026',
        )

    def request(self, method='get', data=None, filial=None):
        request = getattr(RequestFactory(), method)('/financeiro/receber/', data or {})
        request.user = self.usuario
        request.filial_ativa = filial or self.filial
        request.session = {}
        request._messages = FallbackStorage(request)
        return request

    def test_documento_textual_criacao_e_limite_sem_truncamento(self):
        dados = {
            'cliente': self.cliente.pk, 'documento_numero': 'Pedido - ' + 'A' * 191,
            'valor_original': '200.00', 'data_emissao': '2026-08-31',
            'data_vencimento': '2026-09-20', 'parcela': 1, 'total_parcelas': 1,
            'status_entrega': 'prevista', 'data_entrega_prevista': '2026-09-10',
        }
        response = ContaReceberCreateView.as_view()(self.request('post', dados))
        self.assertEqual(response.status_code, 302)
        conta = ContaReceber.objects.exclude(pk=self.conta.pk).get()
        self.assertEqual(conta.documento_numero, dados['documento_numero'])
        self.assertEqual(conta.data_entrega_prevista, date(2026, 9, 10))
        dados['documento_numero'] += 'X'
        form = ContaReceberForm(dados, filial=self.filial)
        self.assertFalse(form.is_valid())
        self.assertIn('documento_numero', form.errors)

    def test_validacao_previsao_exata_aproximada_e_sem_previsao(self):
        for dados, valido in [
            ({'status_entrega': 'prevista'}, False),
            ({'status_entrega': 'prevista', 'previsao_entrega_complemento': 'Outubro/2026'}, True),
            ({'status_entrega': 'prevista', 'data_entrega_prevista': '2026-09-10'}, True),
            ({'status_entrega': 'sem_previsao'}, True),
            ({'status_entrega': 'sem_previsao', 'data_entrega_prevista': '2026-09-10'}, False),
            ({'status_entrega': 'entregue'}, True),
            ({'status_entrega': 'invalido'}, False),
        ]:
            with self.subTest(dados=dados):
                form = ReferenciaContaReceberForm(dados, filial=self.filial)
                self.assertEqual(form.is_valid(), valido, form.errors)

    def test_entregue_nao_quita_nao_altera_financeiro_e_audita(self):
        antes = {campo: getattr(self.conta, campo) for campo in (
            'valor_original', 'valor_final', 'valor_saldo', 'valor_pago',
            'status', 'data_emissao', 'data_vencimento', 'data_pagamento',
        )}
        response = ContaReceberReferenciaView.as_view()(self.request('post', {
            'documento_numero': self.conta.documento_numero, 'status_entrega': 'entregue',
            'valor_original': '0', 'status': 'pago', 'data_vencimento': '2030-01-01',
        }), pk=self.conta.pk)
        self.assertEqual(response.status_code, 302)
        self.conta.refresh_from_db()
        self.assertEqual(self.conta.status_entrega, 'entregue')
        for campo, valor in antes.items():
            self.assertEqual(getattr(self.conta, campo), valor, campo)
        self.assertFalse(PagamentoContaReceber.objects.exists())
        audit = RegistroAuditoria.objects.get(objeto_id=self.conta.pk)
        self.assertEqual(audit.dados_anteriores['status_entrega'], 'prevista')
        self.assertEqual(audit.dados_novos['status_entrega'], 'entregue')

    def test_filial_desativada_oculta_campos_e_ignora_post_forjado(self):
        self.params.controlar_entrega_contas_receber = False
        self.params.save()
        form = ReferenciaContaReceberForm({
            'documento_numero': '739', 'status_entrega': 'entregue',
        }, filial=self.filial)
        self.assertNotIn('status_entrega', form.fields)
        self.assertTrue(form.is_valid())
        ContaReceberService.editar_referencia(
            conta=self.conta, dados=form.cleaned_data, usuario=self.usuario,
        )
        self.conta.refresh_from_db()
        self.assertEqual(self.conta.status_entrega, 'prevista')
        self.assertEqual(self.conta.previsao_entrega_complemento, 'Outubro/2026')
        self.assertEqual(self.conta.documento_numero, '739')

    def test_edicao_isolada_por_filial_e_permissao(self):
        with self.assertRaises(Http404):
            ContaReceberReferenciaView.as_view()(
                self.request('post', {'documento_numero': 'INVASAO'}, filial=self.outra),
                pk=self.conta.pk,
            )
        with patch.object(Usuario, 'tem_permissao', return_value=False):
            response = ContaReceberReferenciaView.as_view()(
                self.request('post', {'documento_numero': 'INVASAO'}), pk=self.conta.pk,
            )
        self.assertIn(response.status_code, (302, 403))
        self.conta.refresh_from_db()
        self.assertTrue(self.conta.documento_numero.startswith('Pedido - '))

    def test_conta_cancelada_nao_pode_ser_editada(self):
        ContaReceber.objects.filter(pk=self.conta.pk).update(status='cancelado')
        with self.assertRaises(DomainError):
            ContaReceberService.editar_referencia(
                conta=self.conta, dados={'documento_numero': 'alterado'}, usuario=self.usuario,
            )

    def test_listagem_relatorio_detalhe_e_modal_respeitam_configuracao(self):
        for habilitada in (True, False):
            self.params.controlar_entrega_contas_receber = habilitada
            self.params.save()
            for view, kwargs, query in (
                (ContaReceberListView, {}, {}), (ContaReceberRelatorioView, {}, {}),
                (ContaReceberDetailView, {'pk': self.conta.pk}, {}),
                (ContaReceberDetailView, {'pk': self.conta.pk}, {'modal': '1'}),
            ):
                with self.subTest(habilitada=habilitada, view=view, query=query):
                    response = view.as_view()(self.request(data=query), **kwargs)
                    self.assertContains(response, self.conta.documento_numero)
                    if habilitada:
                        self.assertContains(response, 'Previsão: Outubro/2026')
                    else:
                        self.assertNotContains(response, 'Previsão: Outubro/2026')
                        self.assertNotContains(response, 'name="entrega"')

    def test_filtro_de_entrega_consistente_no_relatorio_e_lista(self):
        for view in (ContaReceberListView, ContaReceberRelatorioView):
            response = view.as_view()(self.request(data={'entrega': 'prevista'}))
            self.assertContains(response, self.conta.documento_numero)
            response = view.as_view()(self.request(data={'entrega': 'entregue'}))
            self.assertNotContains(response, self.conta.documento_numero)
        self.params.controlar_entrega_contas_receber = False
        self.params.save()
        response = ContaReceberRelatorioView.as_view()(self.request(data={'entrega': 'entregue'}))
        self.assertContains(response, self.conta.documento_numero)

    def test_filial_sem_parametros_nao_habilita_entrega(self):
        form = ReferenciaContaReceberForm(filial=self.outra)
        self.assertFalse(form.entrega_habilitada)
        self.assertNotIn('status_entrega', form.fields)

    def test_documento_e_previsao_escapados_no_html(self):
        ContaReceber.objects.filter(pk=self.conta.pk).update(
            documento_numero='<script>alert(1)</script>',
            previsao_entrega_complemento='<img src=x onerror=alert(1)>',
        )
        for view in (ContaReceberListView, ContaReceberRelatorioView):
            response = view.as_view()(self.request())
            self.assertNotContains(response, '<script>alert(1)</script>')
            self.assertNotContains(response, '<img src=x onerror=alert(1)>')
            self.assertContains(response, '&lt;script&gt;')
