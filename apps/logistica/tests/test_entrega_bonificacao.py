"""
A entrega da bonificação, e a prova de que ela chegou.

A PERGUNTA QUE NINGUÉM FAZ SOZINHO. Mercadoria vendida cobra a si mesma: se
não chegar, o cliente liga. A bonificação não — ninguém pagou, ninguém
reclama, e ela é exatamente a que some no caminho sem que nada acuse.

O QUE ESTES TESTES CERCAM:

  · AS TRANSIÇÕES SÃO EXPLÍCITAS. Sem elas alguém marca "entregue" numa
    bonificação que voltou, e o controle deixa de significar coisa alguma;

  · RECUSADA NÃO É O FIM: a mercadoria está no caminhão e precisa voltar —
    daí "retorno pendente", o único estado que cobra ação de alguém;

  · ENTREGUE EXIGE QUEM RECEBEU. "Entregue: sim" não prova nada, e é este
    registro que a auditoria vai ler;

  · A QUANTIDADE PODE SER MENOR que a prometida (o cliente aceita 15 das
    20) — mas nunca maior, porque não se entrega o que não saiu;

  · A PROVA É ANEXO, e vários por entrega: a doca fotografa a caixa, o
    canhoto e às vezes a porta;

  · O SERVIÇO NÃO MEXE EM ESTOQUE. "Retornada" registra o fato; devolver ao
    saldo é o caminho que já existe, e fazê-lo aqui também seria a mesma
    caixa voltando duas vezes.
"""
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from apps.cadastros.models import Cliente, ClienteFilial
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import DadosInvalidosError
from apps.estoque.models import MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.fiscal.models import NaturezaOperacao, RegraNaturezaOperacao
from apps.logistica.models import (
    ComprovanteBonificacao, EntregaBonificacao, VendaViagem, Viagem,
)
from apps.logistica.services.entrega_bonificacao import (
    EntregaBonificacaoService,
)
from apps.logistica.services.venda_viagem import VendaViagemService
from apps.logistica.services.viagem import ViagemService
from apps.produtos.models import Produto, ProdutoFilial
from apps.produtos.models.unidade import UnidadeMedida, UnidadeMedidaFilial

ZERO = Decimal('0')
S = EntregaBonificacao.Status
TIPO = ComprovanteBonificacao.Tipo


class EntregaBonificacaoBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Entrega Brinde LTDA', nome_fantasia='Entrega',
            cnpj='81345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='81345678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='UN', descricao='Unidade',
            tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='doca@entrega.local', nome='Doca', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Mercado da Esquina',
            cpf_cnpj='12345678901', uf='RN', cidade='Natal',
        )
        ClienteFilial.objects.create(cliente=cls.cliente, filial=cls.filial)
        cls.bonificacao = NaturezaOperacao.objects.create(
            filial=cls.filial, codigo='bonif', descricao='Bonificação',
            especie=NaturezaOperacao.Especie.BONIFICACAO,
        )
        RegraNaturezaOperacao.objects.create(natureza=cls.bonificacao, cfop='5910')
        cls.remessa = NaturezaOperacao.objects.create(
            filial=cls.filial, codigo='remessa', descricao='Remessa',
            especie=NaturezaOperacao.Especie.REMESSA_VENDA_FORA,
            exige_destinatario=False,
        )
        RegraNaturezaOperacao.objects.create(natureza=cls.remessa, cfop='5904')

    def setUp(self):
        self.client.force_login(self.usuario)
        self.produto = self._produto('P1', '1000')

    # ── Fixtures ─────────────────────────────────────────────────────────

    def _produto(self, codigo, saldo):
        produto = Produto.objects.create(
            filial=self.filial, unidade_medida=self.unidade,
            descricao=f'Produto {codigo}', codigo=codigo, ncm='20079900',
            controla_lote=False, preco_venda=Decimal('10'),
            preco_custo=Decimal('4'),
        )
        ProdutoFilial.objects.create(produto=produto, filial=self.filial)
        MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk, filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal(saldo), usuario_id=self.usuario.pk,
            documento_tipo=MovimentacaoEstoque.DocumentoTipo.OUTRAS,
        )
        return produto

    def _viagem(self):
        return Viagem.objects.create(
            filial=self.filial, numero=Viagem.objects.count() + 1,
            motorista_nome='Seu Zé', veiculo_placa='ABC1D23',
            vendedor=self.usuario, responsavel=self.usuario,
        )

    def _bonificacao_na_carga(self, quantidade='20'):
        viagem = self._viagem()
        item = ViagemService.adicionar_item(viagem, {
            'natureza': self.bonificacao, 'produto': self.produto,
            'cliente': self.cliente, 'quantidade': quantidade,
            'valor_unitario': '10',
        })
        ViagemService.fechar_carga(viagem, usuario=self.usuario)
        return viagem, EntregaBonificacaoService.para_item(item)

    def _bonificacao_na_rua(self, quantidade='20'):
        viagem = self._viagem()
        ViagemService.adicionar_item(viagem, {
            'natureza': self.remessa, 'produto': self.produto,
            'quantidade': '300', 'valor_unitario': '10',
        })
        ViagemService.fechar_carga(viagem, usuario=self.usuario)
        viagem.status = Viagem.Status.EM_VENDAS
        viagem.save(update_fields=['status'])
        entrega_rua = VendaViagemService.registrar(viagem, {
            'tipo': VendaViagem.Tipo.BONIFICACAO, 'produto': self.produto,
            'quantidade': quantidade, 'valor_unitario': '10',
            'cliente': self.cliente, 'motivo': VendaViagem.Motivo.BRINDE,
        }, usuario=self.usuario)
        return viagem, EntregaBonificacaoService.para_entrega_da_rua(entrega_rua)

    def _arquivo(self, nome='canhoto.png'):
        return SimpleUploadedFile(nome, b'imagem-de-teste', content_type='image/png')


