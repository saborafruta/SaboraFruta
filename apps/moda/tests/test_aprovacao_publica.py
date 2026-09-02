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
from decimal import Decimal

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

    def test_link_do_orcamento_mostra_valores_pagamento_e_observacoes_do_pdf(self):
        from apps.moda.models import ItemPedidoProducao

        ItemPedidoProducao.objects.create(
            pedido=self.pedido, descricao='Camisa de jogo', quantidade=3,
            valor_unitario=Decimal('100.00'),
        )
        self.pedido.desconto = Decimal('20.00')
        self.pedido.frete = Decimal('15.00')
        self.pedido.previsao_pagamento = [
            {'forma': 'pix', 'valor': '150.00'},
            {'forma': 'credito_parcelado', 'valor': '145.00'},
        ]
        self.pedido.observacoes = 'Conferir a arte antes da produção.'
        self.pedido.save(update_fields=[
            'desconto', 'frete', 'previsao_pagamento', 'observacoes',
        ])

        resposta = self.client.get(self._url_pagina())

        self.assertContains(resposta, 'Valor unitário')
        self.assertContains(resposta, 'Subtotal do produto')
        self.assertContains(resposta, 'R$ 100,00')
        self.assertContains(resposta, 'R$ 300,00')
        self.assertContains(resposta, 'R$ 295,00')
        self.assertContains(resposta, 'Forma de pagamento prevista')
        self.assertContains(resposta, 'PIX')
        self.assertContains(resposta, 'Crédito parcelado')
        self.assertContains(resposta, 'Conferir a arte antes da produção.')
        self.assertContains(resposta, 'Este orçamento é válido por 5 dias')
        self.assertContains(resposta, 'O pagamento de 50% do valor total')

    def test_link_repete_hierarquia_do_pdf_e_identifica_quem_aprova(self):
        from datetime import date

        from apps.moda.models import ItemPedidoProducao

        ItemPedidoProducao.objects.create(
            pedido=self.pedido, descricao='Camisa de jogo', quantidade=3,
            valor_unitario=Decimal('100.00'),
        )
        self.pedido.data_prevista_entrega = date(2026, 9, 20)
        self.pedido.save(update_fields=['data_prevista_entrega'])

        resposta = self.client.get(self._url_pagina())

        self.assertContains(resposta, 'logo_erk_preta.png')
        self.assertContains(resposta, 'ORÇAMENTO')
        self.assertContains(resposta, 'Produto / grade e personalizações')
        self.assertContains(resposta, 'Fechamento do orçamento')
        self.assertContains(resposta, 'Previsão de entrega')
        self.assertContains(resposta, '20/09/2026')
        self.assertContains(resposta, 'Nome de quem está aprovando')
        self.assertContains(resposta, 'id="nome-aprovador"')
        self.assertNotContains(resposta, 'Situação do pedido')
        self.assertNotContains(resposta, 'Arte do pedido')

    def test_link_do_orcamento_mostra_clientes_estrutura_cor_e_observacao_individual(self):
        from apps.moda.models import (
            ItemPedidoProducao, PersonalizacaoIndividual, Tamanho,
        )

        cliente_adicional = Cliente.objects.create(
            filial=self.filial, razao_social='Maria Parceira',
            cpf_cnpj='98765432100',
        )
        self.pedido.clientes_adicionais.add(cliente_adicional)
        item = ItemPedidoProducao.objects.create(
            pedido=self.pedido, descricao='Camisa especial', quantidade=1,
            observacoes=(
                'Estrutura da peça:\nTipo de peça: Camisa\n'
                'Cor: Bordô personalizado\nMalha: DRYTECH'
            ),
        )
        tamanho = Tamanho.objects.create(
            filial=self.filial, sigla='M', ordem=30,
        )
        PersonalizacaoIndividual.objects.create(
            pedido=self.pedido, item=item, tamanho=tamanho,
            nome='Ana', numero='8', observacoes='Nome com acento no peito',
        )

        resposta = self.client.get(self._url_pagina())

        self.assertContains(resposta, 'Clientes')
        self.assertContains(resposta, 'Diego Macedo')
        self.assertContains(resposta, 'Maria Parceira')
        self.assertContains(resposta, '98765432100')
        self.assertNotContains(resposta, 'Estrutura e especificações')
        self.assertNotContains(resposta, 'Bordô personalizado')
        self.assertNotContains(resposta, 'DRYTECH')
        self.assertContains(resposta, 'Nomes e números')
        self.assertContains(resposta, 'Ana')
        self.assertContains(resposta, 'Nome com acento no peito')

    def test_contatos_extras_ficam_na_mesma_linha_no_orcamento(self):
        from apps.moda.services.orcamento_pdf import observacoes_orcamento

        self.pedido.observacoes = (
            'Conferir nomes.\n\nContatos extras:\n'
            '- Diego Allyson: 456456456456\n- Maria: 84999990000'
        )

        observacoes = observacoes_orcamento(self.pedido)

        self.assertIn(
            'Contatos extras: Diego Allyson: 456456456456 | Maria: 84999990000',
            observacoes,
        )
        self.assertNotIn('Contatos extras:', observacoes)

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


