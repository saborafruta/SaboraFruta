"""
A resposta do cliente move o cartão sozinha.

O vendedor mandava o link, o cliente aprovava, e o cartão continuava em
"Aguardando Aprovação" até alguém de dentro arrastar. A coluna virava
depósito de pedido já resolvido — e os que DE FATO esperavam resposta sumiam
no meio deles. Quem olhava o quadro não conseguia responder "quem está me
devendo um sim?", que é a única pergunta que aquela coluna existe para
responder.

O que os testes cercam:

  · APROVOU AVANÇA sozinho, sem ninguém arrastar;
  · PEDIU AJUSTE NÃO MOVE. O pedido continua aguardando aprovação, porque é
    isso que ele está fazendo — esperando arte nova e o novo sim. Movê-lo
    para "Arte" pareceria mais arrumado e apagaria o fato de haver cliente
    esperando;
  · SÓ AVANÇA, NUNCA VOLTA. E aqui mora a armadilha: no enum de status
    `confirmado` vem ANTES de `aguardando_arte`, porque o enum foi escrito na
    ordem do cadastro. No quadro, "Pedido Confirmado" vem DEPOIS de
    "Aguardando Aprovação", porque quem confirma é o cliente. Comparar pela
    ordem do enum faria o aceite que chega tarde PUXAR DE VOLTA para o
    comercial um pedido que já está na produção;
  · o cartão com ajuste PISCA. O dado já existia na tela em texto; sem o
    alerta, achar o cartão no meio da coluna era abrir um por um.
"""
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from datetime import timedelta
from decimal import Decimal

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.moda.models import (
    AprovacaoPedido, ItemPedidoProducao, PedidoProducao, ProdutoModa,
)

S = PedidoProducao.Status
R = AprovacaoPedido.Resposta


class RespostaBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao Cartao LTDA', nome_fantasia='Cartao',
            cnpj='53345678000191', segmento='moda_confeccao',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='53345678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Henry Freitas',
            cpf_cnpj='12345678901', ativo=True,
        )

    def _pedido(self, status=S.AGUARDANDO_APROVACAO, numero=1):
        pedido = PedidoProducao.objects.create(
            filial=self.filial, cliente=self.cliente, numero=numero,
            status=status,
        )
        aprovacao = AprovacaoPedido.objects.create(pedido=pedido)
        aprovacao.liberar(usuario=None)
        return pedido, aprovacao

    def _completar(self, pedido):
        """O que `OrcamentoService.faltas` cobra para fechar a proposta."""
        produto = ProdutoModa.objects.create(
            filial=self.filial, codigo=f'CAM{pedido.numero:03d}', nome='Camisa',
        )
        ItemPedidoProducao.objects.create(
            pedido=pedido, produto=produto, descricao='Camisa',
            quantidade=10, valor_unitario=Decimal('45'),
        )
        pedido.data_prevista_entrega = timezone.localdate() + timedelta(days=20)
        pedido.save(update_fields=['data_prevista_entrega'])
        return pedido

    def _responder(self, pedido, resposta, nome='Henry'):
        """Pelo link público, do jeito que o cliente responde de verdade."""
        return self.client.post(
            reverse('moda_publico:pedido-responder', args=[pedido.token_publico]),
            {'resposta': resposta, 'nome': nome},
        )