class CicloTests(EntregaBonificacaoBase):
    """As sete situações e os caminhos entre elas."""

    def test_a_bonificacao_da_carga_nasce_pendente(self):
        """
        Enquanto ninguém marca, ela fica visível: é a cortesia que some sem
        que nada acuse.
        """
        _, entrega = self._bonificacao_na_carga()

        self.assertEqual(entrega.status, S.PENDENTE)
        self.assertTrue(entrega.aberta)

    def test_as_sete_situacoes_da_especificacao_existem(self):
        self.assertEqual(
            [v for v, _ in S.choices],
            [
                'pendente', 'em_transporte', 'entregue', 'recusada',
                'cancelada', 'retorno_pendente', 'retornada',
            ],
        )

    def test_o_caminho_normal_ate_a_entrega(self):
        _, entrega = self._bonificacao_na_carga()

        EntregaBonificacaoService.mover(entrega, S.EM_TRANSPORTE)
        EntregaBonificacaoService.entregar(entrega, {
            'destinatario_nome': 'Maria', 'quantidade_entregue': '20',
        }, self.usuario)

        entrega.refresh_from_db()
        self.assertEqual(entrega.status, S.ENTREGUE)

    def test_recusada_so_vai_para_retorno_pendente(self):
        """A mercadoria está no caminhão: ela precisa voltar."""
        _, entrega = self._bonificacao_na_carga()
        EntregaBonificacaoService.mover(entrega, S.EM_TRANSPORTE)
        EntregaBonificacaoService.mover(entrega, S.RECUSADA, {
            'motivo_nao_entrega': EntregaBonificacao.MotivoNaoEntrega.AUSENTE,
        })

        self.assertEqual(
            [v for v, _ in entrega.proximos], [S.RETORNO_PENDENTE],
        )

        EntregaBonificacaoService.mover(entrega, S.RETORNO_PENDENTE)
        EntregaBonificacaoService.mover(entrega, S.RETORNADA)

        entrega.refresh_from_db()
        self.assertEqual(entrega.status, S.RETORNADA)

    def test_entregue_nao_volta_a_ser_pendente(self):
        """
        Sem transições, alguém marca "pendente" numa entrega feita e o
        controle deixa de significar coisa alguma.
        """
        _, entrega = self._bonificacao_na_carga()
        EntregaBonificacaoService.entregar(entrega, {
            'destinatario_nome': 'Maria', 'quantidade_entregue': '20',
        }, self.usuario)

        with self.assertRaises(DadosInvalidosError):
            EntregaBonificacaoService.mover(entrega, S.EM_TRANSPORTE)

    def test_uma_bonificacao_retornada_nao_e_entregue_depois(self):
        _, entrega = self._bonificacao_na_carga()
        EntregaBonificacaoService.mover(entrega, S.EM_TRANSPORTE)
        EntregaBonificacaoService.mover(entrega, S.RECUSADA, {
            'motivo_nao_entrega': EntregaBonificacao.MotivoNaoEntrega.RECUSOU,
        })
        EntregaBonificacaoService.mover(entrega, S.RETORNO_PENDENTE)
        EntregaBonificacaoService.mover(entrega, S.RETORNADA, usuario=self.usuario)

        with self.assertRaises(DadosInvalidosError):
            EntregaBonificacaoService.entregar(entrega, {
                'destinatario_nome': 'Maria', 'quantidade_entregue': '20',
            }, self.usuario)

    def test_situacao_desconhecida_e_recusada(self):
        _, entrega = self._bonificacao_na_carga()

        with self.assertRaises(DadosInvalidosError):
            EntregaBonificacaoService.mover(entrega, 'extraviada')

    def test_a_bonificacao_da_rua_nasce_entregue(self):
        """
        O vendedor estava com o cliente na frente — marcá-la pendente
        descreveria errado o que aconteceu.
        """
        _, entrega = self._bonificacao_na_rua('20')

        self.assertEqual(entrega.status, S.ENTREGUE)
        self.assertEqual(entrega.destinatario_nome, 'Mercado da Esquina')
        self.assertEqual(entrega.quantidade_entregue, Decimal('20.000'))


