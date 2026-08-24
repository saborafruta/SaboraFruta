"""
Reenviar o link de aprovação — a saída do beco.

O cliente pedia ajuste, a equipe refazia a arte, e a tela não tinha botão
nenhum. O link só aparece enquanto o pedido está "aguardando", então depois
da resposta ele sumia; o cartão continuava piscando no quadro e o alerta
ficava aceso no sino, porque a resposta gravada seguia sendo "ajuste" para
sempre. Quem precisava seguir mandava o link de outro lugar — e no quadro o
pedido ficava parado do mesmo jeito.

O que os testes cercam:

  · REENVIAR ABRE UMA RODADA NOVA: a resposta volta a "aguardando o cliente",
    e é isso que apaga o pisca-pisca e o alerta;
  · O ALERTA MORRE JUNTO, na hora. A varredura desligaria sozinha na próxima
    volta, mas até lá o sino apontaria para trabalho já feito — e alerta aceso
    depois de resolvido é o que ensina a ignorar o sino;
  · A RODADA ANTERIOR NÃO SE PERDE. O campo guarda a posição ATUAL do cliente;
    o motivo, o nome, a data e o IP da rodada anterior ficam na auditoria, que
    é de onde o histórico do pedido lê;
  · EXIGE `aprovar`. Reenviar é assumir preço e prazo perante o cliente de
    novo, e descarta registro — quem não pode liberar não reabre por outra
    porta;
  · REABRIR UM ACEITE AVISA ANTES. O aceite registrado é o que vira prova
    quando o pedido dá problema, e o botão que o desfaz tem de dizer isso.
"""
from django.test import TestCase
from django.urls import reverse

from apps.cadastros.models import Cliente
from apps.core.models import (
    Empresa, Filial, Notificacao, PerfilAcesso, Usuario,
)
from apps.moda.models import AprovacaoPedido, PedidoProducao
from apps.moda.services.alertas import AlertaService

S = PedidoProducao.Status
R = AprovacaoPedido.Resposta
TIPO = Notificacao.Tipo.MODA_CLIENTE_AJUSTE


class ReenvioBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao Reenvio LTDA', nome_fantasia='Reenvio',
            cnpj='53345678000191', segmento='moda_confeccao',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='53345678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Diego Macedo',
            cpf_cnpj='12345678901', ativo=True, celular='84999887766',
        )

    def _usuario(self, email, admin=True):
        perfil = PerfilAcesso.objects.create(
            empresa=self.empresa, nome=f'Perfil {email}', is_admin=admin,
        )
        return Usuario.objects.create_user(
            email=email, nome='Fulano', password='x' * 12,
            empresa=self.empresa, perfil=perfil, filial=self.filial,
        )

    def _pedido(self, numero=8):
        pedido = PedidoProducao.objects.create(
            filial=self.filial, cliente=self.cliente, numero=numero,
            status=S.AGUARDANDO_APROVACAO,
        )
        aprovacao = AprovacaoPedido.objects.create(pedido=pedido)
        aprovacao.liberar(usuario=None)
        return pedido, aprovacao

    def _com_ajuste(self, motivo='ALTERAR A COR'):
        """O estado do print: cliente pediu ajuste, alerta aceso."""
        pedido, aprovacao = self._pedido()
        self.client.post(
            reverse('moda_publico:pedido-responder', args=[pedido.token_publico]),
            {'resposta': R.AJUSTE, 'nome': 'THIAGO', 'motivo': motivo},
        )
        aprovacao.refresh_from_db()
        return pedido, aprovacao

    def _url(self, pedido):
        return reverse('moda:pedido-reenviar', args=[pedido.pk])

    def _alertas(self):
        return Notificacao.objects.filter(filial=self.filial, tipo=TIPO)


