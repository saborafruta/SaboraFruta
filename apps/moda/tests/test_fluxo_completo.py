"""
O FLUXO INTEIRO, do pedido pronto até o cliente assinar o recebimento.

Cada tela foi testada isolada. Este arquivo testa a COSTURA entre elas, que
é onde um sistema montado por partes costuma falhar: cada peça passa no seu
teste e a corrente arrebenta na emenda.

O caminho percorrido é o real, pelos serviços que a interface chama:

    pedido pronto
      → emitir a ordem de produção (as onze validações)
      → encerrar as etapas do fluxo, inclusive qualidade
      → abrir a expedição
      → conferir por tamanho E pessoa a pessoa
      → separação → embalagem, com volume
      → despacho
      → o CLIENTE confirma o recebimento pela página pública

Nenhum atalho: onde a interface chama um serviço, o teste chama o mesmo
serviço; onde o cliente usa o navegador, o teste usa o cliente HTTP com
verificação de CSRF ligada.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial
from apps.financeiro.models import CondicaoPagamento, FormaPagamento
from apps.financeiro.constants.enums import TipoFormaPagamento
from apps.moda.models import (
    AprovacaoPedido, ConferenciaPessoa, Expedicao, FichaTecnica, ItemGradePedido,
    ItemPedidoProducao, MaterialFicha, Operacao, OperacaoRoteiro, OrdemProducao,
    PedidoProducao, PersonalizacaoIndividual, ProdutoModa, Roteiro, Tamanho,
    Volume,
)
from apps.moda.services import ExpedicaoService, OrdemProducaoService
from apps.moda.services.validacao import ValidacaoProducao

ORIGEM = 'http://testserver'


class FluxoCompletoTests(TestCase):
    """Do pedido pronto ao aceite do cliente, sem pular etapa."""

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao Fluxo LTDA', nome_fantasia='Fluxo',
            cnpj='53345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Confeccao Fluxo LTDA',
            cnpj='53345678000272', uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Time do Bairro',
            cpf_cnpj='09944149438', ativo=True,
        )
        cls.forma = FormaPagamento.objects.create(
            empresa=cls.empresa, filial=cls.filial, descricao='PIX',
            tipo=TipoFormaPagamento.PIX,
        )
        cls.condicao = CondicaoPagamento.objects.create(
            empresa=cls.empresa, descricao='A vista', numero_parcelas=1,
        )
        cls.m = Tamanho.objects.create(filial=cls.filial, sigla='M', ordem=30)
        cls.g = Tamanho.objects.create(filial=cls.filial, sigla='G', ordem=40)

    def setUp(self):
        self.produto = self._produto_completo()
        self.pedido = self._pedido_pronto()

    # ── Montagem ─────────────────────────────────────────────────────────

    def _produto_completo(self):
        """
        Produto com FICHA e ROTEIRO — as duas coisas que a validação exige
        antes de deixar a peça entrar na fábrica.
        """
        produto = ProdutoModa.objects.create(
            filial=self.filial, codigo='CAM001', nome='Camisa de jogo',
        )
        ficha = FichaTecnica.objects.create(filial=self.filial, produto=produto)
        MaterialFicha.objects.create(
            ficha=ficha, tipo=MaterialFicha.Tipo.TECIDO_PRINCIPAL,
            descricao='Malha Dry', unidade=MaterialFicha.Unidade.METRO,
            consumo=Decimal('1.2'), custo_unitario=Decimal('18.00'),
        )
        roteiro = Roteiro.objects.create(filial=self.filial, produto=produto)
        operacao = Operacao.objects.create(filial=self.filial, nome='Costura')
        OperacaoRoteiro.objects.create(
            roteiro=roteiro, operacao=operacao, sequencia=10,
            tempo_padrao=Decimal('5'), custo=Decimal('2.00'),
        )
        return produto

    def _pedido_pronto(self):
        """Um pedido que passa nas onze — e o teste confere isso."""
        pedido = PedidoProducao.objects.create(
            filial=self.filial, cliente=self.cliente, numero=11,
            status=PedidoProducao.Status.PRONTO,
            data_prevista_entrega=timezone.localdate() + timedelta(days=10),
            forma_pagamento=self.forma, condicao_pagamento=self.condicao,
        )
        item = ItemPedidoProducao.objects.create(
            pedido=pedido, produto=self.produto, quantidade=3,
            valor_unitario=Decimal('80.00'),
        )
        ItemGradePedido.objects.create(item=item, tamanho=self.m, quantidade=2)
        ItemGradePedido.objects.create(item=item, tamanho=self.g, quantidade=1)

        # As três pessoas do time: é o que a conferência peça a peça lê.
        for i, (nome, numero, tamanho) in enumerate([
            ('Joao Silva', '10', self.m),
            ('Pedro Lima', '7', self.g),
            ('Lucas Souza', '21', self.m),
        ]):
            PersonalizacaoIndividual.objects.create(
                pedido=pedido, item=item, tamanho=tamanho,
                nome=nome, numero=numero, ordem=i * 10,
            )

        aprovacao = AprovacaoPedido.objects.create(pedido=pedido)
        aprovacao.liberar(usuario=None)
        aprovacao.responder(resposta=AprovacaoPedido.Resposta.APROVADO,
                            nome='Diego Macedo')
        return pedido

    # ── O fluxo, passo a passo ───────────────────────────────────────────

    def test_1_o_pedido_montado_passa_nas_onze_validacoes(self):
        """
        Se esta falhar, as outras falham em cascata — e o motivo estaria
        na montagem, não no fluxo. Por isso ela vem primeiro.
        """
        bloqueios = [c for c in ValidacaoProducao.checar(self.pedido) if c.bloqueia]

        self.assertEqual(
            [c.titulo for c in bloqueios], [],
            'o pedido de teste deveria estar liberado para produzir',
        )

    def test_2_emite_a_ordem_de_producao(self):
        ordens = OrdemProducaoService.gerar_do_pedido(self.pedido, usuario=None)

        self.assertEqual(len(ordens), 1)
        self.assertEqual(ordens[0].quantidade, 3)
        self.assertEqual(ordens[0].pedido, self.pedido)

    def test_3_expedicao_so_abre_depois_da_qualidade(self):
        """
        A trava que impede documento de expedição para peça que ainda está
        na costura.
        """
        from apps.core.services.exceptions import DomainError

        ordem = OrdemProducaoService.gerar_do_pedido(self.pedido, usuario=None)[0]

        if ordem.etapas.filter(etapa='qualidade').exists():
            with self.assertRaises(DomainError):
                ExpedicaoService.criar(self.filial, ordem)

        self._encerrar_qualidade(ordem)
        expedicao = ExpedicaoService.criar(self.filial, ordem)

        self.assertEqual(expedicao.ordem, ordem)
        self.assertEqual(expedicao.status, Expedicao.Status.PRODUCAO_CONCLUIDA)

    def test_4_fluxo_inteiro_ate_o_cliente_assinar(self):
        """
        O caminho completo, numa tacada — é a emenda entre as telas que
        este teste protege.
        """
        # 1. Ordem de produção
        ordem = OrdemProducaoService.gerar_do_pedido(self.pedido, usuario=None)[0]
        self._encerrar_qualidade(ordem)

        # 2. Expedição aberta
        expedicao = ExpedicaoService.criar(self.filial, ordem)

        # 3. Conferência por tamanho — bate com a grade do pedido
        ExpedicaoService.conferir(expedicao, {self.m.pk: 2, self.g.pk: 1}, {})
        expedicao.refresh_from_db()
        self.assertTrue(
            expedicao.conferencia_fecha,
            'a conferência por tamanho deveria fechar contra a ordem',
        )

        # 4. Conferência pessoa a pessoa — as três do time
        for pessoa in PersonalizacaoIndividual.objects.filter(pedido=self.pedido):
            ConferenciaPessoa.objects.create(
                expedicao=expedicao, individual=pessoa, conferido_por='Conferente',
            )
        self.assertEqual(expedicao.conferencia_pessoas.count(), 3)

        # 5. Separação e embalagem, com o volume fechando
        ExpedicaoService.avancar(expedicao, None, {})
        self.assertEqual(expedicao.status, Expedicao.Status.SEPARACAO)

        ExpedicaoService.avancar(expedicao, None, {})
        self.assertEqual(expedicao.status, Expedicao.Status.EMBALAGEM)

        Volume.objects.create(
            expedicao=expedicao, quantidade=3, peso_kg=Decimal('1.200'),
        )
        expedicao.refresh_from_db()
        self.assertTrue(
            expedicao.volumes_fecham,
            'toda peça conferida deveria estar dentro de um volume',
        )

        # 6. Despacho
        ExpedicaoService.avancar(
            expedicao, None, {'transportadora': 'Correios', 'rastreio': 'BR123'},
        )
        self.assertEqual(expedicao.status, Expedicao.Status.DESPACHO)

        # 7. O CLIENTE assina, pelo navegador, na página pública
        url = reverse('moda_publico:entrega', args=[expedicao.codigo])
        navegador = Client(enforce_csrf_checks=True)
        pagina = navegador.get(url)
        self.assertEqual(pagina.status_code, 200)

        resposta = navegador.post(
            url,
            {'recebido_por': 'Diego Macedo',
             'csrfmiddlewaretoken': navegador.cookies['csrftoken'].value},
            HTTP_ORIGIN=ORIGEM,
        )
        self.assertEqual(resposta.status_code, 302)

        # 8. O comprovante ficou gravado
        expedicao.refresh_from_db()
        self.assertTrue(expedicao.entregue)
        self.assertEqual(expedicao.recebido_por, 'Diego Macedo')
        self.assertIsNotNone(expedicao.data_entrega)

    def test_5_nao_da_para_despachar_pulando_a_embalagem(self):
        """
        As etapas são LINEARES de propósito: despachar sem embalar mandaria
        peça solta para a transportadora.
        """
        from apps.core.services.exceptions import DomainError

        ordem = OrdemProducaoService.gerar_do_pedido(self.pedido, usuario=None)[0]
        self._encerrar_qualidade(ordem)
        expedicao = ExpedicaoService.criar(self.filial, ordem)
        expedicao.status = Expedicao.Status.EMBALAGEM
        expedicao.save(update_fields=['status'])

        # Sem volume nenhum, a saída da embalagem tem de ser recusada.
        with self.assertRaises(DomainError):
            ExpedicaoService.avancar(expedicao, None, {})

    # ── Apoio ────────────────────────────────────────────────────────────

    @staticmethod
    def _encerrar_qualidade(ordem):
        """
        Encerra a etapa de qualidade, que é o que libera a expedição.

        Se o fluxo daquela ordem não tiver a etapa, não há o que encerrar —
        e o serviço já trata esse caso deixando passar.
        """
        etapa = ordem.etapas.filter(etapa='qualidade').first()
        if etapa is None:
            return
        # `encerrada` é propriedade de leitura: quem manda é o STATUS, e
        # concluída e pulada contam as duas como encerrada.
        etapa.status = etapa.Status.CONCLUIDA
        etapa.save(update_fields=['status'])