class RegistroDaEntregaTests(EntregaBonificacaoBase):
    """O que a entrega precisa registrar."""

    def test_entregue_guarda_quem_quanto_quando_e_por_quem(self):
        _, entrega = self._bonificacao_na_carga()

        EntregaBonificacaoService.entregar(entrega, {
            'destinatario_nome': 'Maria Souza', 'destinatario_documento': '123',
            'quantidade_entregue': '20', 'observacao': 'Deixado na portaria.',
        }, self.usuario)

        entrega.refresh_from_db()
        self.assertEqual(entrega.destinatario_nome, 'Maria Souza')
        self.assertEqual(entrega.destinatario_documento, '123')
        self.assertEqual(entrega.quantidade_entregue, Decimal('20.000'))
        self.assertEqual(entrega.entregue_por, self.usuario)
        self.assertIsNotNone(entrega.entregue_em)
        self.assertIn('portaria', entrega.observacao)

    def test_sem_quem_recebeu_nao_marca(self):
        """"Entregue: sim" não prova nada."""
        _, entrega = self._bonificacao_na_carga()

        with self.assertRaises(DadosInvalidosError) as erro:
            EntregaBonificacaoService.entregar(entrega, {
                'destinatario_nome': '  ', 'quantidade_entregue': '20',
            }, self.usuario)

        self.assertIn('quem recebeu', str(erro.exception))
        entrega.refresh_from_db()
        self.assertEqual(entrega.status, S.PENDENTE)

    def test_sem_quantidade_nao_marca(self):
        _, entrega = self._bonificacao_na_carga()

        with self.assertRaises(DadosInvalidosError):
            EntregaBonificacaoService.entregar(entrega, {
                'destinatario_nome': 'Maria',
            }, self.usuario)

    def test_entrega_parcial_e_aceita_e_fica_visivel(self):
        """
        O cliente aceita 15 das 20 — assumir que chegou tudo esconderia a
        diferença que precisa voltar.
        """
        _, entrega = self._bonificacao_na_carga('20')

        EntregaBonificacaoService.entregar(entrega, {
            'destinatario_nome': 'Maria', 'quantidade_entregue': '15',
        }, self.usuario)

        pendencias = EntregaBonificacaoService.pendencias(entrega)
        self.assertTrue(any('15' in p and '20' in p for p in pendencias))

    def test_nao_se_entrega_mais_do_que_saiu(self):
        _, entrega = self._bonificacao_na_carga('20')

        with self.assertRaises(DadosInvalidosError):
            EntregaBonificacaoService.entregar(entrega, {
                'destinatario_nome': 'Maria', 'quantidade_entregue': '25',
            }, self.usuario)