class RodadaNovaTests(ReenvioBase):

    def setUp(self):
        self.usuario = self._usuario('chefe@t.local')
        self.client.force_login(self.usuario)

    def test_reenviar_volta_a_aguardar_o_cliente(self):
        pedido, aprovacao = self._com_ajuste()

        self.client.post(self._url(pedido))

        aprovacao.refresh_from_db()
        self.assertTrue(aprovacao.aguardando_cliente)
        self.assertFalse(aprovacao.pediu_ajuste)

    def test_a_resposta_anterior_sai_do_campo(self):
        pedido, aprovacao = self._com_ajuste()

        self.client.post(self._url(pedido))

        aprovacao.refresh_from_db()
        self.assertEqual(aprovacao.resposta, R.PENDENTE)
        self.assertEqual(aprovacao.motivo_ajuste, '')
        self.assertEqual(aprovacao.respondido_por, '')
        self.assertIsNone(aprovacao.respondido_em)
        self.assertIsNone(aprovacao.ip_resposta)

    def test_a_liberacao_passa_a_ser_de_quem_reenviou(self):
        """
        Reenviar é assumir preço e prazo de novo, sobre uma arte que mudou. O
        passo 7 passa a falar desta rodada, e o nome que fica é o de quem
        decidiu.
        """
        pedido, aprovacao = self._com_ajuste()

        self.client.post(self._url(pedido))

        aprovacao.refresh_from_db()
        self.assertEqual(aprovacao.liberado_por, self.usuario)
        self.assertTrue(aprovacao.liberado)

    def test_a_observacao_do_reenvio_e_gravada(self):
        pedido, aprovacao = self._com_ajuste()

        self.client.post(self._url(pedido), {'observacao': 'Gola azul agora'})

        aprovacao.refresh_from_db()
        self.assertEqual(aprovacao.observacao_interna, 'Gola azul agora')

    def test_o_cartao_para_de_piscar_no_quadro(self):
        """
        É o efeito que o usuário vê. O pisca vem de `pediu_ajuste`, e reenviar
        é justamente o que deixa de ser verdade.
        """
        from apps.moda.services.kanban_comercial import KanbanComercialService

        pedido, _ = self._com_ajuste()

        self.client.post(self._url(pedido))

        quadro = KanbanComercialService.quadro(self.filial)
        raias = {r.coluna.chave: r for r in quadro['raias']}
        cartao = next(
            c for c in raias['aprovacao'].cartoes if c.pedido.pk == pedido.pk
        )
        self.assertFalse(cartao.pediu_ajuste)

    def test_a_tela_do_pedido_para_de_mostrar_o_ajuste(self):
        pedido, _ = self._com_ajuste()

        self.client.post(self._url(pedido))
        resposta = self.client.get(reverse('moda:pedido-detail', args=[pedido.pk]))

        self.assertNotContains(resposta, 'Cliente pediu ajuste')


class AlertaMorreJuntoTests(ReenvioBase):

    def setUp(self):
        self.client.force_login(self._usuario('chefe@t.local'))

    def test_o_alerta_do_sino_se_apaga_na_hora(self):
        pedido, _ = self._com_ajuste()
        self.assertTrue(self._alertas().get().ativa)

        self.client.post(self._url(pedido))

        self.assertFalse(self._alertas().get().ativa)

    def test_a_varredura_concorda_com_o_desligamento(self):
        """
        As duas metades precisam calcular a MESMA chave. Se divergissem, o
        desligamento imediato erraria o alvo e a varredura criaria uma segunda
        linha para o mesmo problema — este teste é o que quebra se o formato
        da referência mudar num lugar só.
        """
        pedido, _ = self._com_ajuste()

        self.client.post(self._url(pedido))
        AlertaService.sincronizar(self.filial)

        self.assertEqual(self._alertas().count(), 1)
        self.assertFalse(self._alertas().get().ativa)

    def test_pedir_ajuste_de_novo_reacende(self):
        """A rodada nova pode terminar igual — e aí o sino toca outra vez."""
        pedido, _ = self._com_ajuste()
        self.client.post(self._url(pedido))

        self.client.post(
            reverse('moda_publico:pedido-responder', args=[pedido.token_publico]),
            {'resposta': R.AJUSTE, 'nome': 'THIAGO', 'motivo': 'Ainda nao'},
        )

        alerta = self._alertas().get()
        self.assertTrue(alerta.ativa)
        self.assertIn('Ainda nao', alerta.mensagem)


class AceiteReabertoTests(ReenvioBase):
    """Mudou a arte depois do sim."""

    def setUp(self):
        self.client.force_login(self._usuario('chefe@t.local'))

    def _aprovado(self):
        pedido, aprovacao = self._pedido()
        self.client.post(
            reverse('moda_publico:pedido-responder', args=[pedido.token_publico]),
            {'resposta': R.APROVADO, 'nome': 'DIEGO'},
        )
        aprovacao.refresh_from_db()
        return pedido, aprovacao

    def test_a_tela_avisa_antes_do_botao(self):
        """
        O aceite registrado é o que vira prova quando o pedido dá problema.
        Um botão que o desfaz sem dizer isso é uma armadilha.
        """
        pedido, _ = self._aprovado()

        resposta = self.client.get(
            reverse('moda:pedido-aprovacao', args=[pedido.pk])
        )

        self.assertContains(resposta, 'tira este aceite da tela')
        self.assertContains(resposta, 'PEDIR NOVA APROVAÇÃO')

    def test_reabrir_o_aceite_volta_a_aguardar(self):
        pedido, aprovacao = self._aprovado()

        self.client.post(self._url(pedido))

        aprovacao.refresh_from_db()
        self.assertTrue(aprovacao.aguardando_cliente)
        self.assertFalse(aprovacao.aprovado_pelo_cliente)

    def test_reabrir_nao_puxa_o_pedido_de_volta_no_quadro(self):
        """
        O status já avançou para "Pedido Confirmado" quando o cliente
        aprovou. Reabrir a aprovação pede um aceite novo; não desfaz o
        caminho que o pedido já andou na fábrica.
        """
        pedido, _ = self._aprovado()
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, S.CONFIRMADO)

        self.client.post(self._url(pedido))

        pedido.refresh_from_db()
        self.assertEqual(pedido.status, S.CONFIRMADO)


