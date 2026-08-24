"""
O ajuste do cliente toca o sino e aparece na tela do pedido.

Faltavam as duas pontas. O cliente respondia "quero ajuste" pelo link, e
dentro da empresa nada acontecia: o sino não tocava, e quem abria o pedido
via só "Aguardando Aprovação" — indistinguível de um pedido que o cliente
ainda nem tinha aberto. O motivo do ajuste existia gravado, mas só a tela de
aprovação mostrava, e era preciso saber que ela existia para ir até lá ler
uma frase.

O que os testes cercam:

  · O SINO TOCA NA HORA. A varredura de alertas roda de hora em hora, e este
    é justamente o aviso que não pode esperar a próxima volta: há cliente
    parado esperando arte nova;
  · O ALERTA SE DESLIGA SOZINHO quando o cliente aprova. É condição, não
    evento — a regra do módulo inteiro. Alerta que só acumula vira ruído, e
    em duas semanas ninguém abre mais o sino;
  · UM PEDIDO, UM ALERTA. Responder duas vezes não pode encher o sino de
    cópias do mesmo aviso;
  · O SINO NÃO PODE DERRUBAR O CLIENTE. O aviso é publicado dentro do POST da
    página pública: falhar ali viraria tela de erro para quem está do lado de
    fora por causa do sino de quem está do lado de dentro;
  · O MOTIVO APARECE NA TELA DO PEDIDO, e SEPARADO da observação do pedido.
    Aquele campo é recado entre a equipe, e alguém edita e salva; despejar o
    texto do cliente ali dentro faria a próxima gravação virar tudo uma coisa
    só, sem dizer mais quem escreveu o quê.
"""
from unittest.mock import patch

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


class AjusteBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao Sino LTDA', nome_fantasia='Sino',
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

    def _responder(self, pedido, resposta, nome='Henry', motivo=''):
        return self.client.post(
            reverse('moda_publico:pedido-responder', args=[pedido.token_publico]),
            {'resposta': resposta, 'nome': nome, 'motivo': motivo},
        )

    def _alertas(self):
        return Notificacao.objects.filter(filial=self.filial, tipo=TIPO)


class SinoTocaTests(AjusteBase):

    def test_pedido_de_ajuste_cria_a_notificacao(self):
        pedido, _ = self._pedido()

        self._responder(pedido, R.AJUSTE, motivo='Trocar a cor da gola')

        alerta = self._alertas().get()
        self.assertTrue(alerta.ativa)
        self.assertIn(f'#{pedido.numero:06d}', alerta.titulo)

    def test_a_notificacao_carrega_o_motivo(self):
        """
        Quem lê o sino no meio do turno decide dali se para o que está
        fazendo. "Trocar a cor da gola" e "refazer a arte toda" não pedem a
        mesma coisa — sem o texto, todo alerta obriga a abrir o pedido.
        """
        pedido, _ = self._pedido()

        self._responder(pedido, R.AJUSTE, motivo='Trocar a cor da gola')

        self.assertIn('Trocar a cor da gola', self._alertas().get().mensagem)

    def test_a_notificacao_leva_ao_pedido(self):
        pedido, _ = self._pedido()

        self._responder(pedido, R.AJUSTE, motivo='Trocar a cor')

        self.assertEqual(
            self._alertas().get().url,
            reverse('moda:pedido-detail', args=[pedido.pk]),
        )

    def test_ajuste_sem_motivo_ainda_avisa(self):
        """
        O cliente pode mandar sem escrever nada. Engolir o alerta por causa
        disso esconderia o pedido que mais precisa de alguém ligando.
        """
        pedido, _ = self._pedido()

        self._responder(pedido, R.AJUSTE, motivo='')

        self.assertEqual(self._alertas().count(), 1)

    def test_aprovar_nao_toca_o_sino(self):
        pedido, _ = self._pedido()

        self._responder(pedido, R.APROVADO)

        self.assertEqual(self._alertas().count(), 0)

    def test_o_sino_da_tela_mostra_o_alerta(self):
        """
        A prova de que chega ao sino é o contexto que o sino lê, não a linha
        no banco: o processador filtra por filial e por `ativa`.
        """
        perfil = PerfilAcesso.objects.create(
            empresa=self.empresa, nome='Admin', is_admin=True,
        )
        usuario = Usuario.objects.create_user(
            email='chefe@t.local', nome='Fulano', password='x' * 12,
            empresa=self.empresa, perfil=perfil, filial=self.filial,
        )
        pedido, _ = self._pedido()
        self._responder(pedido, R.AJUSTE, motivo='Trocar a cor')

        self.client.force_login(usuario)
        resposta = self.client.get(reverse('moda:comercial'))

        titulos = [n.titulo for n in resposta.context['notificacoes_recentes']]
        self.assertGreaterEqual(resposta.context['notificacoes_nao_lidas'], 1)
        self.assertTrue(
            any(f'#{pedido.numero:06d}' in t for t in titulos),
            f'esperava o alerta do pedido no sino, veio: {titulos}',
        )