class ComprovanteTests(EntregaBonificacaoBase):
    """A prova."""

    def test_os_quatro_tipos_da_especificacao_existem(self):
        self.assertEqual(
            [v for v, _ in TIPO.choices],
            ['foto', 'assinatura', 'comprovante', 'canhoto'],
        )

    def test_varios_comprovantes_na_mesma_entrega(self):
        """A doca fotografa a caixa, o canhoto e às vezes a porta."""
        _, entrega = self._bonificacao_na_carga()

        EntregaBonificacaoService.anexar(
            entrega, TIPO.FOTO, self._arquivo('caixa.png'), usuario=self.usuario,
        )
        EntregaBonificacaoService.anexar(
            entrega, TIPO.CANHOTO, self._arquivo('canhoto.png'),
            usuario=self.usuario,
        )

        self.assertEqual(entrega.comprovantes.count(), 2)
        self.assertTrue(entrega.tem_prova)

    def test_tipo_desconhecido_e_recusado(self):
        _, entrega = self._bonificacao_na_carga()

        with self.assertRaises(DadosInvalidosError):
            EntregaBonificacaoService.anexar(
                entrega, 'video', self._arquivo(), usuario=self.usuario,
            )

    def test_sem_arquivo_nao_anexa(self):
        _, entrega = self._bonificacao_na_carga()

        with self.assertRaises(DadosInvalidosError):
            EntregaBonificacaoService.anexar(
                entrega, TIPO.FOTO, None, usuario=self.usuario,
            )

    def test_entrega_sem_prova_aparece_como_pendencia(self):
        _, entrega = self._bonificacao_na_carga()
        EntregaBonificacaoService.entregar(entrega, {
            'destinatario_nome': 'Maria', 'quantidade_entregue': '20',
        }, self.usuario)

        pendencias = EntregaBonificacaoService.pendencias(entrega)

        self.assertTrue(any('comprovante' in p for p in pendencias))

    def test_a_rota_nao_para_por_falta_de_foto(self):
        """Exigir comprovante pararia a entrega num lugar sem sinal."""
        _, entrega = self._bonificacao_na_carga()

        EntregaBonificacaoService.entregar(entrega, {
            'destinatario_nome': 'Maria', 'quantidade_entregue': '20',
        }, self.usuario)

        entrega.refresh_from_db()
        self.assertEqual(entrega.status, S.ENTREGUE)


class EstoqueTests(EntregaBonificacaoBase):
    """
    O retorno devolve — e devolve no lugar certo.

    Antes de a seção do retorno existir, marcar RETORNADA não mexia em nada:
    o status dizia que a mercadoria voltou e ela seguia fora de qualquer
    saldo. Agora ela volta de verdade, e o que precisa ser cercado é o
    LUGAR: devolver no errado somaria estoque que nunca saiu de lá.
    """

    def test_o_estoque_so_muda_quando_o_retorno_e_tratado(self):
        from apps.estoque.models import Estoque

        _, entrega = self._bonificacao_na_carga('20')
        saldo = Estoque.objects.get(
            produto=self.produto, filial=self.filial,
        ).quantidade_atual

        EntregaBonificacaoService.mover(entrega, S.EM_TRANSPORTE)
        EntregaBonificacaoService.mover(entrega, S.RECUSADA, {
            'motivo_nao_entrega': EntregaBonificacao.MotivoNaoEntrega.AUSENTE,
        })
        EntregaBonificacaoService.mover(entrega, S.RETORNO_PENDENTE)

        # Recusada e aguardando retorno: a caixa esta' no caminhao, fora de
        # qualquer saldo -- e o estoque ainda nao mudou.
        atual = Estoque.objects.get(
            produto=self.produto, filial=self.filial,
        ).quantidade_atual
        self.assertEqual(atual, saldo)

        EntregaBonificacaoService.tratar_retorno(entrega, usuario=self.usuario)

        atual = Estoque.objects.get(
            produto=self.produto, filial=self.filial,
        ).quantidade_atual
        self.assertEqual(atual, saldo + Decimal('20'))


