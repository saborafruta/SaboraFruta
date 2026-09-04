"""
Conferir para entrega: o QR, a contagem por quantidade e a assinatura.

O que estes testes cercam:

  · O BOTÃO ABRE O QR. Quem está no pedido está no computador; quem confere
    está de pé ao lado da caixa. Se o botão voltar a mandar direto para a
    lista, a conferência abre no aparelho errado e a pessoa marca de
    memória depois -- que é o mesmo que não conferir;

  · CONFERIR POR QUANTIDADE existe para todo pedido. Antes só havia a
    conferência por pessoa, e pedido sem personalização -- a maioria -- caía
    numa tela que dizia "não tem personalização", como se não houvesse o que
    conferir;

  · O TAMANHO CONFERIDO É DA GRADE DESTE PEDIDO. O `name` do campo vem do
    HTML, e um `qtd_999` forjado gravaria conferência de tamanho que não
    está nesta caixa;

  · A ASSINATURA É IMAGEM E NÃO AVANÇA STATUS. Assinar a conferência é dizer
    "conferi isto junto com vocês"; despachar e entregar continuam sendo
    atos da expedição. Emendar as duas coisas marcaria como entregue uma
    caixa que ainda não saiu.
"""
import base64
import shutil
import tempfile
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import DomainError
from apps.moda.models import (
    Expedicao, ItemGradePedido, ItemPedidoProducao, OrdemProducao,
    PedidoProducao, PersonalizacaoIndividual, ProdutoModa, Tamanho,
)
from apps.moda.services import ExpedicaoService, OrdemProducaoService

# 1x1 PNG transparente — o menor traço possível, só para provar o caminho.
PNG = (
    'data:image/png;base64,'
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='
)

MEDIA = tempfile.mkdtemp(prefix='moda-assinatura-')


@override_settings(MEDIA_ROOT=MEDIA)
class ConferenciaBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao Entrega LTDA', nome_fantasia='Entrega',
            cnpj='63345678000191', segmento='moda_confeccao',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='63345678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='DIEGO MACEDO',
            cpf_cnpj='12345678901', ativo=True,
        )
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='chefe@entrega.local', nome='Chefe', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        self.client.force_login(self.usuario)

    def _pedido_com_grade(self):
        """20 peças em três tamanhos — o pedido do print."""
        produto = ProdutoModa.objects.create(
            filial=self.filial, codigo='CAM001', nome='Camisa',
        )
        pedido = PedidoProducao.objects.create(
            filial=self.filial, cliente=self.cliente, numero=12,
            status=PedidoProducao.Status.PRONTO,
            data_prevista_entrega=timezone.localdate() + timedelta(days=1),
        )
        item = ItemPedidoProducao.objects.create(
            pedido=pedido, produto=produto, descricao='Camisa',
            quantidade=20, valor_unitario=Decimal('50'),
        )
        self.tamanhos = {}
        for ordem, (sigla, quantidade) in enumerate(
            (('P', 3), ('M', 10), ('G', 7)), start=1
        ):
            tamanho = Tamanho.objects.create(
                filial=self.filial, sigla=sigla, ordem=ordem,
            )
            self.tamanhos[sigla] = tamanho
            ItemGradePedido.objects.create(
                item=item, tamanho=tamanho, quantidade=quantidade,
            )
        self.item = item
        return pedido

    def _expedicao(self, pedido, item=None):
        """A ordem sai pelo serviço: numeração e etapas nascem lá dentro."""
        ordens = OrdemProducaoService.gerar_do_pedido(
            pedido, usuario=self.usuario, forcar=True,
        )
        alvo = item or self.item
        ordem = next(o for o in ordens if o.item_id == alvo.pk)
        return ExpedicaoService.criar(self.filial, ordem, self.usuario, forcar=True)


