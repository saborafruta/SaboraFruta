"""
Conferência pessoa a pessoa e o aceite de entrega do cliente.

Dois riscos concretos, e é neles que os testes se concentram:

1. A conferência por tamanho FECHAR e a peça de alguém ter ficado para
   trás. É o motivo de a conferência por pessoa existir, e o teste
   `test_por_tamanho_fecha_e_uma_pessoa_falta` mostra o caso.

2. O aceite público aceitar entrega que ainda não saiu, ou anônima. A
   página é sem login: o que ela grava tem de ser exatamente quem recebeu
   e quando, e nada mais.
"""
from django.test import Client, TestCase
from django.urls import reverse

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial
from apps.core.services.exceptions import DomainError
from apps.moda.models import (
    ConferenciaPessoa, Expedicao, ItemPedidoProducao, OrdemProducao,
    PedidoProducao, PersonalizacaoIndividual, Tamanho,
)

ORIGEM = 'http://testserver'


class ConferenciaEntregaTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao Entrega LTDA', nome_fantasia='Entrega',
            cnpj='53345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Confeccao Entrega LTDA',
            cnpj='53345678000272', uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Time do Bairro', cpf_cnpj='12345678901',
        )
        cls.m = Tamanho.objects.create(filial=cls.filial, sigla='M', ordem=30)
        cls.g = Tamanho.objects.create(filial=cls.filial, sigla='G', ordem=40)

    def setUp(self):
        self.pedido = PedidoProducao.objects.create(
            filial=self.filial, cliente=self.cliente, numero=11,
            status=PedidoProducao.Status.PRONTO,
        )
        self.item = ItemPedidoProducao.objects.create(
            pedido=self.pedido, descricao='Camisa de jogo', quantidade=3,
        )
        self.ordem = OrdemProducao.objects.create(
            filial=self.filial, pedido=self.pedido, item=self.item,
            numero='OP-0001', ano=2026, sequencial=1, quantidade=3,
        )
        self.expedicao = Expedicao.objects.create(
            filial=self.filial, ordem=self.ordem,
        )
        self.pessoas = [
            PersonalizacaoIndividual.objects.create(
                pedido=self.pedido, item=self.item, tamanho=t,
                nome=nome, numero=num, ordem=i * 10,
            )
            for i, (nome, num, t) in enumerate([
                ('Joao Silva', '10', self.m),
                ('Pedro Lima', '7', self.g),
                ('Lucas Souza', '21', self.m),
            ])
        ]

    def _marcar(self, *pessoas):
        for p in pessoas:
            ConferenciaPessoa.objects.create(expedicao=self.expedicao, individual=p)

    # ── Por que a conferência por pessoa existe ──────────────────────────

    def test_por_tamanho_fecha_e_uma_pessoa_falta(self):
        """
        O caso que motiva a tela: a contagem por tamanho pode fechar e a
        camisa de alguém ter ficado para trás, porque peça com nome não é
        intercambiável com nenhuma outra.
        """
        self._marcar(self.pessoas[0], self.pessoas[1])

        conferidas = self.expedicao.conferencia_pessoas.count()

        self.assertEqual(conferidas, 2)
        self.assertEqual(len(self.pessoas), 3)
        faltando = set(self.pessoas) - {
            c.individual for c in self.expedicao.conferencia_pessoas.all()
        }
        self.assertEqual({p.nome for p in faltando}, {'Lucas Souza'})

    def test_a_linha_existir_e_a_conferencia(self):
        """Desmarcar apaga: não fica registro de conferência desfeita."""
        self._marcar(self.pessoas[0])
        self.expedicao.conferencia_pessoas.all().delete()

        self.assertEqual(self.expedicao.conferencia_pessoas.count(), 0)

    def test_nao_da_para_conferir_a_mesma_pessoa_duas_vezes(self):
        from django.db import IntegrityError

        self._marcar(self.pessoas[0])
        with self.assertRaises(IntegrityError):
            ConferenciaPessoa.objects.create(
                expedicao=self.expedicao, individual=self.pessoas[0],
            )

    # ── Aceite público de entrega ────────────────────────────────────────

    def _url_entrega(self):
        return reverse('moda_publico:entrega', args=[self.expedicao.codigo])

    def test_pagina_de_entrega_abre_pelo_codigo(self):
        resposta = self.client.get(self._url_entrega())

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta['Referrer-Policy'], 'same-origin')
        self.assertEqual(resposta['Cache-Control'], 'private, no-store')

    def test_codigo_inexistente_da_404(self):
        url = reverse('moda_publico:entrega', args=['naoexisteisso123'])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_nao_aceita_entrega_que_ainda_nao_saiu(self):
        """
        A expedição está em Produção Concluída. Confirmar recebimento aqui
        registraria entrega de caixa que não saiu da fábrica.
        """
        cliente_http = Client(enforce_csrf_checks=True)
        cliente_http.get(self._url_entrega())
        token = cliente_http.cookies['csrftoken'].value

        cliente_http.post(
            self._url_entrega(),
            {'recebido_por': 'Fulano', 'csrfmiddlewaretoken': token},
            HTTP_ORIGIN=ORIGEM,
        )

        self.expedicao.refresh_from_db()
        self.assertFalse(self.expedicao.entregue)
        self.assertEqual(self.expedicao.recebido_por, '')

    def test_sem_nome_nao_grava_entrega_anonima(self):
        """O nome é a prova da entrega — sem ele não há comprovante."""
        self.expedicao.status = Expedicao.Status.DESPACHO
        self.expedicao.save(update_fields=['status'])

        cliente_http = Client(enforce_csrf_checks=True)
        cliente_http.get(self._url_entrega())
        token = cliente_http.cookies['csrftoken'].value

        cliente_http.post(
            self._url_entrega(),
            {'recebido_por': '   ', 'csrfmiddlewaretoken': token},
            HTTP_ORIGIN=ORIGEM,
        )

        self.expedicao.refresh_from_db()
        self.assertFalse(self.expedicao.entregue)

    def test_cliente_confirma_recebimento(self):
        self.expedicao.status = Expedicao.Status.DESPACHO
        self.expedicao.save(update_fields=['status'])

        cliente_http = Client(enforce_csrf_checks=True)
        cliente_http.get(self._url_entrega())
        token = cliente_http.cookies['csrftoken'].value

        resposta = cliente_http.post(
            self._url_entrega(),
            {'recebido_por': 'Diego Macedo', 'csrfmiddlewaretoken': token},
            HTTP_ORIGIN=ORIGEM,
        )

        self.assertEqual(resposta.status_code, 302)
        self.expedicao.refresh_from_db()
        self.assertTrue(self.expedicao.entregue)
        self.assertEqual(self.expedicao.recebido_por, 'Diego Macedo')
        self.assertIsNotNone(self.expedicao.data_entrega)

    def test_expedicao_cancelada_nao_tem_pagina_de_entrega(self):
        self.expedicao.status = Expedicao.Status.CANCELADA
        self.expedicao.save(update_fields=['status'])

        self.assertEqual(self.client.get(self._url_entrega()).status_code, 404)

    # ── O link copiável ──────────────────────────────────────────────────

    def test_a_tela_da_expedicao_oferece_o_link_de_entrega(self):
        """
        O endereço vai por WhatsApp: sem um campo de onde copiar, sobraria
        digitar um token à mão, que é onde o erro acontece.
        """
        from django.test import RequestFactory

        from apps.moda.views_expedicao import ExpedicaoDetailView

        pedido = RequestFactory().get('/x/')
        pedido.filial_ativa = self.filial
        # A view pergunta a permissão ao usuário; aqui interessa o contexto,
        # não o perfil, então um dublê que responde "pode" basta.
        pedido.user = type('Dubl', (), {'tem_permissao': lambda self, *a: True})()
        contexto = {}

        import apps.moda.views_expedicao as modulo
        from django.http import HttpResponse

        original = modulo.render
        modulo.render = lambda r, t, ctx: (contexto.update(ctx), HttpResponse(''))[1]
        try:
            ExpedicaoDetailView().get(pedido, self.expedicao.pk)
        finally:
            modulo.render = original

        self.assertIn('link_entrega', contexto)
        self.assertIn(self.expedicao.codigo, contexto['link_entrega'])
        self.assertIn('/pedido/entrega/', contexto['link_entrega'])

    # ── Rotas ────────────────────────────────────────────────────────────

    def test_as_rotas_existem_e_apontam_para_as_views_certas(self):
        from django.urls import resolve

        from apps.moda import views_conferencia as vc

        pares = [
            (reverse('moda:conferencia-pessoas', args=[1]), vc.ConferenciaPessoasView),
            (reverse('moda:conferencia-pessoas-salvar', args=[1]), vc.ConferenciaPessoasSalvarView),
            (reverse('moda:conferencia-qr', args=[1]), vc.ConferenciaQrView),
            (reverse('moda:pedido-conferencia', args=[1]), vc.PedidoConferenciaView),
        ]
        for url, esperada in pares:
            self.assertIs(resolve(url).func.view_class, esperada)