class TelaTests(EntregaBonificacaoBase):
    """As ações na tela da viagem."""

    def _detalhe(self, viagem):
        return self.client.get(
            reverse('logistica:viagem-detail', args=[viagem.pk]),
        ).content.decode()

    def test_a_tela_mostra_a_situacao_da_entrega(self):
        viagem, _ = self._bonificacao_na_carga()

        html = self._detalhe(viagem)

        self.assertIn('Pendente', html)
        self.assertIn('Quem recebeu', html)

    def test_marcar_entregue_pela_tela(self):
        viagem, entrega = self._bonificacao_na_carga('20')

        self.client.post(
            reverse('logistica:bonificacao-entrega', args=[viagem.pk, entrega.pk]),
            {
                'acao': 'entregar', 'destinatario_nome': 'Maria',
                'quantidade_entregue': '20',
            },
        )

        entrega.refresh_from_db()
        self.assertEqual(entrega.status, S.ENTREGUE)
        self.assertEqual(entrega.destinatario_nome, 'Maria')

    def test_anexar_comprovante_pela_tela(self):
        viagem, entrega = self._bonificacao_na_carga()

        self.client.post(
            reverse('logistica:bonificacao-entrega', args=[viagem.pk, entrega.pk]),
            {'acao': 'anexar', 'tipo': 'canhoto', 'arquivo': self._arquivo()},
        )

        self.assertEqual(entrega.comprovantes.count(), 1)

    def test_mudar_situacao_pela_tela(self):
        viagem, entrega = self._bonificacao_na_carga()

        self.client.post(
            reverse('logistica:bonificacao-entrega', args=[viagem.pk, entrega.pk]),
            {'acao': 'em_transporte'},
        )

        entrega.refresh_from_db()
        self.assertEqual(entrega.status, S.EM_TRANSPORTE)

    def test_a_entrega_de_outra_viagem_e_recusada(self):
        """
        Id colado à mão mexeria na bonificação de outra viagem, e talvez de
        outra filial.
        """
        primeira, entrega = self._bonificacao_na_carga()
        outra, _ = self._bonificacao_na_carga()

        self.client.post(
            reverse('logistica:bonificacao-entrega', args=[outra.pk, entrega.pk]),
            {'acao': 'em_transporte'},
        )

        entrega.refresh_from_db()
        self.assertEqual(entrega.status, S.PENDENTE)

    def test_a_tela_avisa_a_cortesia_sem_confirmacao(self):
        viagem, _ = self._bonificacao_na_carga()

        html = self._detalhe(viagem)

        self.assertIn('sem confirmação de entrega', html)


class NaoEntregueTests(EntregaBonificacaoBase):
    """
    A cortesia que não chegou.

    SEM MOTIVO, "não entregue" é um número em relatório. Com ele, é um
    problema com dono: "cliente ausente" é roteiro errado; "produto
    danificado" é carregamento errado — e os dois se resolvem em lugares
    diferentes.
    """

    def _ate_recusa(self, motivo=None):
        viagem, entrega = self._bonificacao_na_carga('20')
        EntregaBonificacaoService.mover(entrega, S.EM_TRANSPORTE)
        EntregaBonificacaoService.mover(entrega, S.RECUSADA, {
            'motivo_nao_entrega': (
                motivo or EntregaBonificacao.MotivoNaoEntrega.AUSENTE
            ),
        })
        return viagem, entrega

    def test_os_seis_motivos_da_especificacao_existem(self):
        self.assertEqual(
            [v for v, _ in EntregaBonificacao.MotivoNaoEntrega.choices],
            [
                'ausente', 'recusou', 'danificado', 'quantidade',
                'cancelamento', 'outro',
            ],
        )

    def test_recusar_sem_motivo_e_impedido(self):
        _, entrega = self._bonificacao_na_carga()
        EntregaBonificacaoService.mover(entrega, S.EM_TRANSPORTE)

        with self.assertRaises(DadosInvalidosError) as erro:
            EntregaBonificacaoService.mover(entrega, S.RECUSADA)

        self.assertIn('por que', str(erro.exception).lower())
        entrega.refresh_from_db()
        self.assertEqual(entrega.status, S.EM_TRANSPORTE)

    def test_cancelar_tambem_pede_motivo(self):
        _, entrega = self._bonificacao_na_carga()

        with self.assertRaises(DadosInvalidosError):
            EntregaBonificacaoService.mover(entrega, S.CANCELADA)

    def test_motivo_desconhecido_e_recusado(self):
        _, entrega = self._bonificacao_na_carga()
        EntregaBonificacaoService.mover(entrega, S.EM_TRANSPORTE)

        with self.assertRaises(DadosInvalidosError):
            EntregaBonificacaoService.mover(entrega, S.RECUSADA, {
                'motivo_nao_entrega': 'chuva',
            })

    def test_a_recusa_guarda_motivo_e_hora(self):
        _, entrega = self._ate_recusa(
            EntregaBonificacao.MotivoNaoEntrega.DANIFICADO,
        )

        entrega.refresh_from_db()
        self.assertEqual(
            entrega.motivo_nao_entrega,
            EntregaBonificacao.MotivoNaoEntrega.DANIFICADO,
        )
        self.assertIsNotNone(entrega.nao_entregue_em)

    def test_a_mercadoria_fica_identificada_ate_o_retorno(self):
        """
        Entre a recusa e o retorno tratado ela está fora de qualquer saldo:
        saiu do estoque, não virou entrega e ainda não voltou.
        """
        _, entrega = self._ate_recusa()

        self.assertTrue(entrega.nao_entregue)
        self.assertIn('BONIFICAÇÃO NÃO ENTREGUE', entrega.rotulo_nao_entregue)
        self.assertIn('Cliente ausente', entrega.rotulo_nao_entregue)
        self.assertTrue(any(
            'NÃO ENTREGUE' in p
            for p in EntregaBonificacaoService.pendencias(entrega)
        ))

    def test_depois_de_tratado_o_rotulo_sai(self):
        _, entrega = self._ate_recusa()
        EntregaBonificacaoService.mover(entrega, S.RETORNO_PENDENTE)

        self.assertTrue(entrega.nao_entregue)

        EntregaBonificacaoService.tratar_retorno(entrega, usuario=self.usuario)

        entrega.refresh_from_db()
        self.assertFalse(entrega.nao_entregue)
        self.assertEqual(entrega.rotulo_nao_entregue, '')