class UmPedidoUmAlertaTests(AjusteBase):

    def test_responder_duas_vezes_nao_duplica(self):
        pedido, _ = self._pedido()

        self._responder(pedido, R.AJUSTE, motivo='Trocar a cor')
        self._responder(pedido, R.AJUSTE, motivo='Na verdade, trocar a manga')

        self.assertEqual(self._alertas().count(), 1)

    def test_a_segunda_resposta_atualiza_o_texto(self):
        """
        Um aviso que mostra o motivo antigo é pior que nenhum: manda refazer
        a coisa errada.
        """
        pedido, _ = self._pedido()

        self._responder(pedido, R.AJUSTE, motivo='Trocar a cor')
        self._responder(pedido, R.AJUSTE, motivo='Na verdade, trocar a manga')

        self.assertIn('trocar a manga', self._alertas().get().mensagem)

    def test_a_varredura_reconhece_o_alerta_do_evento(self):
        """
        As duas metades usam a mesma chave. Se divergissem, a varredura
        criaria uma segunda linha para o mesmo problema.
        """
        pedido, _ = self._pedido()
        self._responder(pedido, R.AJUSTE, motivo='Trocar a cor')

        AlertaService.sincronizar(self.filial)

        self.assertEqual(self._alertas().count(), 1)
        self.assertTrue(self._alertas().get().ativa)


class DesligaSozinhoTests(AjusteBase):
    """É condição, não evento — e condição que passa desliga o aviso."""

    def test_cliente_aprova_depois_e_o_alerta_se_desliga(self):
        pedido, _ = self._pedido()
        self._responder(pedido, R.AJUSTE, motivo='Trocar a cor')

        self._responder(pedido, R.APROVADO)
        AlertaService.sincronizar(self.filial)

        self.assertFalse(self._alertas().get().ativa)

    def test_pedido_entregue_nao_alerta_mais(self):
        pedido, _ = self._pedido()
        self._responder(pedido, R.AJUSTE, motivo='Trocar a cor')

        pedido.status = S.ENTREGUE
        pedido.save(update_fields=['status'])
        AlertaService.sincronizar(self.filial)

        self.assertFalse(self._alertas().get().ativa)

    def test_pedido_cancelado_nao_alerta_mais(self):
        pedido, _ = self._pedido()
        self._responder(pedido, R.AJUSTE, motivo='Trocar a cor')

        pedido.status = S.CANCELADO
        pedido.save(update_fields=['status'])
        AlertaService.sincronizar(self.filial)

        self.assertFalse(self._alertas().get().ativa)

    def test_o_detector_acha_o_pedido_com_ajuste(self):
        pedido, _ = self._pedido()
        self._responder(pedido, R.AJUSTE, motivo='Trocar a cor')

        chaves = [a.referencia for a in AlertaService.detectar(self.filial)]

        self.assertIn(f'pedido:{pedido.pk}', chaves)


