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

    def test_a_lista_mostra_o_cronograma_em_dias(self):
        """
        "30 · 60 · 90" é o que a pessoa reconhece de imediato. Três campos
        soltos obrigam a somar de cabeça, linha a linha — e ninguém confere
        uma tabela assim.
        """
        CondicaoPagamento.objects.create(
            empresa=self.empresa, descricao='30/60/90', numero_parcelas=3,
            intervalo_dias=30, dias_primeira_parcela=30,
        )

        resposta = self.client.get(self.url)
        condicao = resposta.context['condicoes'][0]

        self.assertEqual(condicao.cronograma, [30, 60, 90])
        self.assertEqual(condicao.cronograma_resto, 0)
        self.assertIn('30/60/90', resposta.content.decode())

    def test_o_cronograma_longo_e_resumido(self):
        """Doze chips numa linha viram ruído; os seis primeiros e o resto."""
        CondicaoPagamento.objects.create(
            empresa=self.empresa, descricao='12x', numero_parcelas=12,
            intervalo_dias=30, dias_primeira_parcela=30,
        )

        condicao = self.client.get(self.url).context['condicoes'][0]

        self.assertEqual(len(condicao.cronograma), 6)
        self.assertEqual(condicao.cronograma_resto, 6)

    def test_sem_condicoes_a_tela_diz_o_que_acontece(self):
        html = self.client.get(self.url).content.decode()

        self.assertIn('toda venda a prazo sai', html)


class PreviaDoParcelamentoTests(CondicoesPagamentoTests):
    """
    A prévia: o que a condição produz, com datas e valores.

    TRÊS NÚMEROS NÃO SE IMAGINAM. É escrevendo as datas que se percebe que o
    parcelamento dobrou o prazo que o cliente pediu — ou que a parcela cai num
    sábado.
    """

    def setUp(self):
        super().setUp()
        self.previa_url = reverse('financeiro:condicoes_pagamento_previa')

    def _previa(self, **params):
        return self.client.get(self.previa_url, params).json()['parcelas']

    def test_a_previa_traz_vencimento_e_valor_de_cada_parcela(self):
        parcelas = self._previa(
            numero_parcelas=3, intervalo_dias=30,
            dias_primeira_parcela=30, valor='1000',
        )

        self.assertEqual(len(parcelas), 3)
        self.assertEqual(parcelas[0]['valor'], '333,34')
        self.assertEqual(parcelas[1]['valor'], '333,33')
        self.assertEqual(parcelas[2]['valor'], '333,33')

    def test_a_conta_e_a_do_sistema(self):
        """
        A prévia chama o mesmo serviço que vai gerar os títulos: uma
        aritmética parecida mostraria uma coisa na tela e cobraria outra.
        """
        from datetime import timedelta

        from django.utils import timezone

        hoje = timezone.localdate()
        parcelas = self._previa(
            numero_parcelas=2, intervalo_dias=15,
            dias_primeira_parcela=7, valor='100',
        )

        self.assertEqual(
            parcelas[0]['vencimento'], (hoje + timedelta(days=7)).strftime('%d/%m/%Y'),
        )
        self.assertEqual(
            parcelas[1]['vencimento'], (hoje + timedelta(days=22)).strftime('%d/%m/%Y'),
        )

    def test_a_previa_marca_o_fim_de_semana(self):
        """
        Vencimento em sábado ou domingo atrasa a compensação — melhor
        descobrir no cadastro do que no extrato.
        """
        from datetime import timedelta

        from django.utils import timezone

        hoje = timezone.localdate()
        # Quantos dias faltam para o proximo sabado.
        ate_sabado = (5 - hoje.weekday()) % 7 or 7

        parcelas = self._previa(
            numero_parcelas=1, dias_primeira_parcela=ate_sabado, valor='100',
        )

        self.assertTrue(parcelas[0]['fim_de_semana'])
        self.assertEqual(parcelas[0]['dia_semana'], 'sáb')

    def test_valor_com_virgula_e_aceito(self):
        parcelas = self._previa(numero_parcelas=1, valor='1.234,56')

        self.assertEqual(parcelas[0]['valor'], '1234,56')

    def test_parametro_invalido_nao_derruba_a_previa(self):
        """
        A prévia ajuda a conferir; ela não pode ser o motivo de a tela quebrar
        enquanto alguém apaga um campo para digitar outro número.
        """
        resposta = self.client.get(
            self.previa_url,
            {'numero_parcelas': '', 'intervalo_dias': 'abc', 'valor': ''},
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(len(resposta.json()['parcelas']), 1)


class TelaComPreviaTests(CondicoesPagamentoTests):
    """O formulário mostra o resultado e oferece os atalhos."""

    def test_o_formulario_traz_a_previa_e_os_atalhos(self):
        html = self.client.get(self.url, {'novo': '1'}).content.decode()

        self.assertIn('Como fica', html)
        self.assertIn('condicaoParcelamento', html)
        for atalho in ('à vista', '30 dias', '30/60/90'):
            self.assertIn(atalho, html)

    def test_a_tela_explica_a_diferenca_entre_forma_e_condicao(self):
        html = self.client.get(self.url).content.decode()

        self.assertIn('diz se a venda é a prazo', html)