class TratamentoDoRetornoTests(EntregaBonificacaoBase):
    """A mercadoria volta de verdade — cada uma para onde saiu."""

    def _saldo_estoque(self):
        from apps.estoque.models import Estoque

        return Estoque.objects.get(
            produto=self.produto, filial=self.filial,
        ).quantidade_atual

    def _ate_retorno_pendente(self, entrega, motivo):
        EntregaBonificacaoService.mover(entrega, S.EM_TRANSPORTE)
        EntregaBonificacaoService.mover(entrega, S.RECUSADA, {
            'motivo_nao_entrega': motivo,
        })
        EntregaBonificacaoService.mover(entrega, S.RETORNO_PENDENTE)

    def test_a_bonificacao_da_carga_volta_para_o_estoque(self):
        """
        Ela saiu do estoque da filial quando o caminhão fechou: é para lá
        que ela volta.
        """
        _, entrega = self._bonificacao_na_carga('20')
        depois_da_saida = self._saldo_estoque()

        self._ate_retorno_pendente(
            entrega, EntregaBonificacao.MotivoNaoEntrega.RECUSOU,
        )
        EntregaBonificacaoService.tratar_retorno(entrega, usuario=self.usuario)

        self.assertEqual(self._saldo_estoque(), depois_da_saida + Decimal('20'))

    def test_o_retorno_deixa_rastro_no_razao(self):
        _, entrega = self._bonificacao_na_carga('20')

        self._ate_retorno_pendente(
            entrega, EntregaBonificacao.MotivoNaoEntrega.DANIFICADO,
        )
        EntregaBonificacaoService.tratar_retorno(entrega, usuario=self.usuario)

        movimento = MovimentacaoEstoque.objects.filter(
            produto=self.produto,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.DEVOLUCAO_CLIENTE,
        ).first()
        self.assertIsNotNone(movimento)
        self.assertIn('Retorno de Bonificação não entregue', movimento.observacao)
        self.assertIn('danificado', movimento.observacao.lower())
        self.assertEqual(movimento.cliente, self.cliente)

    def test_a_bonificacao_da_rua_volta_para_o_saldo_da_viagem(self):
        """
        Aquela mercadoria saiu na remessa: devolvê-la ao estoque da filial
        somaria estoque que nunca saiu de lá.
        """
        from apps.logistica.models import SaldoCarga

        viagem, entrega = self._bonificacao_na_rua('20')
        antes_estoque = self._saldo_estoque()
        saldo = SaldoCarga.objects.get(viagem=viagem, produto=self.produto)
        self.assertEqual(saldo.quantidade_bonificada, Decimal('20.000'))

        # A entrega da rua nasce ENTREGUE; para devolver, ela volta ao
        # comeco e passa pelo mesmo caminho de recusa das outras.
        entrega.status = S.PENDENTE
        entrega.save(update_fields=['status'])
        self._ate_retorno_pendente(
            entrega, EntregaBonificacao.MotivoNaoEntrega.QUANTIDADE,
        )
        EntregaBonificacaoService.tratar_retorno(entrega, usuario=self.usuario)

        saldo.refresh_from_db()
        self.assertEqual(saldo.quantidade_bonificada, ZERO)
        self.assertEqual(saldo.quantidade_em_poder, Decimal('300.000'))
        # O estoque da filial não é tocado.
        self.assertEqual(self._saldo_estoque(), antes_estoque)

    def test_nao_se_trata_retorno_de_quem_ainda_nao_recusou(self):
        _, entrega = self._bonificacao_na_carga()

        with self.assertRaises(DadosInvalidosError):
            EntregaBonificacaoService.tratar_retorno(entrega, usuario=self.usuario)

    def test_o_tratamento_marca_quando_aconteceu(self):
        _, entrega = self._bonificacao_na_carga('20')

        self._ate_retorno_pendente(
            entrega, EntregaBonificacao.MotivoNaoEntrega.OUTRO,
        )
        EntregaBonificacaoService.tratar_retorno(entrega, usuario=self.usuario)

        entrega.refresh_from_db()
        self.assertEqual(entrega.status, S.RETORNADA)
        self.assertIsNotNone(entrega.retorno_tratado_em)

    def test_marcar_retornada_passa_pelo_tratamento(self):
        """
        Marcar RETORNADA sem devolver deixaria o status dizendo que voltou
        enquanto a mercadoria segue fora de qualquer saldo.
        """
        _, entrega = self._bonificacao_na_carga('20')
        depois_da_saida = self._saldo_estoque()

        self._ate_retorno_pendente(
            entrega, EntregaBonificacao.MotivoNaoEntrega.AUSENTE,
        )
        EntregaBonificacaoService.mover(entrega, S.RETORNADA, usuario=self.usuario)

        self.assertEqual(self._saldo_estoque(), depois_da_saida + Decimal('20'))


