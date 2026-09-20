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


class DepositoAviamentoViewTests(DepositoViewsBase):
    """Painel de aviamentos dentro da edição do depósito."""

    def setUp(self):
        super().setUp()
        self._set_filial_sessao()
        self.aviamentos = Deposito.objects.create(
            filial=self.filial, nome='Aviamentos', tipo='producao',
            tipos_material=['linha', 'ziper'],
        )
        self.url_novo = reverse('estoque:deposito-aviamento-create', args=[self.aviamentos.pk])

    def _payload(self, **extra):
        dados = {
            'nome': 'Zíper nylon nº 5 preto', 'tipo': 'ziper',
            'unidade_medida': self.unidade.pk, 'codigo': 'ZP5',
            'quantidade_inicial': '25',
        }
        dados.update(extra)
        return dados

    def test_edicao_mostra_painel_e_formulario_rapido(self):
        resp = self.client.get(reverse('estoque:deposito-update', args=[self.aviamentos.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="aviamentos"')
        self.assertContains(resp, self.url_novo)

    def test_criacao_de_deposito_nao_mostra_painel(self):
        resp = self.client.get(reverse('estoque:deposito-create'))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'id="aviamentos"')

    def test_cadastra_aviamento_com_saldo_no_deposito(self):
        from apps.moda.models import Aviamento

        resp = self.client.post(self.url_novo, self._payload())
        self.assertEqual(resp.status_code, 302)
        self.assertIn('#aviamentos', resp['Location'])

        avi = Aviamento.objects.get(filial=self.filial, nome='Zíper nylon nº 5 preto')
        self.assertEqual(avi.tipo, 'ziper')
        self.assertEqual(avi.codigo, 'ZP5')
        self.assertEqual(avi.unidade, 'un')
        self.assertIsNotNone(avi.produto_estoque_id)
        saldo = Estoque.objects.get(
            filial=self.filial, produto=avi.produto_estoque, deposito=self.aviamentos,
        )
        self.assertEqual(saldo.quantidade_atual, Decimal('25'))

        resp = self.client.get(reverse('estoque:deposito-update', args=[self.aviamentos.pk]))
        self.assertContains(resp, 'Zíper nylon nº 5 preto')

    def test_cadastra_sem_saldo_inicial(self):
        from apps.moda.models import Aviamento

        resp = self.client.post(self.url_novo, self._payload(quantidade_inicial=''))
        self.assertEqual(resp.status_code, 302)
        avi = Aviamento.objects.get(filial=self.filial, nome='Zíper nylon nº 5 preto')
        self.assertFalse(Estoque.objects.filter(produto=avi.produto_estoque).exists())

    def test_nome_repetido_e_recusado(self):
        from apps.moda.models import Aviamento

        Aviamento.objects.create(filial=self.filial, nome='Botão 4 furos', tipo='botao')
        resp = self.client.post(self.url_novo, self._payload(nome='botão 4 FUROS', tipo='botao'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Já existe')
        self.assertEqual(Aviamento.objects.filter(filial=self.filial).count(), 1)

    def test_deposito_de_outra_filial_da_404(self):
        outra = Filial.objects.create(
            empresa=self.empresa, razao_social='G', nome_fantasia='Filial 2',
            cnpj='29345678000203', uf='RN',
        )
        alheio = Deposito.objects.create(filial=outra, nome='Alheio')
        resp = self.client.post(
            reverse('estoque:deposito-aviamento-create', args=[alheio.pk]), self._payload(),
        )
        self.assertEqual(resp.status_code, 404)

    def test_cria_unidade_nova_junto_com_o_aviamento(self):
        from apps.moda.models import Aviamento
        from apps.produtos.models import UnidadeMedida, UnidadeMedidaFilial

        resp = self.client.post(self.url_novo, self._payload(
            nome='Elástico chato 25mm', tipo='elastico',
            unidade_medida='__nova__', nova_unidade_sigla='m',
            nova_unidade_descricao='Metro', nova_unidade_tipo='comprimento',
            quantidade_inicial='50',
        ))
        self.assertEqual(resp.status_code, 302)
        metro = UnidadeMedida.objects.get(empresa=self.empresa, sigla='M')
        self.assertEqual(metro.descricao, 'Metro')
        self.assertEqual(metro.tipo, 'comprimento')
        self.assertTrue(UnidadeMedidaFilial.objects.filter(unidade=metro, filial=self.filial).exists())
        avi = Aviamento.objects.get(filial=self.filial, nome='Elástico chato 25mm')
        self.assertEqual(avi.unidade, 'm')
        self.assertEqual(avi.produto_estoque.unidade_medida, metro)
        self.assertIn(f'un={metro.pk}', resp['Location'])

    def test_unidade_nova_com_sigla_repetida_e_recusada(self):
        from apps.produtos.models import UnidadeMedida

        resp = self.client.post(self.url_novo, self._payload(
            unidade_medida='__nova__', nova_unidade_sigla='un',
            nova_unidade_descricao='Outra unidade',
        ))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Já existe a unidade')
        self.assertEqual(UnidadeMedida.objects.filter(empresa=self.empresa).count(), 1)

    def test_unidade_nova_sem_nome_nao_cria_nada(self):
        from apps.moda.models import Aviamento
        from apps.produtos.models import UnidadeMedida

        resp = self.client.post(self.url_novo, self._payload(
            unidade_medida='__nova__', nova_unidade_sigla='m', nova_unidade_descricao='',
        ))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Informe o nome.')
        self.assertFalse(UnidadeMedida.objects.filter(empresa=self.empresa, sigla='M').exists())
        self.assertFalse(Aviamento.objects.filter(filial=self.filial).exists())

    def test_sem_unidade_nenhuma_e_recusado(self):
        resp = self.client.post(self.url_novo, self._payload(unidade_medida=''))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'obrigat')


    def test_lista_oferece_metro_pronto_e_nao_repete_unidade_existente(self):
        resp = self.client.get(reverse('estoque:deposito-update', args=[self.aviamentos.pk]))
        self.assertContains(resp, 'value="padrao:M"')
        self.assertNotContains(resp, 'value="padrao:UN"')  # a empresa já tem UN
        self.assertContains(resp, 'Tipo de unidade')

    def test_escolhe_metro_pronto_e_a_unidade_e_criada(self):
        from apps.moda.models import Aviamento
        from apps.produtos.models import UnidadeMedida

        resp = self.client.post(self.url_novo, self._payload(
            nome='Elástico 30mm', tipo='elastico', unidade_medida='padrao:M',
        ))
        self.assertEqual(resp.status_code, 302)
        metro = UnidadeMedida.objects.get(empresa=self.empresa, sigla='M')
        self.assertEqual((metro.descricao, metro.tipo), ('Metro', 'comprimento'))
        avi = Aviamento.objects.get(filial=self.filial, nome='Elástico 30mm')
        self.assertEqual(avi.unidade, 'm')

        # Depois de criada, deixa de ser oferecida como "pronta".
        resp = self.client.get(reverse('estoque:deposito-update', args=[self.aviamentos.pk]))
        self.assertNotContains(resp, 'value="padrao:M"')

    def test_cria_tipo_novo_de_aviamento(self):
        from apps.moda.models import Aviamento

        resp = self.client.post(self.url_novo, self._payload(
            nome='Patch bandeirinha RN', tipo='__novo__', novo_tipo_nome='  Patch ',
        ))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('custom%3APatch', resp['Location'])
        avi = Aviamento.objects.get(filial=self.filial, nome='Patch bandeirinha RN')
        self.assertEqual(avi.tipo, 'aviamento')  # a produção o trata como "Outro aviamento"
        self.assertEqual(avi.tipo_personalizado, 'Patch')
        self.assertEqual(avi.tipo_rotulo, 'Patch')

        # O tipo criado passa a ser oferecido na lista, e reaproveitá-lo não duplica.
        resp = self.client.get(reverse('estoque:deposito-update', args=[self.aviamentos.pk]))
        self.assertContains(resp, 'value="custom:Patch"')
        self.client.post(self.url_novo, self._payload(
            nome='Patch bandeira BRA', tipo='custom:Patch',
        ))
        self.assertEqual(
            Aviamento.objects.filter(filial=self.filial, tipo_personalizado='Patch').count(), 2,
        )

    def test_tipo_novo_com_nome_de_tipo_existente_usa_o_da_lista(self):
        from apps.moda.models import Aviamento

        self.client.post(self.url_novo, self._payload(
            nome='Botão pérola', tipo='__novo__', novo_tipo_nome='botão',
        ))
        avi = Aviamento.objects.get(filial=self.filial, nome='Botão pérola')
        self.assertEqual((avi.tipo, avi.tipo_personalizado), ('botao', ''))

    def test_tipo_novo_sem_nome_e_recusado(self):
        from apps.moda.models import Aviamento

        resp = self.client.post(self.url_novo, self._payload(tipo='__novo__', novo_tipo_nome=''))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Informe o nome do tipo.')
        self.assertFalse(Aviamento.objects.filter(filial=self.filial).exists())
