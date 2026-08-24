"""
Abrir a conferência por cima das pendências — o desvio, com dono.

Antes isto era um beco: a tela explicava por que não dava e devolvia a
pessoa ao pedido, sem caminho nenhum. Quem precisava seguir ia ao Django
Admin, onde não há validação nem registro de quem fez o quê — o atalho
existia, só era invisível e sem rastro.

O que os testes cercam:

  · a TRAVA PADRÃO não pode afrouxar. `forcar` é parâmetro novo com padrão
    `False`: quem chamar o serviço sem pensar continua batendo na validação;
  · o desvio precisa de PERMISSÃO. Pular a validação da produção tem a mesma
    gravidade de liberar um pedido, e quem não pode liberar não pode
    contornar a liberação por outra porta;
  · o desvio precisa de RASTRO. Um atalho sem registro é o que ninguém
    consegue explicar seis meses depois;
  · a tela precisa MOSTRAR o que está sendo ignorado. Um "tem certeza?" que
    não diz do que se trata treina a pessoa a clicar em sim sem ler.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.moda.models import (
    Expedicao, ItemPedidoProducao, OrdemProducao, PedidoProducao, ProdutoModa,
)


class ForcarBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao Forcar LTDA', nome_fantasia='Forcar',
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

    def _usuario(self, email, **permissoes):
        perfil = PerfilAcesso.objects.create(
            empresa=self.empresa, nome=f'Perfil {email}',
            is_admin=permissoes.get('admin', False),
        )
        return Usuario.objects.create_user(
            email=email, nome='Fulano', password='x' * 12,
            empresa=self.empresa, perfil=perfil, filial=self.filial,
        )

    def _pedido_travado(self):
        """
        Um pedido "Pronto" que nunca passou pela produção — sem aprovação,
        sem ficha e sem roteiro. É o do print do usuário.
        """
        produto = ProdutoModa.objects.create(
            filial=self.filial, codigo='CAM001', nome='Camisa',
        )
        pedido = PedidoProducao.objects.create(
            filial=self.filial, cliente=self.cliente, numero=4,
            status=PedidoProducao.Status.PRONTO,
            data_prevista_entrega=timezone.localdate() + timedelta(days=27),
        )
        ItemPedidoProducao.objects.create(
            pedido=pedido, produto=produto, descricao='Camisa',
            quantidade=7, valor_unitario=Decimal('45'),
        )
        return pedido


class TravaPadraoTests(ForcarBase):
    """O desvio não pode afrouxar o caminho normal."""

    def test_sem_forcar_a_validacao_continua_cobrando(self):
        from apps.core.services.exceptions import DomainError
        from apps.moda.services import OrdemProducaoService

        pedido = self._pedido_travado()

        with self.assertRaises(DomainError):
            OrdemProducaoService.gerar_do_pedido(pedido, usuario=None)

    def test_o_padrao_do_parametro_e_nao_forcar(self):
        """
        Quem chamar o serviço sem pensar tem de receber a trava, e não o
        atalho. É o padrão do parâmetro que garante isso.
        """
        import inspect

        from apps.moda.services import OrdemProducaoService

        assinatura = inspect.signature(
            OrdemProducaoService.gerar_do_pedido.__func__
        )
        self.assertIs(assinatura.parameters['forcar'].default, False)

    def test_com_forcar_a_ordem_sai(self):
        from apps.moda.services import OrdemProducaoService

        pedido = self._pedido_travado()

        ordens = OrdemProducaoService.gerar_do_pedido(
            pedido, usuario=None, forcar=True,
        )

        self.assertEqual(len(ordens), 1)
        self.assertEqual(ordens[0].quantidade, 7)

    def test_forcar_nao_passa_por_cima_do_cancelado(self):
        """
        Pedido cancelado continua não gerando ordem. `forcar` pula a
        VALIDAÇÃO da produção, não as regras do próprio pedido.
        """
        from apps.core.services.exceptions import DomainError
        from apps.moda.services import OrdemProducaoService

        pedido = self._pedido_travado()
        pedido.status = PedidoProducao.Status.CANCELADO
        pedido.save(update_fields=['status'])

        with self.assertRaises(DomainError):
            OrdemProducaoService.gerar_do_pedido(
                pedido, usuario=None, forcar=True,
            )


class TelaDaPerguntaTests(ForcarBase):
    """O que a pessoa vê antes de decidir."""

    def setUp(self):
        self.usuario = self._usuario('chefe@t.local', admin=True)
        self.client.force_login(self.usuario)

    def test_o_botao_leva_a_pergunta_em_vez_de_um_beco(self):
        pedido = self._pedido_travado()

        resposta = self.client.get(
            reverse('moda:pedido-conferencia', args=[pedido.pk])
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'ainda não passou pela produção')

    def test_a_tela_mostra_as_pendencias_uma_a_uma(self):
        """
        Um "tem certeza?" que não diz do que se trata treina a pessoa a
        clicar em sim sem ler.
        """
        pedido = self._pedido_travado()

        resposta = self.client.get(
            reverse('moda:pedido-conferencia', args=[pedido.pk])
        )

        self.assertContains(resposta, 'pendência(s) que travam a produção')
        # As mesmas que a validação da produção acusa, e não uma lista à
        # parte que pode divergir dela.
        from apps.moda.services.validacao import ValidacaoProducao

        bloqueios = [c for c in ValidacaoProducao.checar(pedido) if c.bloqueia]
        self.assertTrue(bloqueios)
        for checagem in bloqueios:
            self.assertContains(resposta, checagem.label)

    def test_a_tela_diz_o_que_vai_acontecer(self):
        pedido = self._pedido_travado()

        resposta = self.client.get(
            reverse('moda:pedido-conferencia', args=[pedido.pk])
        )

        self.assertContains(resposta, 'O que acontece se prosseguir')
        self.assertContains(resposta, 'com o seu nome')

    def test_com_expedicao_aberta_abre_o_qr_em_vez_da_pergunta(self):
        """
        Quem já tem expedição não vê a pergunta das pendências: vê o QR da
        conferência, que é a porta para conferir ao lado da caixa.
        """
        pedido = self._pedido_travado()
        item = pedido.itens.first()
        ordem = OrdemProducao.objects.create(
            filial=self.filial, pedido=pedido, item=item,
            numero='OP-0001', ano=2026, sequencial=1, quantidade=7,
        )
        expedicao = Expedicao.objects.create(
            filial=self.filial, ordem=ordem, numero=1,
        )

        resposta = self.client.get(
            reverse('moda:pedido-conferencia', args=[pedido.pk])
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertNotContains(resposta, 'pendência(s) que travam a produção')
        self.assertContains(resposta, expedicao.codigo)
        # E o caminho para conferir ali mesmo continua à mão.
        self.assertContains(
            resposta, reverse('moda:conferencia-pessoas', args=[expedicao.pk])
        )


class PermissaoTests(ForcarBase):
    """Quem não pode liberar não contorna a liberação por outra porta."""

    def test_sem_permissao_de_aprovar_a_tela_nao_oferece_o_botao(self):
        usuario = self._usuario('leitor@t.local')
        self.client.force_login(usuario)
        pedido = self._pedido_travado()

        resposta = self.client.get(
            reverse('moda:pedido-conferencia', args=[pedido.pk])
        )

        if resposta.status_code == 200:
            self.assertFalse(resposta.context['pode_forcar'])
            self.assertContains(resposta, 'exige a permissão')

    def test_sem_permissao_o_post_nao_cria_nada(self):
        usuario = self._usuario('leitor@t.local')
        self.client.force_login(usuario)
        pedido = self._pedido_travado()

        self.client.post(
            reverse('moda:pedido-conferencia-forcar', args=[pedido.pk])
        )

        self.assertEqual(OrdemProducao.objects.count(), 0)
        self.assertEqual(Expedicao.objects.count(), 0)


class RastroTests(ForcarBase):
    """Um atalho sem registro é o que ninguém explica depois."""

    def setUp(self):
        self.usuario = self._usuario('chefe@t.local', admin=True)
        self.client.force_login(self.usuario)

    def test_forcar_cria_ordem_e_expedicao_e_leva_a_conferencia(self):
        pedido = self._pedido_travado()

        resposta = self.client.post(
            reverse('moda:pedido-conferencia-forcar', args=[pedido.pk])
        )

        self.assertEqual(OrdemProducao.objects.count(), 1)
        expedicao = Expedicao.objects.get()
        self.assertRedirects(
            resposta,
            reverse('moda:conferencia-pessoas', args=[expedicao.pk]),
            fetch_redirect_response=False,
        )

    def test_a_expedicao_guarda_quem_forcou_e_o_que_foi_ignorado(self):
        pedido = self._pedido_travado()

        self.client.post(
            reverse('moda:pedido-conferencia-forcar', args=[pedido.pk])
        )

        observacao = Expedicao.objects.get().observacao

        self.assertIn('Fulano', observacao)
        self.assertIn('pendência', observacao)

    def test_a_ordem_tambem_guarda_o_registro(self):
        """
        Na ordem também, e não só na expedição: quem abre a OP no chão de
        fábrica precisa saber que ela saiu sem ficha.
        """
        pedido = self._pedido_travado()

        self.client.post(
            reverse('moda:pedido-conferencia-forcar', args=[pedido.pk])
        )

        self.assertIn('Fulano', OrdemProducao.objects.get().observacoes)

    def test_reaproveita_a_ordem_que_ja_existe(self):
        """
        Emitir outra colocaria a mesma peça duas vezes na fila da fábrica —
        o pedido pode estar travado só na expedição.
        """
        pedido = self._pedido_travado()
        item = pedido.itens.first()
        OrdemProducao.objects.create(
            filial=self.filial, pedido=pedido, item=item,
            numero='OP-0001', ano=2026, sequencial=1, quantidade=7,
        )

        self.client.post(
            reverse('moda:pedido-conferencia-forcar', args=[pedido.pk])
        )

        self.assertEqual(OrdemProducao.objects.count(), 1)
        self.assertEqual(Expedicao.objects.count(), 1)

    def test_o_aviso_na_tela_diz_que_foi_por_cima(self):
        pedido = self._pedido_travado()

        resposta = self.client.post(
            reverse('moda:pedido-conferencia-forcar', args=[pedido.pk]),
            follow=True,
        )

        mensagens = [str(m) for m in resposta.context['messages']]
        self.assertTrue(
            any('por cima' in m for m in mensagens),
            f'esperava aviso de desvio, veio: {mensagens}',
        )