class TelaNaoEntregueTests(EntregaBonificacaoBase):
    """O motivo e a etiqueta na tela."""

    def test_a_tela_oferece_os_motivos_junto_do_botao(self):
        """
        Pedi-los numa tela seguinte faria metade das recusas ficar sem
        explicação: quem está na porta do cliente responde agora ou não
        responde mais.
        """
        viagem, entrega = self._bonificacao_na_carga()
        EntregaBonificacaoService.mover(entrega, S.EM_TRANSPORTE)

        html = self.client.get(
            reverse('logistica:viagem-detail', args=[viagem.pk]),
        ).content.decode()

        self.assertIn('name="motivo_nao_entrega"', html)
        self.assertIn('Cliente ausente', html)
        self.assertIn('Produto danificado', html)

    def test_recusar_pela_tela_com_motivo(self):
        viagem, entrega = self._bonificacao_na_carga()
        EntregaBonificacaoService.mover(entrega, S.EM_TRANSPORTE)

        self.client.post(
            reverse('logistica:bonificacao-entrega', args=[viagem.pk, entrega.pk]),
            {'acao': 'recusada', 'motivo_nao_entrega': 'recusou'},
        )

        entrega.refresh_from_db()
        self.assertEqual(entrega.status, S.RECUSADA)
        self.assertEqual(entrega.motivo_nao_entrega, 'recusou')

    def test_a_etiqueta_aparece_na_viagem(self):
        viagem, entrega = self._bonificacao_na_carga()
        EntregaBonificacaoService.mover(entrega, S.EM_TRANSPORTE)
        EntregaBonificacaoService.mover(entrega, S.RECUSADA, {
            'motivo_nao_entrega': 'ausente',
        })

        html = self.client.get(
            reverse('logistica:viagem-detail', args=[viagem.pk]),
        ).content.decode()

        self.assertIn('BONIFICAÇÃO NÃO ENTREGUE', html)
        self.assertIn('aguardando retorno', html)