class AprovacaoAvancaTests(RespostaBase):

    def test_aprovar_leva_o_pedido_para_confirmado(self):
        pedido, _ = self._pedido()

        self._responder(pedido, R.APROVADO)

        pedido.refresh_from_db()
        self.assertEqual(pedido.status, S.CONFIRMADO)

    def test_o_cartao_muda_de_coluna_no_quadro(self):
        """O status por si só não prova nada: o que o usuário vê é a coluna."""
        from apps.moda.services.kanban_comercial import KanbanComercialService

        pedido, _ = self._pedido()
        self._responder(pedido, R.APROVADO)

        quadro = KanbanComercialService.quadro(self.filial)
        raias = {r.coluna.chave: r for r in quadro['raias']}

        self.assertIn(pedido.pk, [c.pedido.pk for c in raias['confirmado'].cartoes])
        self.assertNotIn(pedido.pk, [c.pedido.pk for c in raias['aprovacao'].cartoes])

    def test_a_resposta_continua_gravada(self):
        """Mover o cartão não pode substituir o registro do aceite."""
        pedido, aprovacao = self._pedido()

        self._responder(pedido, R.APROVADO, nome='Henry Freitas')

        aprovacao.refresh_from_db()
        self.assertEqual(aprovacao.resposta, R.APROVADO)
        self.assertEqual(aprovacao.respondido_por, 'Henry Freitas')
        self.assertIsNotNone(aprovacao.respondido_em)

    def test_aprovar_a_partir_do_orcamento_tambem_avanca(self):
        """
        Orçamento liberado abre o link — então pode receber um sim, e o sim
        vale igual.
        """
        pedido, _ = self._pedido(status=S.ORCAMENTO)
        self._completar(pedido)

        self._responder(pedido, R.APROVADO)

        pedido.refresh_from_db()
        self.assertEqual(pedido.status, S.CONFIRMADO)

    def test_orcamento_fecha_pelo_servico_de_orcamento(self):
        """
        E não por atribuição de status. Quem sabe o que falta para uma
        proposta virar compromisso é aquele serviço; trocar o status na mão
        aqui abriria a porta dos fundos que o quadro fecha do outro lado.
        """
        from unittest.mock import patch

        from apps.moda.services.kanban_comercial import avancar_por_resposta

        pedido, aprovacao = self._pedido(status=S.ORCAMENTO)
        self._completar(pedido)
        aprovacao.responder(resposta=R.APROVADO, nome='Henry')

        with patch(
            'apps.moda.services.orcamentos.OrcamentoService.fechar'
        ) as fechar:
            avancar_por_resposta(pedido, aprovacao)

        fechar.assert_called_once_with(pedido)

    def test_orcamento_incompleto_nao_fecha_e_nao_estoura(self):
        """
        O sim do cliente JÁ ESTÁ GRAVADO — e não pode virar tela de erro na
        cara de quem está do lado de fora por causa de um campo que falta
        aqui dentro. Fica na mesma coluna, com a resposta registrada.
        """
        pedido, aprovacao = self._pedido(status=S.ORCAMENTO)  # sem item, sem data

        resposta = self._responder(pedido, R.APROVADO)

        self.assertEqual(resposta.status_code, 302)
        pedido.refresh_from_db()
        aprovacao.refresh_from_db()
        self.assertEqual(pedido.status, S.ORCAMENTO)
        self.assertEqual(aprovacao.resposta, R.APROVADO)


class AjusteNaoMoveTests(RespostaBase):

    def test_pedir_ajuste_mantem_aguardando_aprovacao(self):
        pedido, _ = self._pedido()

        self._responder(pedido, R.AJUSTE)

        pedido.refresh_from_db()
        self.assertEqual(pedido.status, S.AGUARDANDO_APROVACAO)

    def test_o_cartao_fica_na_coluna_da_aprovacao(self):
        from apps.moda.services.kanban_comercial import KanbanComercialService

        pedido, _ = self._pedido()
        self._responder(pedido, R.AJUSTE)

        quadro = KanbanComercialService.quadro(self.filial)
        raias = {r.coluna.chave: r for r in quadro['raias']}

        self.assertIn(pedido.pk, [c.pedido.pk for c in raias['aprovacao'].cartoes])

    def test_o_cartao_sabe_que_pediu_ajuste(self):
        from apps.moda.services.kanban_comercial import KanbanComercialService

        pedido, _ = self._pedido()
        self._responder(pedido, R.AJUSTE)

        quadro = KanbanComercialService.quadro(self.filial)
        raias = {r.coluna.chave: r for r in quadro['raias']}
        cartao = next(
            c for c in raias['aprovacao'].cartoes if c.pedido.pk == pedido.pk
        )

        self.assertTrue(cartao.pediu_ajuste)


