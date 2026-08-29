"""
A tela de condições de pagamento.

ELAS SÓ EXISTIAM NO ADMIN DO DJANGO — quer dizer, para quem tem acesso de
superusuário e sabe que aquilo existe. Na prática, quem monta uma venda a
prazo escolhia entre as condições que alguém criou uma vez, e não tinha como
criar a que faltava. Sem condição, toda venda a prazo sai em uma parcela só.

O QUE ESTES TESTES CERCAM:

  · CRIAR E EDITAR pela tela, com a condição valendo para a empresa inteira;

  · INATIVAR, E NÃO EXCLUIR: a condição está escrita nos títulos que ela
    gerou, e apagá-la deixaria pedido antigo sem saber em quantas vezes foi
    cobrado;

  · ZERO PARCELA NÃO É CONDIÇÃO: o valor teria de sumir em algum lugar, e
    some no pior — na cobrança que nunca sai;

  · A CONDIÇÃO DE OUTRA EMPRESA NÃO APARECE nem se edita.
"""
from django.test import TestCase
from django.urls import reverse

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.financeiro.models.formas_pagamento import CondicaoPagamento


class CondicoesPagamentoTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Parcelas LTDA', nome_fantasia='Parcelas',
            cnpj='71345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='71345678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='parcelas@erp.local', nome='Parcelas', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.outra_empresa = Empresa.objects.create(
            razao_social='Alheia LTDA', nome_fantasia='Alheia',
            cnpj='81945678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        self.url = reverse('financeiro:condicoes_pagamento')

    # ── Criar e editar ───────────────────────────────────────────────────

    def test_criar_condicao_pela_tela(self):
        self.client.post(self.url, {
            'descricao': '30/60/90',
            'numero_parcelas': '3',
            'intervalo_dias': '30',
            'dias_primeira_parcela': '30',
            'desconto_avista': '0',
            'acrescimo': '0',
            'ativo': 'on',
        }, follow=True)

        condicao = CondicaoPagamento.objects.get(empresa=self.empresa)
        self.assertEqual(condicao.descricao, '30/60/90')
        self.assertEqual(condicao.numero_parcelas, 3)
        self.assertEqual(condicao.intervalo_dias, 30)
        self.assertEqual(condicao.dias_primeira_parcela, 30)

    def test_editar_condicao(self):
        condicao = CondicaoPagamento.objects.create(
            empresa=self.empresa, descricao='À vista', numero_parcelas=1,
        )

        self.client.post(self.url, {
            'id': condicao.pk,
            'descricao': 'Sete dias',
            'numero_parcelas': '1',
            'intervalo_dias': '0',
            'dias_primeira_parcela': '7',
            'desconto_avista': '0',
            'acrescimo': '0',
            'ativo': 'on',
        }, follow=True)

        condicao.refresh_from_db()
        self.assertEqual(condicao.descricao, 'Sete dias')
        self.assertEqual(condicao.dias_primeira_parcela, 7)

    def test_zero_parcela_nao_e_condicao(self):
        """O valor teria de sumir em algum lugar — e some na cobrança que
        nunca sai."""
        resposta = self.client.post(self.url, {
            'descricao': 'Nenhuma',
            'numero_parcelas': '0',
            'intervalo_dias': '30',
            'dias_primeira_parcela': '0',
            'desconto_avista': '0',
            'acrescimo': '0',
        })

        self.assertEqual(CondicaoPagamento.objects.count(), 0)
        self.assertIn(
            'ao menos uma parcela',
            resposta.context['form'].errors['numero_parcelas'][0],
        )

    # ── Inativar ─────────────────────────────────────────────────────────

    def test_inativar_em_vez_de_excluir(self):
        """
        A condição está escrita nos títulos que ela gerou: apagá-la deixaria
        pedido antigo sem saber em quantas vezes foi cobrado.
        """
        condicao = CondicaoPagamento.objects.create(
            empresa=self.empresa, descricao='3x', numero_parcelas=3,
        )

        self.client.post(
            self.url, {'acao': 'inativar', 'id': condicao.pk}, follow=True,
        )

        condicao.refresh_from_db()
        self.assertFalse(condicao.ativo)
        self.assertEqual(CondicaoPagamento.objects.count(), 1)

    def test_reativar(self):
        condicao = CondicaoPagamento.objects.create(
            empresa=self.empresa, descricao='3x', numero_parcelas=3, ativo=False,
        )

        self.client.post(
            self.url, {'acao': 'inativar', 'id': condicao.pk}, follow=True,
        )

        condicao.refresh_from_db()
        self.assertTrue(condicao.ativo)

    # ── Cada empresa com as suas ─────────────────────────────────────────

    def test_condicao_de_outra_empresa_nao_aparece(self):
        CondicaoPagamento.objects.create(
            empresa=self.outra_empresa, descricao='Alheia 10x',
            numero_parcelas=10,
        )

        html = self.client.get(self.url).content.decode()

        self.assertNotIn('Alheia 10x', html)

    def test_condicao_de_outra_empresa_nao_se_edita(self):
        alheia = CondicaoPagamento.objects.create(
            empresa=self.outra_empresa, descricao='Alheia', numero_parcelas=2,
        )

        resposta = self.client.get(self.url, {'editar': alheia.pk})

        self.assertEqual(resposta.status_code, 404)

    # ── A tela ───────────────────────────────────────────────────────────

    def test_a_tela_lista_as_condicoes_com_os_vencimentos_por_extenso(self):
        CondicaoPagamento.objects.create(
            empresa=self.empresa, descricao='30/60/90', numero_parcelas=3,
            intervalo_dias=30, dias_primeira_parcela=30,
        )

        html = self.client.get(self.url).content.decode()

        self.assertIn('30/60/90', html)
        self.assertIn('primeira em 30 dia(s)', html)
        self.assertIn('demais a cada 30 dia(s)', html)

    def test_sem_condicoes_a_tela_diz_o_que_acontece(self):
        html = self.client.get(self.url).content.decode()

        self.assertIn('toda venda a prazo sai', html)