class FluxoConferenciaEQrTests(ConferenciaBase):
    """A conferência abre direto; o QR fica em ação própria da OP."""

    def test_cria_expedicao_para_item_que_faltava(self):
        pedido = self._pedido_com_grade()
        outro_item = ItemPedidoProducao.objects.create(
            pedido=pedido, descricao='Oversized', quantidade=1,
            valor_unitario=Decimal('30'), ordem=2,
        )
        primeira = self._expedicao(pedido, self.item)
        OrdemProducao.objects.filter(item=outro_item).delete()

        resposta = self.client.get(
            reverse('moda:pedido-conferencia', args=[pedido.pk])
        )

        self.assertRedirects(
            resposta, reverse('moda:conferencia-pessoas', args=[primeira.pk])
        )
        self.assertEqual(
            Expedicao.objects.filter(ordem__pedido=pedido)
            .exclude(status=Expedicao.Status.CANCELADA).count(),
            2,
        )
        self.assertTrue(
            Expedicao.objects.filter(ordem__item=outro_item).exists()
        )

    def test_o_botao_abre_direto_a_conferencia(self):
        pedido = self._pedido_com_grade()
        expedicao = self._expedicao(pedido)

        resposta = self.client.get(reverse('moda:pedido-conferencia', args=[pedido.pk]))

        self.assertRedirects(resposta, reverse('moda:conferencia-pessoas', args=[expedicao.pk]))

    def test_varios_itens_abrem_a_conferencia_da_propria_op(self):
        pedido = self._pedido_com_grade()
        outro_item = ItemPedidoProducao.objects.create(
            pedido=pedido, descricao='Oversized', quantidade=1,
            valor_unitario=Decimal('30'), ordem=2,
        )
        primeira = self._expedicao(pedido, self.item)
        outra_ordem = OrdemProducao.objects.get(pedido=pedido, item=outro_item)
        ExpedicaoService.criar(self.filial, outra_ordem, self.usuario, forcar=True)

        resposta = self.client.get(
            reverse('moda:pedido-conferencia', args=[pedido.pk])
        )

        self.assertRedirects(
            resposta, reverse('moda:conferencia-pessoas', args=[primeira.pk])
        )

    def test_qr_e_impresso_em_tela_separada(self):
        """
        Quem já está no computador certo não pode ficar sem saída: o QR é
        para o celular, e o botão é para quem confere ali mesmo.
        """
        pedido = self._pedido_com_grade()
        expedicao = self._expedicao(pedido)

        resposta = self.client.get(
            reverse('moda:pedido-conferencia-qr', args=[pedido.pk])
        )

        self.assertContains(
            resposta, reverse('moda:conferencia-pessoas', args=[expedicao.pk])
        )

    def test_varias_caixas_mostram_um_qr_cada(self):
        """
        Escolher por conta própria mandaria conferir a caixa errada.
        """
        pedido = self._pedido_com_grade()
        ItemPedidoProducao.objects.create(
            pedido=pedido, descricao='Short', quantidade=5,
            valor_unitario=Decimal('30'),
        )
        primeira = self._expedicao(pedido)
        self.segunda_ordem = next(
            o for o in OrdemProducao.objects.filter(pedido=pedido)
            if o.item_id != self.item.pk
        )

        segunda = ExpedicaoService.criar(
            self.filial, self.segunda_ordem, self.usuario, forcar=True,
        )

        resposta = self.client.get(reverse('moda:pedido-conferencia-qr', args=[pedido.pk]))

        self.assertContains(resposta, primeira.codigo)
        self.assertContains(resposta, segunda.codigo)
        self.assertContains(resposta, 'Camisa')
        self.assertContains(resposta, 'Short')

    def test_sem_expedicao_abre_conferencia_direto(self):
        pedido = self._pedido_com_grade()

        resposta = self.client.get(
            reverse('moda:pedido-conferencia', args=[pedido.pk])
        )

        expedicao = Expedicao.objects.get(ordem__pedido=pedido)
        self.assertRedirects(
            resposta, reverse('moda:conferencia-pessoas', args=[expedicao.pk])
        )