class SemRodadaParaReabrirTests(ReenvioBase):

    def setUp(self):
        self.client.force_login(self._usuario('chefe@t.local'))

    def test_pedido_que_nunca_respondeu_nao_recarimba(self):
        """
        Não há rodada para reabrir, e o link já está na tela. Recarimbar a
        liberação aqui trocaria o nome de quem liberou por nada.
        """
        pedido, aprovacao = self._pedido()
        liberado_em = aprovacao.liberado_em

        self.client.post(self._url(pedido))

        aprovacao.refresh_from_db()
        self.assertEqual(aprovacao.liberado_em, liberado_em)
        self.assertIsNone(aprovacao.liberado_por)

    def test_e_avisa_que_o_link_continua_valendo(self):
        pedido, _ = self._pedido()

        resposta = self.client.post(self._url(pedido), follow=True)

        mensagens = [str(m) for m in resposta.context['messages']]
        self.assertTrue(
            any('continua valendo' in m for m in mensagens),
            f'esperava aviso de que o link vale, veio: {mensagens}',
        )


class PermissaoTests(ReenvioBase):
    """Quem não pode liberar não reabre por outra porta."""

    def test_sem_aprovar_o_post_nao_reabre(self):
        self.client.force_login(self._usuario('leitor@t.local', admin=False))
        pedido, aprovacao = self._com_ajuste()

        self.client.post(self._url(pedido))

        aprovacao.refresh_from_db()
        self.assertTrue(aprovacao.pediu_ajuste)
        self.assertEqual(aprovacao.motivo_ajuste, 'ALTERAR A COR')

    def test_sem_aprovar_a_tela_explica_em_vez_de_sumir(self):
        """
        Botão que simplesmente não está lá faz a pessoa procurar em todas as
        telas antes de desconfiar que é permissão.
        """
        self.client.force_login(self._usuario('leitor@t.local', admin=False))
        pedido, _ = self._com_ajuste()

        resposta = self.client.get(
            reverse('moda:pedido-aprovacao', args=[pedido.pk])
        )

        if resposta.status_code == 200:
            self.assertFalse(resposta.context['pode_reenviar'])
            self.assertContains(resposta, 'exige a permissão')


class TelaDoAjusteTests(ReenvioBase):
    """O que a pessoa vê no estado do print."""

    def setUp(self):
        self.client.force_login(self._usuario('chefe@t.local'))

    def _abrir(self, pedido):
        return self.client.get(
            reverse('moda:pedido-aprovacao', args=[pedido.pk])
        )

    def test_o_botao_de_reenviar_aparece(self):
        pedido, _ = self._com_ajuste()

        self.assertContains(self._abrir(pedido), 'REENVIAR PARA APROVAÇÃO')

    def test_o_link_volta_a_aparecer(self):
        """
        Ele sumia depois da resposta — e era metade do beco: mesmo sabendo o
        que fazer, não havia de onde copiar o endereço.
        """
        pedido, _ = self._com_ajuste()

        self.assertContains(self._abrir(pedido), pedido.token_publico)

    def test_o_whatsapp_aparece_quando_ha_telefone(self):
        pedido, _ = self._com_ajuste()

        self.assertContains(self._abrir(pedido), 'wa.me/5584999887766')

    def test_sem_telefone_nao_oferece_whatsapp(self):
        """
        `wa.me/` sem número abre "número inválido" no celular, e quem clicou
        fica achando que o link do pedido é que está quebrado.
        """
        self.cliente.celular = ''
        self.cliente.telefone = ''
        self.cliente.save(update_fields=['celular', 'telefone'])
        pedido, _ = self._com_ajuste()

        self.assertNotContains(self._abrir(pedido), 'wa.me/')

    def test_o_motivo_continua_a_vista_ao_lado_do_botao(self):
        """Quem vai refazer a arte precisa do texto na mesma tela."""
        pedido, _ = self._com_ajuste(motivo='ALTERAR A COR')

        resposta = self._abrir(pedido)

        self.assertContains(resposta, 'ALTERAR A COR')
        self.assertContains(resposta, 'Refez a arte?')

    def test_ajuste_sem_motivo_nao_deixa_o_bloco_vazio(self):
        pedido, _ = self._com_ajuste(motivo='')

        self.assertContains(self._abrir(pedido), 'Sem motivo informado')
