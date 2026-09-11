"""Telas de depósito e transferência interna (fase 2)."""
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import Deposito, Estoque, MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.produtos.models import (
    Produto, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial,
)


class DepositoViewsBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Dep Views LTDA', nome_fantasia='DepViews',
            cnpj='29345678000101',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='F', nome_fantasia='Matriz',
            cnpj='29345678000102', uf='RN', is_matriz=True,
        )
        cls.perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='depviews@teste.local', nome='Dep', password='x' * 12,
            empresa=cls.empresa, filial=cls.filial, perfil=cls.perfil,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='UN', descricao='Unidade',
            tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        cls.produto = Produto.objects.create(
            filial=cls.filial, unidade_medida=cls.unidade, descricao='Item',
            ncm='20089900', permite_venda_sem_estoque=False,
        )
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.filial)

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.usuario)

    def _set_filial_sessao(self):
        session = self.client.session
        session['filial_ativa_id'] = self.filial.pk
        session.save()


class DepositoCrudViewTests(DepositoViewsBase):

    def test_lista_abre(self):
        self._set_filial_sessao()
        resp = self.client.get(reverse('estoque:deposito-list'))
        self.assertEqual(resp.status_code, 200)

    def test_cria_deposito(self):
        self._set_filial_sessao()
        resp = self.client.post(reverse('estoque:deposito-create'), {
            'nome': 'Fábrica', 'tipo': 'producao',
            'permite_producao': 'on', 'ativo': 'on',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            Deposito.objects.filter(filial=self.filial, nome='Fábrica').exists()
        )

    def test_nao_cria_nome_repetido(self):
        self._set_filial_sessao()
        Deposito.objects.create(filial=self.filial, nome='Fábrica')
        resp = self.client.post(reverse('estoque:deposito-create'), {
            'nome': 'fábrica', 'tipo': 'geral', 'ativo': 'on',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Já existe um depósito')


class TransferenciaInternaViewTests(DepositoViewsBase):

    def test_tela_abre(self):
        self._set_filial_sessao()
        resp = self.client.get(reverse('estoque:transferencia-interna'))
        self.assertEqual(resp.status_code, 200)

    def test_transfere_pela_tela(self):
        self._set_filial_sessao()
        loja_id = Deposito.padrao_id(self.filial.pk)
        fabrica = Deposito.objects.create(filial=self.filial, nome='Fábrica')
        MovimentacaoService.registrar_movimentacao(
            produto_id=self.produto.pk, filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal('10'), usuario_id=self.usuario.pk,
            valor_unitario=Decimal('2'),
        )
        resp = self.client.post(reverse('estoque:transferencia-interna'), {
            'produto': self.produto.pk,
            'deposito_origem': loja_id,
            'deposito_destino': fabrica.pk,
            'quantidade': '4',
            'observacao': '',
        })
        self.assertEqual(resp.status_code, 302)
        saldo_fabrica = Estoque.objects.get(
            produto=self.produto, filial=self.filial, deposito=fabrica,
        ).quantidade_atual
        self.assertEqual(saldo_fabrica, Decimal('4'))


class EstoquePorDepositoJsonViewTests(DepositoViewsBase):

    def test_soma_por_deposito_e_devolve_todos_os_ativos(self):
        self._set_filial_sessao()
        loja_id = Deposito.padrao_id(self.filial.pk)
        fabrica = Deposito.objects.create(filial=self.filial, nome='Fábrica')
        inativo = Deposito.objects.create(
            filial=self.filial, nome='Antigo', ativo=False,
        )
        MovimentacaoService.registrar_movimentacao(
            produto_id=self.produto.pk, filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal('10'), usuario_id=self.usuario.pk,
            valor_unitario=Decimal('2'), deposito_id=loja_id,
        )
        MovimentacaoService.registrar_movimentacao(
            produto_id=self.produto.pk, filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal('3'), usuario_id=self.usuario.pk,
            valor_unitario=Decimal('2'), deposito_id=fabrica.pk,
        )

        resp = self.client.get(
            reverse('estoque:estoque-por-deposito-json'),
            {'produto': self.produto.pk},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['ok'])
        por_id = {row['id']: row for row in data['results']}
        self.assertEqual(por_id[loja_id]['atual'], 10.0)
        self.assertEqual(por_id[fabrica.pk]['atual'], 3.0)
        self.assertNotIn(inativo.pk, por_id)

    def test_sem_produto_devolve_erro(self):
        self._set_filial_sessao()
        resp = self.client.get(reverse('estoque:estoque-por-deposito-json'))
        self.assertEqual(resp.status_code, 400)