class NuncaVoltaTests(RespostaBase):
    """A armadilha da ordem do enum."""

    def test_as_duas_ordens_sao_mesmo_diferentes(self):
        """
        Se um dia alguém reordenar o enum e este teste cair, o guarda de
        posição pode ter deixado de ser necessário — ou, pior, passado a
        proteger a coisa errada. Vale ser avisado.
        """
        from apps.moda.services.kanban_comercial import POSICAO

        ordem_enum = [s for s, _ in S.choices]
        self.assertLess(
            ordem_enum.index(S.CONFIRMADO),
            ordem_enum.index(S.AGUARDANDO_APROVACAO),
            'no enum, confirmado vem antes de aguardando_aprovacao',
        )
        self.assertGreater(
            POSICAO['confirmado'], POSICAO['aprovacao'],
            'no quadro a ordem é a oposta — e o quadro é quem manda aqui',
        )

    def test_aceite_tardio_nao_puxa_pedido_da_producao_de_volta(self):
        pedido, _ = self._pedido(status=S.EM_PRODUCAO)

        self._responder(pedido, R.APROVADO)

        pedido.refresh_from_db()
        self.assertEqual(pedido.status, S.EM_PRODUCAO)

    def test_aceite_tardio_nao_mexe_no_que_ja_esta_pronto(self):
        pedido, _ = self._pedido(status=S.PRONTO)

        self._responder(pedido, R.APROVADO)

        pedido.refresh_from_db()
        self.assertEqual(pedido.status, S.PRONTO)

    def test_pedido_ja_confirmado_nao_e_salvo_de_novo(self):
        from apps.moda.services.kanban_comercial import avancar_por_resposta

        pedido, aprovacao = self._pedido(status=S.CONFIRMADO)
        aprovacao.responder(resposta=R.APROVADO, nome='Henry')

        self.assertFalse(avancar_por_resposta(pedido, aprovacao))

    def test_sem_resposta_nao_move_nada(self):
        from apps.moda.services.kanban_comercial import avancar_por_resposta

        pedido, aprovacao = self._pedido()

        self.assertFalse(avancar_por_resposta(pedido, aprovacao))
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, S.AGUARDANDO_APROVACAO)

    def test_sem_aprovacao_nenhuma_nao_estoura(self):
        from apps.moda.services.kanban_comercial import avancar_por_resposta

        pedido = PedidoProducao.objects.create(
            filial=self.filial, cliente=self.cliente, numero=99,
            status=S.AGUARDANDO_APROVACAO,
        )

        self.assertFalse(avancar_por_resposta(pedido, None))


class AlertaNaTelaTests(RespostaBase):
    """O cartão com ajuste precisa se anunciar no quadro."""

    def setUp(self):
        perfil = PerfilAcesso.objects.create(
            empresa=self.empresa, nome='Admin', is_admin=True,
        )
        usuario = Usuario.objects.create_user(
            email='chefe@t.local', nome='Fulano', password='x' * 12,
            empresa=self.empresa, perfil=perfil, filial=self.filial,
        )
        self.client.force_login(usuario)

    def _quadro(self):
        return self.client.get(reverse('moda:comercial'))

    def test_o_cartao_com_ajuste_recebe_a_classe_que_pisca(self):
        pedido, aprovacao = self._pedido()
        aprovacao.responder(resposta=R.AJUSTE, nome='Henry', motivo='Trocar a cor')

        resposta = self._quadro()

        self.assertContains(resposta, 'kc-card rounded-lg p-2.5 kc-alerta')

    def test_cartao_sem_ajuste_nao_pisca(self):
        """
        Alerta que aparece em tudo não é alerta. Se todo cartão piscasse, o
        que precisa ser visto ficaria igual ao resto.
        """
        self._pedido()

        self.assertNotContains(self._quadro(), 'kc-card rounded-lg p-2.5 kc-alerta')

    def test_a_animacao_existe_na_folha(self):
        """A classe sozinha não pisca nada."""
        resposta = self._quadro()

        self.assertContains(resposta, '@keyframes kc-pisca')
        self.assertContains(resposta, '.kc-card.kc-alerta')

    def test_quem_pediu_menos_movimento_continua_vendo_o_alerta(self):
        """
        Piscar aqui é chamar atenção, não enfeite: apagar o aviso junto com a
        animação esconderia justamente o cartão que precisa ser visto.
        """
        resposta = self._quadro()

        self.assertContains(resposta, 'prefers-reduced-motion')
        trecho = resposta.content.decode().split('prefers-reduced-motion', 1)[1][:400]
        self.assertIn('animation: none', trecho)
        self.assertIn('box-shadow', trecho)