class NaoDerrubaOClienteTests(AjusteBase):
    """O sino de dentro não pode virar tela de erro para quem está fora."""

    def test_falha_ao_publicar_nao_quebra_a_resposta(self):
        pedido, aprovacao = self._pedido()

        with patch.object(
            AlertaService, 'publicar', side_effect=RuntimeError('banco fora')
        ):
            resposta = self._responder(pedido, R.AJUSTE, motivo='Trocar a cor')

        self.assertEqual(resposta.status_code, 302)
        aprovacao.refresh_from_db()
        self.assertEqual(aprovacao.resposta, R.AJUSTE)
        self.assertEqual(aprovacao.motivo_ajuste, 'Trocar a cor')


class MotivoNaTelaTests(AjusteBase):
    """O campo 2: o que o cliente pediu, na tela do pedido."""

    def setUp(self):
        perfil = PerfilAcesso.objects.create(
            empresa=self.empresa, nome='Admin', is_admin=True,
        )
        usuario = Usuario.objects.create_user(
            email='chefe@t.local', nome='Fulano', password='x' * 12,
            empresa=self.empresa, perfil=perfil, filial=self.filial,
        )
        self.client.force_login(usuario)

    def _abrir(self, pedido):
        return self.client.get(reverse('moda:pedido-detail', args=[pedido.pk]))

    def test_a_tela_mostra_o_motivo_do_ajuste(self):
        pedido, aprovacao = self._pedido()
        aprovacao.responder(
            resposta=R.AJUSTE, nome='Henry', motivo='Trocar a cor da gola',
        )

        resposta = self._abrir(pedido)

        self.assertContains(resposta, 'Cliente pediu ajuste')
        self.assertContains(resposta, 'Trocar a cor da gola')

    def test_a_tela_diz_quem_respondeu_e_quando(self):
        """Sem isso não dá para saber se a frase é de ontem ou de um mês."""
        pedido, aprovacao = self._pedido()
        aprovacao.responder(resposta=R.AJUSTE, nome='Henry Freitas', motivo='Trocar')

        self.assertContains(self._abrir(pedido), 'Henry Freitas')

    def test_ajuste_sem_motivo_diz_que_nao_veio_motivo(self):
        """
        Bloco vazio deixaria a pessoa achando que a tela quebrou. Dizer que
        não veio motivo é o que manda ela ligar para o cliente.
        """
        pedido, aprovacao = self._pedido()
        aprovacao.responder(resposta=R.AJUSTE, nome='Henry', motivo='')

        self.assertContains(self._abrir(pedido), 'Sem motivo informado')

    def test_pedido_sem_ajuste_nao_mostra_o_bloco(self):
        pedido, _ = self._pedido()

        self.assertNotContains(self._abrir(pedido), 'Cliente pediu ajuste')

    def test_pedido_aprovado_nao_mostra_o_bloco(self):
        pedido, aprovacao = self._pedido()
        aprovacao.responder(resposta=R.APROVADO, nome='Henry')

        self.assertNotContains(self._abrir(pedido), 'Cliente pediu ajuste')

    def test_abrir_a_tela_nao_escreve_na_observacao_do_pedido(self):
        """
        A observação do pedido é recado entre a equipe, e alguém edita e
        salva. Se o texto do cliente fosse despejado ali, a próxima gravação
        viraria tudo uma coisa só, sem dizer mais quem escreveu o quê — e
        cada recarga da tela empilharia mais uma cópia.
        """
        pedido, aprovacao = self._pedido()
        pedido.observacoes = 'Entregar na portaria.'
        pedido.save(update_fields=['observacoes'])
        aprovacao.responder(resposta=R.AJUSTE, nome='Henry', motivo='Trocar a cor')

        self._abrir(pedido)
        self._abrir(pedido)

        pedido.refresh_from_db()
        self.assertEqual(pedido.observacoes, 'Entregar na portaria.')

    def test_a_observacao_da_equipe_continua_aparecendo(self):
        """Os dois textos convivem: são de autores diferentes."""
        pedido, aprovacao = self._pedido()
        pedido.observacoes = 'Entregar na portaria.'
        pedido.save(update_fields=['observacoes'])
        aprovacao.responder(resposta=R.AJUSTE, nome='Henry', motivo='Trocar a cor')

        resposta = self._abrir(pedido)

        self.assertContains(resposta, 'Entregar na portaria.')
        self.assertContains(resposta, 'Trocar a cor')
