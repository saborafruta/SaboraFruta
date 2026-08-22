"""
A aprovação do cliente pelo link público.

O cliente abria o link, clicava em APROVAR PEDIDO e recebia "a página ficou
aberta tempo demais" -- o texto da falha de CSRF -- tendo acabado de abrir a
página. O GET funcionava, então o link parecia bom até a hora de responder.

A causa era o cabeçalho `Referrer-Policy: no-referrer` da própria página.
Pela especificação do Fetch, requisição que não é GET nem HEAD, saindo de
uma página com essa política, manda `Origin: null`; o Django compara o
`Origin` com o host, não bate, e recusa. O teste `test_origin_null...`
reproduz exatamente esse envio.
"""
from django.test import Client, TestCase
from django.urls import reverse

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial
from apps.moda.models import AprovacaoPedido, PedidoProducao

# O cliente de teste fala HTTP: origem com https nao bateria com o host,
# e o Django recusaria por Origin -- exatamente o defeito que se testa.
ORIGEM = 'http://testserver'


class AprovacaoPublicaTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao Aprova LTDA', nome_fantasia='Aprova',
            cnpj='53345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Confeccao Aprova LTDA',
            cnpj='53345678000272', uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Diego Macedo', cpf_cnpj='12345678901',
        )

    def setUp(self):
        self.pedido = PedidoProducao.objects.create(
            filial=self.filial, cliente=self.cliente, numero=12,
        )
        self.aprovacao = AprovacaoPedido.objects.create(pedido=self.pedido)
        self.aprovacao.liberar(usuario=None)
        self.token = self.pedido.token_publico

    def _url_pagina(self):
        return reverse('moda_publico:pedido', args=[self.token])

    def _url_responder(self):
        return reverse('moda_publico:pedido-responder', args=[self.token])

    # ── O cabeçalho que quebrava tudo ────────────────────────────────────

    def test_pagina_nao_manda_no_referrer(self):
        """
        `no-referrer` faz o navegador mandar `Origin: null` no POST, e o
        Django recusa como CSRF. `same-origin` guarda o mesmo: o endereço
        com o token não vaza para site de fora.
        """
        resposta = self.client.get(self._url_pagina())

        self.assertEqual(resposta['Referrer-Policy'], 'same-origin')
        self.assertNotEqual(resposta['Referrer-Policy'], 'no-referrer')

    def test_o_resto_da_blindagem_continua(self):
        """A correção não pode ter afrouxado cache nem indexação."""
        resposta = self.client.get(self._url_pagina())

        self.assertEqual(resposta['Cache-Control'], 'private, no-store')
        self.assertIn('noindex', resposta['X-Robots-Tag'])

    # ── A aprovação em si ────────────────────────────────────────────────

    def test_cliente_aprova_pelo_link(self):
        cliente_http = Client(enforce_csrf_checks=True)
        cliente_http.get(self._url_pagina())  # pega o cookie de CSRF
        token_csrf = cliente_http.cookies['csrftoken'].value

        resposta = cliente_http.post(
            self._url_responder(),
            {'resposta': 'aprovado', 'nome': 'DIEGO MACEDO',
             'csrfmiddlewaretoken': token_csrf},
            HTTP_ORIGIN=ORIGEM,
        )

        self.assertEqual(resposta.status_code, 302)
        self.aprovacao.refresh_from_db()
        self.assertTrue(self.aprovacao.aprovado_pelo_cliente)
        self.assertEqual(self.aprovacao.respondido_por, 'DIEGO MACEDO')

    def test_cliente_pede_ajuste_pelo_link(self):
        cliente_http = Client(enforce_csrf_checks=True)
        cliente_http.get(self._url_pagina())
        token_csrf = cliente_http.cookies['csrftoken'].value

        cliente_http.post(
            self._url_responder(),
            {'resposta': 'ajuste', 'nome': 'DIEGO MACEDO',
             'motivo': 'Trocar a gola', 'csrfmiddlewaretoken': token_csrf},
            HTTP_ORIGIN=ORIGEM,
        )

        self.aprovacao.refresh_from_db()
        self.assertTrue(self.aprovacao.pediu_ajuste)
        self.assertEqual(self.aprovacao.motivo_ajuste, 'Trocar a gola')

    def test_origin_null_e_recusado_e_era_esse_o_defeito(self):
        """
        O envio que o navegador FAZIA com `no-referrer`, reproduzido.

        Continua sendo recusado -- e tem de continuar, porque `Origin: null`
        de verdade é requisição de origem opaca. O que se corrigiu foi o
        navegador parar de mandar assim, não o Django passar a aceitar.
        """
        cliente_http = Client(enforce_csrf_checks=True)
        cliente_http.get(self._url_pagina())
        token_csrf = cliente_http.cookies['csrftoken'].value

        resposta = cliente_http.post(
            self._url_responder(),
            {'resposta': 'aprovado', 'nome': 'DIEGO MACEDO',
             'csrfmiddlewaretoken': token_csrf},
            HTTP_ORIGIN='null',
        )

        self.assertEqual(resposta.status_code, 403)
        self.aprovacao.refresh_from_db()
        self.assertFalse(self.aprovacao.aprovado_pelo_cliente)

    # ── O raio da escrita pública ────────────────────────────────────────

    def test_sem_nome_nao_grava_aceite_anonimo(self):
        """O nome é a assinatura: aceite anônimo não serve a ninguém."""
        cliente_http = Client(enforce_csrf_checks=True)
        cliente_http.get(self._url_pagina())
        token_csrf = cliente_http.cookies['csrftoken'].value

        cliente_http.post(
            self._url_responder(),
            {'resposta': 'aprovado', 'nome': '  ', 'csrfmiddlewaretoken': token_csrf},
            HTTP_ORIGIN=ORIGEM,
        )

        self.aprovacao.refresh_from_db()
        self.assertFalse(self.aprovacao.aprovado_pelo_cliente)

    def test_pedido_nao_liberado_nao_aceita_resposta(self):
        """
        Até a casa liberar, não há o que o cliente responder.

        Sem `enforce_csrf_checks` de propósito: o que se testa aqui é a
        regra de liberação, e com a checagem ligada a resposta seria 403
        antes de a view sequer rodar -- passaria pelo motivo errado.
        """
        self.aprovacao.liberado_em = None
        self.aprovacao.save(update_fields=['liberado_em'])

        resposta = self.client.post(
            self._url_responder(),
            {'resposta': 'aprovado', 'nome': 'DIEGO MACEDO'},
        )

        self.assertEqual(resposta.status_code, 404)