class OrdemDeVariosProdutosTests(ConferenciaBase):
    """
    Achado ao montar a tela das várias caixas: pedido com DOIS produtos não
    emitia ordem nenhuma.

    `bulk_create` não chama `save()`, então o `codigo_qr` -- que nasce lá --
    ficava vazio em todas. Com um produto só passava despercebido; no
    segundo, a coluna é única e a emissão inteira estourava com
    `UNIQUE constraint failed`. O número já era montado à mão por esse mesmo
    motivo; o código do QR tinha ficado para trás.
    """

    def test_cada_ordem_nasce_com_o_seu_codigo_de_qr(self):
        pedido = self._pedido_com_grade()
        ItemPedidoProducao.objects.create(
            pedido=pedido, descricao='Short', quantidade=5,
            valor_unitario=Decimal('30'),
        )

        ordens = OrdemProducaoService.gerar_do_pedido(
            pedido, usuario=self.usuario, forcar=True,
        )

        self.assertEqual(len(ordens), 2)
        codigos = {o.codigo_qr for o in ordens}
        self.assertEqual(len(codigos), 2)
        self.assertNotIn('', codigos)


class ConferirPorQuantidadeTests(ConferenciaBase):
    """A conferência que todo pedido tem, com ou sem personalização."""

    def test_a_tela_lista_os_tamanhos_do_pedido(self):
        pedido = self._pedido_com_grade()
        expedicao = self._expedicao(pedido)

        resposta = self.client.get(
            reverse('moda:conferencia-pessoas', args=[expedicao.pk])
        )

        self.assertEqual(resposta.status_code, 200)
        for sigla, tamanho in self.tamanhos.items():
            self.assertContains(resposta, f'qtd_{tamanho.pk}')
        self.assertContains(resposta, 'Conferência completa da OP')

    def test_nao_lista_tamanho_sem_peca_pedida(self):
        pedido = self._pedido_com_grade()
        sem_peca = Tamanho.objects.create(filial=self.filial, sigla='XG', ordem=9)
        ItemGradePedido.objects.create(item=self.item, tamanho=sem_peca, quantidade=0)
        expedicao = self._expedicao(pedido)

        resposta = self.client.get(reverse('moda:conferencia-pessoas', args=[expedicao.pk]))

        self.assertNotContains(resposta, f'qtd_{sem_peca.pk}')

    def test_pedido_sem_pessoas_nao_cai_mais_em_tela_vazia(self):
        pedido = self._pedido_com_grade()
        expedicao = self._expedicao(pedido)

        resposta = self.client.get(
            reverse('moda:conferencia-pessoas', args=[expedicao.pk])
        )

        self.assertNotContains(
            resposta, 'Este pedido não tem personalização por pessoa'
        )

    def test_a_contagem_e_gravada_por_tamanho(self):
        pedido = self._pedido_com_grade()
        expedicao = self._expedicao(pedido)

        self.client.post(
            reverse('moda:conferencia-pessoas-salvar', args=[expedicao.pk]),
            {
                f'qtd_{self.tamanhos["P"].pk}': '3',
                f'qtd_{self.tamanhos["M"].pk}': '10',
                f'qtd_{self.tamanhos["G"].pk}': '7',
            },
        )

        expedicao.refresh_from_db()
        self.assertEqual(expedicao.quantidade_conferida, 20)
        self.assertTrue(expedicao.conferencia_fecha)

    def test_o_que_falta_vira_divergencia_e_nao_erro(self):
        """
        Contar menos do que o pedido é um FATO da caixa, não um formulário
        inválido. Recusar a gravação mandaria a pessoa mentir o número para
        conseguir salvar.
        """
        pedido = self._pedido_com_grade()
        expedicao = self._expedicao(pedido)

        self.client.post(
            reverse('moda:conferencia-pessoas-salvar', args=[expedicao.pk]),
            {
                f'qtd_{self.tamanhos["P"].pk}': '1',
                f'qtd_{self.tamanhos["M"].pk}': '10',
                f'qtd_{self.tamanhos["G"].pk}': '7',
            },
        )

        expedicao.refresh_from_db()
        self.assertEqual(expedicao.divergencia_conferencia, 2)
        divergencias = {d['tamanho'].sigla: d for d in expedicao.divergencias_por_tamanho}
        self.assertEqual(divergencias['P']['conferido'], 1)
        self.assertEqual(divergencias['P']['esperado'], 3)

    def test_tamanho_de_fora_da_grade_e_ignorado(self):
        pedido = self._pedido_com_grade()
        expedicao = self._expedicao(pedido)
        intruso = Tamanho.objects.create(filial=self.filial, sigla='XG', ordem=9)

        self.client.post(
            reverse('moda:conferencia-pessoas-salvar', args=[expedicao.pk]),
            {
                f'qtd_{self.tamanhos["P"].pk}': '3',
                f'qtd_{intruso.pk}': '99',
            },
        )

        expedicao.refresh_from_db()
        gravados = {i.tamanho_id for i in expedicao.conferencia.all()}
        self.assertIn(self.tamanhos['P'].pk, gravados)
        self.assertNotIn(intruso.pk, gravados)


