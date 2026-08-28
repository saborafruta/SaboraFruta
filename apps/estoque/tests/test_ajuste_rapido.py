from decimal import Decimal
from unittest.mock import patch

from django.test import RequestFactory, TestCase

from apps.core.models import Empresa, Filial, PerfilAcesso, RegistroAuditoria, Usuario
from apps.estoque.models import Estoque, MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.estoque.views.estoque import AjusteRapidoEstoqueAtualizarView, AjusteRapidoEstoqueView
from apps.produtos.models import Produto, ProdutoFilial, UnidadeMedida


class AjusteRapidoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        empresa = Empresa.objects.create(razao_social='Teste', cnpj='12345678000191', regime_tributario='simples_nacional', codigo_regime_tributario=1)
        cls.filial = Filial.objects.create(empresa=empresa, razao_social='Teste', cnpj='12345678000192', uf='RN')
        perfil = PerfilAcesso.objects.create(empresa=empresa, nome='Admin', is_admin=True)
        cls.usuario = Usuario.objects.create_user(email='contagem@example.test', nome='Teste', password='test', empresa=empresa, filial=cls.filial, perfil=perfil)
        unidade = UnidadeMedida.objects.create(empresa=empresa, sigla='UN', descricao='Unidade', tipo='unidade')
        cls.produto = Produto.objects.create(filial=cls.filial, unidade_medida=unidade, descricao='Camisa GG', ncm='61099000', controla_lote=False)
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.filial)

    def setUp(self):
        self.factory = RequestFactory()
        self.estoque = Estoque.objects.create(produto=self.produto, filial=self.filial, quantidade_atual=3, quantidade_disponivel=2, quantidade_reservada=1)

    def request(self, quantidade, **extras):
        request = self.factory.post('/estoque/ajuste-rapido/atualizar/', {'produto_id': self.produto.pk, 'quantidade': quantidade, **extras})
        request.user = self.usuario
        request.filial_ativa = self.filial
        request.session = self.client.session
        return request

    def post(self, quantidade, **extras):
        return AjusteRapidoEstoqueAtualizarView.as_view()(self.request(quantidade, **extras))

    def test_digitacao_eh_saldo_final_e_nao_incremento(self):
        response = self.post('10', interacao='digitacao', saldo_exibido='3')
        self.assertEqual(response.status_code, 200)
        self.estoque.refresh_from_db()
        self.assertEqual(self.estoque.quantidade_atual, 10)
        self.assertEqual(self.estoque.quantidade_disponivel, 9)
        movimento = MovimentacaoEstoque.objects.get()
        self.assertEqual(movimento.quantidade, 7)
        self.assertEqual(movimento.quantidade_anterior, 3)
        self.assertEqual(movimento.quantidade_posterior, 10)
        self.assertEqual(RegistroAuditoria.objects.get().metadados['interacao'], 'digitacao')

    def test_repetir_envio_legado_nao_aplica_diferenca_duas_vezes(self):
        self.assertEqual(self.post('10').status_code, 200)
        self.assertEqual(self.post('10').status_code, 200)
        self.estoque.refresh_from_db()
        self.assertEqual(self.estoque.quantidade_atual, 10)
        self.assertEqual(MovimentacaoEstoque.objects.count(), 1)

    def test_lock_obtido_antes_de_calcular_delta(self):
        # Simula uma transação concorrente que terminou antes de obtermos o
        # lock: o saldo mudou de 3 para 6. A diferença correta agora é 4.
        manager = Estoque.objects
        original = manager.select_for_update
        def locked(*args, **kwargs):
            manager.filter(pk=self.estoque.pk).update(quantidade_atual=6)
            return original(*args, **kwargs)
        registrar = MovimentacaoService.registrar_movimentacao
        with patch.object(manager, 'select_for_update', side_effect=locked) as lock:
            with patch.object(MovimentacaoService, 'registrar_movimentacao', wraps=registrar) as movement:
                response = self.post('10')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(lock.called)
        self.assertEqual(movement.call_args.kwargs['quantidade'], 4)
        self.estoque.refresh_from_db()
        self.assertEqual(self.estoque.quantidade_atual, 10)

    def test_saldo_desatualizado_recusa_sem_movimentar(self):
        response = self.post('10', saldo_exibido='2')
        self.assertEqual(response.status_code, 409)
        self.assertFalse(MovimentacaoEstoque.objects.exists())
        self.estoque.refresh_from_db()
        self.assertEqual(self.estoque.quantidade_atual, 3)

    def test_botoes_somam_e_subtraem_uma_unidade(self):
        for value, before in [('4', '3'), ('3', '4'), ('2', '3')]:
            self.assertEqual(self.post(value, saldo_exibido=before, interacao='botoes').status_code, 200)
        self.estoque.refresh_from_db()
        self.assertEqual(self.estoque.quantidade_atual, 2)
        self.assertTrue(all(m.quantidade == 1 for m in MovimentacaoEstoque.objects.all()))

    def test_reducao_e_zero_sao_saldos_finais(self):
        self.assertEqual(self.post('1').status_code, 200)
        self.assertEqual(self.post('0').status_code, 200)
        self.assertEqual(self.post('0').status_code, 200)
        self.estoque.refresh_from_db()
        self.assertEqual(self.estoque.quantidade_atual, 0)
        self.assertEqual(MovimentacaoEstoque.objects.count(), 2)

    def test_quantidades_invalidas_nunca_zeram_estoque(self):
        for value in ['', ' ', '-1', 'abc', 'NaN', 'Infinity', '1000000000']:
            with self.subTest(value=value):
                self.assertEqual(self.post(value).status_code, 400)
        self.assertFalse(MovimentacaoEstoque.objects.exists())
        self.estoque.refresh_from_db()
        self.assertEqual(self.estoque.quantidade_atual, 3)

    def test_quantidade_fracionaria(self):
        response = self.post('10,125')
        self.assertEqual(response.status_code, 200)
        self.estoque.refresh_from_db()
        self.assertEqual(self.estoque.quantidade_atual, Decimal('10.125'))

    def test_cria_saldo_ausente_com_lock(self):
        self.estoque.delete()
        self.assertEqual(self.post('10').status_code, 200)
        self.assertEqual(self.post('10').status_code, 200)
        self.assertEqual(Estoque.objects.get(produto=self.produto).quantidade_atual, 10)
        self.assertEqual(MovimentacaoEstoque.objects.count(), 1)

    def test_falha_de_auditoria_reverte_movimento_e_saldo(self):
        with patch('apps.estoque.views.estoque._auditar_estoque', side_effect=RuntimeError('teste')):
            self.assertEqual(self.post('10').status_code, 500)
        self.estoque.refresh_from_db()
        self.assertEqual(self.estoque.quantidade_atual, 3)
        self.assertFalse(MovimentacaoEstoque.objects.exists())

    def test_get_nao_recupera_contagem_antiga_da_sessao(self):
        request = self.request('10')
        request.method = 'GET'
        request.session[AjusteRapidoEstoqueView.session_key] = {str(self.produto.pk): {'contado': '99', 'conferido': True}}
        captured = {}
        from django.http import HttpResponse
        def render(request, template, context):
            captured.update(context)
            return HttpResponse('ok')
        with patch('apps.estoque.views.estoque.render', side_effect=render):
            AjusteRapidoEstoqueView.as_view()(request)
        self.assertEqual(list(captured['produtos'])[0].ajuste_rapido_contado, '3')