class NomesENumerosNoLinkTests(AprovacaoPublicaTests):
    """
    A lista de nome e número na página do cliente.

    Ele aprovava a grade e a arte sem nunca ver os nomes -- e nome errado
    não é ajuste de tela, é peça refeita: bordado não se apaga. Só o cliente
    sabe se o "João" do time é com ou sem H, e se o 10 é do Pedro ou do
    Lucas. Se esta seção sumir, o erro caro volta a passar despercebido.
    """

    def _com_pessoas(self):
        from apps.moda.models import (
            ItemPedidoProducao, PersonalizacaoIndividual, Tamanho,
        )

        item = ItemPedidoProducao.objects.create(
            pedido=self.pedido, descricao='Camisa de jogo', quantidade=2,
        )
        m = Tamanho.objects.create(filial=self.filial, sigla='M', ordem=30)
        g = Tamanho.objects.create(filial=self.filial, sigla='G', ordem=40)
        PersonalizacaoIndividual.objects.create(
            pedido=self.pedido, item=item, tamanho=m,
            nome='Joao Silva', numero='10', ordem=1,
        )
        PersonalizacaoIndividual.objects.create(
            pedido=self.pedido, item=item, tamanho=g,
            nome='Pedro Lima', numero='7', ordem=2,
        )
        return item

    def test_a_lista_de_nomes_aparece_no_link(self):
        self._com_pessoas()

        resposta = self.client.get(self._url_pagina())

        self.assertContains(resposta, 'Nomes e números')
        self.assertContains(resposta, 'Joao Silva')
        self.assertContains(resposta, 'Pedro Lima')

    def test_o_numero_e_o_tamanho_vao_junto(self):
        """
        Nome sem número não dá para conferir: na quadra é pelo número que se
        procura a camisa, e é nele que um dígito trocado passa despercebido.
        """
        self._com_pessoas()

        resposta = self.client.get(self._url_pagina())

        import re

        pagina = resposta.content.decode()
        # O número fica sozinho no seu proprio elemento — e' o destaque da
        # linha. Espaco em volta e' formatacao do template, nao conteudo.
        numeros = {n.strip() for n in re.findall(r'>\s*(\d{1,3})\s*<', pagina)}
        self.assertIn('10', numeros)
        self.assertIn('7', numeros)
        self.assertContains(resposta, 'Tamanho M')
        self.assertContains(resposta, 'Tamanho G')

    def test_pedido_sem_personalizacao_nao_ganha_secao_vazia(self):
        """
        Camisa lisa não tem nome nenhum. Um bloco vazio faria o cliente
        procurar o que não existe.
        """
        from apps.moda.models import ItemPedidoProducao

        ItemPedidoProducao.objects.create(
            pedido=self.pedido, descricao='Camisa lisa', quantidade=5,
        )

        resposta = self.client.get(self._url_pagina())

        self.assertNotContains(resposta, 'Nomes e números')

    def test_o_aviso_do_bordado_so_aparece_quando_ha_nomes(self):
        self._com_pessoas()

        resposta = self.client.get(self._url_pagina())

        self.assertContains(resposta, 'nome errado é peça refeita')