class PersonalizacaoDaCaixaTests(ConferenciaBase):
    """Cada expedição mostra somente as peças nominais do próprio produto."""

    def test_conferencia_com_outro_item_da_op_nao_gera_erro(self):
        pedido = self._pedido_com_grade()
        outro_item = ItemPedidoProducao.objects.create(
            pedido=pedido, descricao='Short', quantidade=1,
            valor_unitario=Decimal('30'), ordem=2,
        )
        expedicao = self._expedicao(pedido, self.item)
        outra_ordem = OrdemProducao.objects.get(pedido=pedido, item=outro_item)
        ExpedicaoService.criar(self.filial, outra_ordem, self.usuario, forcar=True)

        resposta = self.client.get(
            reverse('moda:conferencia-pessoas', args=[expedicao.pk])
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Conferência completa da OP')
        self.assertContains(resposta, 'Short')

    def test_todos_os_itens_grades_e_pessoas_ficam_na_mesma_tabela(self):
        pedido = self._pedido_com_grade()
        xgg = Tamanho.objects.create(filial=self.filial, sigla='XGG', ordem=9)
        outro_item = ItemPedidoProducao.objects.create(
            pedido=pedido, descricao='Oversized', quantidade=1,
            valor_unitario=Decimal('30'), ordem=2,
        )
        ItemGradePedido.objects.create(item=outro_item, tamanho=xgg, quantidade=1)
        pessoa_pp = PersonalizacaoIndividual.objects.create(
            pedido=pedido, item=self.item, tamanho=self.tamanhos['P'],
            nome='EDUARDO', numero='1',
        )
        pessoa_xgg = PersonalizacaoIndividual.objects.create(
            pedido=pedido, item=outro_item, tamanho=xgg,
            nome='COMPLEMENTO', numero='7',
        )
        primeira = self._expedicao(pedido, self.item)
        outra_ordem = OrdemProducao.objects.get(pedido=pedido, item=outro_item)
        segunda = ExpedicaoService.criar(
            self.filial, outra_ordem, self.usuario, forcar=True,
        )

        resposta = self.client.get(
            reverse('moda:conferencia-pessoas', args=[primeira.pk])
        )

        self.assertContains(resposta, 'Camisa')
        self.assertContains(resposta, 'Oversized')
        self.assertContains(resposta, f'qtd_{primeira.pk}_{self.tamanhos["P"].pk}')
        self.assertContains(resposta, f'qtd_{segunda.pk}_{xgg.pk}')
        self.assertContains(resposta, 'EDUARDO')
        self.assertContains(resposta, 'COMPLEMENTO')
        self.assertContains(resposta, f'pessoa_{primeira.pk}')
        self.assertContains(resposta, f'pessoa_{segunda.pk}')
        self.assertNotContains(resposta, 'Outros itens desta OP para conferir')

        self.client.post(
            reverse('moda:conferencia-pessoas-salvar', args=[primeira.pk]),
            {
                f'qtd_{primeira.pk}_{self.tamanhos["P"].pk}': '3',
                f'qtd_{primeira.pk}_{self.tamanhos["M"].pk}': '10',
                f'qtd_{primeira.pk}_{self.tamanhos["G"].pk}': '7',
                f'qtd_{segunda.pk}_{xgg.pk}': '1',
                f'pessoa_{primeira.pk}': str(pessoa_pp.pk),
                f'pessoa_{segunda.pk}': str(pessoa_xgg.pk),
            },
        )
        primeira.refresh_from_db()
        segunda.refresh_from_db()
        self.assertEqual(primeira.quantidade_conferida, 20)
        self.assertEqual(segunda.quantidade_conferida, 1)
        self.assertEqual(primeira.conferencia_pessoas.count(), 1)
        self.assertEqual(segunda.conferencia_pessoas.count(), 1)

    def test_conferencia_nao_mistura_personalizacoes_de_outro_produto(self):
        pedido = self._pedido_com_grade()
        outro_item = ItemPedidoProducao.objects.create(
            pedido=pedido, descricao='Short', quantidade=1,
            valor_unitario=Decimal('30'), ordem=2,
        )
        PersonalizacaoIndividual.objects.create(
            pedido=pedido, item=self.item, tamanho=self.tamanhos['M'],
            nome='SOMENTE CAMISA', numero='10',
        )
        PersonalizacaoIndividual.objects.create(
            pedido=pedido, item=outro_item, tamanho=self.tamanhos['M'],
            nome='SOMENTE SHORT', numero='20',
        )
        expedicao = self._expedicao(pedido, self.item)

        resposta = self.client.get(
            reverse('moda:conferencia-pessoas', args=[expedicao.pk])
        )

        self.assertContains(resposta, 'SOMENTE CAMISA')
        self.assertNotContains(resposta, 'SOMENTE SHORT')
        self.assertEqual(resposta.context['total'], 1)

    def test_personalizacoes_saem_do_menor_tamanho_para_o_maior(self):
        pedido = self._pedido_com_grade()
        pp = Tamanho.objects.create(
            filial=self.filial, sigla='PP', ordem=0,
        )
        xgg = Tamanho.objects.create(
            filial=self.filial, sigla='XGG', ordem=9,
        )
        for tamanho, nome in (
            (xgg, 'PESSOA XGG'),
            (self.tamanhos['G'], 'PESSOA G'),
            (self.tamanhos['P'], 'PESSOA P'),
            (pp, 'PESSOA PP'),
        ):
            PersonalizacaoIndividual.objects.create(
                pedido=pedido, item=self.item, tamanho=tamanho, nome=nome,
            )
        expedicao = self._expedicao(pedido)

        resposta = self.client.get(
            reverse('moda:conferencia-pessoas', args=[expedicao.pk])
        )

        tamanhos = [linha['pessoa'].tamanho.sigla for linha in resposta.context['linhas']]
        self.assertEqual(tamanhos, ['PP', 'P', 'G', 'XGG'])

    def test_personalizacao_fica_na_mesma_linha_do_tamanho(self):
        pedido = self._pedido_com_grade()
        PersonalizacaoIndividual.objects.create(
            pedido=pedido, item=self.item, tamanho=self.tamanhos['M'], nome='ALINHADA',
        )
        expedicao = self._expedicao(pedido)

        resposta = self.client.get(
            reverse('moda:conferencia-pessoas', args=[expedicao.pk])
        )

        self.assertContains(resposta, 'Nome e número')
        self.assertContains(resposta, 'ALINHADA')
        self.assertContains(
            resposta, f'id="pessoa-{expedicao.pk}-{pedido.individuais.get().pk}"'
        )


class ArteNaConferenciaTests(ConferenciaBase):
    """
    A arte ao lado da contagem.

    Sem ela a conferência é só aritmética: o número fecha e ninguém percebeu
    que o escudo saiu na manga errada. Quem confere está com a peça na mão --
    o que faltava na tela era o desenho ao lado dela.
    """

    def _anexar_arte(self, pedido):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from apps.moda.models import ArquivoPedido

        return ArquivoPedido.objects.create(
            pedido=pedido, tipo=ArquivoPedido.Tipo.ARTE,
            descricao='Escudo em curva',
            arquivo=SimpleUploadedFile(
                'escudo.png', base64.b64decode(PNG.split(',', 1)[1]),
                content_type='image/png',
            ),
        )

    def test_a_arte_do_pedido_aparece_na_conferencia(self):
        pedido = self._pedido_com_grade()
        arte = self._anexar_arte(pedido)
        expedicao = self._expedicao(pedido)

        resposta = self.client.get(
            reverse('moda:conferencia-pessoas', args=[expedicao.pk])
        )

        self.assertContains(resposta, 'Arte do pedido')
        self.assertContains(resposta, 'Escudo em curva')
        self.assertContains(resposta, arte.arquivo.url)

    def test_a_arte_aplicada_no_item_tambem_aparece(self):
        """
        As duas origens existem: o acervo do pedido e a arte APLICADA no
        item, que é a que diz técnica e local. Mostrar só uma faria a tela
        parecer vazia justamente nos pedidos em que a outra foi usada.
        """
        from django.core.files.uploadedfile import SimpleUploadedFile

        from apps.moda.models import Personalizacao

        pedido = self._pedido_com_grade()
        Personalizacao.objects.create(
            item=self.item, tecnica=Personalizacao.Tecnica.SILK, local='Peito',
            arquivo=SimpleUploadedFile(
                'aplicada.png', base64.b64decode(PNG.split(',', 1)[1]),
                content_type='image/png',
            ),
        )
        expedicao = self._expedicao(pedido)

        resposta = self.client.get(
            reverse('moda:conferencia-pessoas', args=[expedicao.pk])
        )

        self.assertContains(resposta, 'aplicada')

    def test_arte_aplicada_em_outro_produto_nao_aparece(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from apps.moda.models import Personalizacao

        pedido = self._pedido_com_grade()
        outro_item = ItemPedidoProducao.objects.create(
            pedido=pedido, descricao='Short', quantidade=1,
            valor_unitario=Decimal('30'), ordem=2,
        )
        arte_da_camisa = Personalizacao.objects.create(
            item=self.item, tecnica=Personalizacao.Tecnica.SILK, local='Peito',
            arquivo=SimpleUploadedFile(
                'somente-camisa.png', base64.b64decode(PNG.split(',', 1)[1]),
                content_type='image/png',
            ),
        )
        arte_do_short = Personalizacao.objects.create(
            item=outro_item, tecnica=Personalizacao.Tecnica.DTF, local='Perna',
            arquivo=SimpleUploadedFile(
                'somente-short.png', base64.b64decode(PNG.split(',', 1)[1]),
                content_type='image/png',
            ),
        )
        expedicao = self._expedicao(pedido, self.item)

        resposta = self.client.get(
            reverse('moda:conferencia-pessoas', args=[expedicao.pk])
        )

        self.assertContains(resposta, arte_da_camisa.arquivo.url)
        self.assertNotContains(resposta, arte_do_short.arquivo.url)

    def test_sem_arte_a_tela_diz_que_nao_ha(self):
        """
        A ausência é informação: uma caixa vazia pareceria defeito da tela,
        e quem confere não saberia se procura a arte ou segue sem ela.
        """
        pedido = self._pedido_com_grade()
        expedicao = self._expedicao(pedido)

        resposta = self.client.get(
            reverse('moda:conferencia-pessoas', args=[expedicao.pk])
        )

        self.assertContains(resposta, 'Nenhuma arte anexada a este pedido')


class AssinaturaTests(ConferenciaBase):
    """O traço do cliente, colhido na própria conferência."""

    def test_o_traco_e_gravado_como_imagem(self):
        pedido = self._pedido_com_grade()
        expedicao = self._expedicao(pedido)

        self.client.post(
            reverse('moda:conferencia-pessoas-salvar', args=[expedicao.pk]),
            {
                f'qtd_{self.tamanhos["P"].pk}': '3',
                'recebido_por': 'Diego Macedo',
                'assinado_documento': '123.456.789-01',
                'assinatura': PNG,
            },
        )

        expedicao.refresh_from_db()
        self.assertEqual(expedicao.recebido_por, 'Diego Macedo')
        self.assertEqual(expedicao.assinado_documento, '123.456.789-01')
        self.assertTrue(expedicao.assinado)
        self.assertIsNotNone(expedicao.assinado_em)
        # O arquivo existe de verdade, e é o PNG que veio da tela.
        with expedicao.assinatura.open('rb') as arquivo:
            self.assertEqual(arquivo.read(), base64.b64decode(PNG.split(',', 1)[1]))

    def test_a_assinatura_nao_avanca_o_status_da_expedicao(self):
        """
        Assinar a conferência não é despachar nem entregar. Emendar as duas
        coisas marcaria como entregue uma caixa que ainda está na bancada.
        """
        pedido = self._pedido_com_grade()
        expedicao = self._expedicao(pedido)

        self.client.post(
            reverse('moda:conferencia-pessoas-salvar', args=[expedicao.pk]),
            {
                f'qtd_{self.tamanhos["P"].pk}': '3',
                'recebido_por': 'Diego Macedo',
                'assinatura': PNG,
            },
        )

        expedicao.refresh_from_db()
        self.assertNotEqual(expedicao.status, Expedicao.Status.ENTREGA)
        self.assertIsNone(expedicao.data_entrega)

    def test_nome_sem_traco_vale(self):
        """
        Em entrega no balcão o cliente às vezes só confere e diz o nome.
        Recusar isso empurraria a pessoa a rabiscar qualquer coisa para o
        botão liberar -- pior registro do que um nome honesto.
        """
        pedido = self._pedido_com_grade()
        expedicao = self._expedicao(pedido)

        self.client.post(
            reverse('moda:conferencia-pessoas-salvar', args=[expedicao.pk]),
            {f'qtd_{self.tamanhos["P"].pk}': '3', 'recebido_por': 'Diego Macedo'},
        )

        expedicao.refresh_from_db()
        self.assertEqual(expedicao.recebido_por, 'Diego Macedo')
        self.assertFalse(expedicao.assinado)

    def test_traco_sem_nome_e_recusado(self):
        pedido = self._pedido_com_grade()
        expedicao = self._expedicao(pedido)

        with self.assertRaises(DomainError):
            ExpedicaoService.assinar(expedicao, '', PNG)

    def test_formato_estranho_e_recusado(self):
        """
        O campo chega do navegador. Aceitar qualquer texto guardaria no
        storage um arquivo que não é imagem nenhuma.
        """
        pedido = self._pedido_com_grade()
        expedicao = self._expedicao(pedido)

        with self.assertRaises(DomainError):
            ExpedicaoService.assinar(expedicao, 'Diego', 'javascript:alert(1)')

    def test_assinatura_grande_demais_e_recusada(self):
        """
        O teto existe para o caso de alguém mandar uma FOTO no lugar do
        traço, que entraria no banco pela porta do formulário.
        """
        pedido = self._pedido_com_grade()
        expedicao = self._expedicao(pedido)
        gigante = 'data:image/png;base64,' + base64.b64encode(
            b'x' * (ExpedicaoService.LIMITE_ASSINATURA + 10)
        ).decode()

        with self.assertRaises(DomainError):
            ExpedicaoService.assinar(expedicao, 'Diego', gigante)

    def test_a_tela_mostra_a_assinatura_ja_colhida(self):
        """
        Quem chega depois precisa ver que a caixa já passou -- senão confere
        de novo e a segunda passada apaga a primeira.
        """
        pedido = self._pedido_com_grade()
        expedicao = self._expedicao(pedido)
        ExpedicaoService.assinar(expedicao, 'Diego Macedo', PNG)

        resposta = self.client.get(
            reverse('moda:conferencia-pessoas', args=[expedicao.pk])
        )

        self.assertContains(resposta, 'Recebimento assinado por')
        self.assertContains(resposta, 'Diego Macedo')
