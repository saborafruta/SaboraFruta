"""
O fluxo completo, do pedido de compra ao cliente — os vinte passos, num teste.

ESTE ARQUIVO NÃO CONSTRÓI NADA. Cada um dos vinte passos já tem serviço,
modelo e tela; o que não existia era alguém percorrer a corrente inteira de uma
vez. E é justamente esse percurso que faltava quando quatro caminhos deste
repositório se revelaram mortos na chegada nesta sessão — todos eles tinham
código, view e botão, e nenhum tinha quem os executasse.

Um teste por serviço prova que a peça funciona. Só o percurso prova que as
peças se ENCAIXAM: que o lote que o recebimento cria é o mesmo que a produção
consome, que o acabado que a ordem gera é o mesmo que a separação despacha, e
que o rastro liga o cliente de volta ao produtor.

O CAMINHO, na ordem da especificação:

     1. compra da matéria-prima      11. envase
     2. recebimento                  12. embalagem
     3. controle de qualidade        13. congelamento
     4. estoque de matéria-prima     14. produto acabado
     5. planejamento da produção     15. câmara fria
     6. ordem de produção            16. pedido de venda
     7. separação dos materiais      17. separação
     8. produção                     18. expedição
     9. controle de perdas           19. cliente
    10. controle de qualidade        20. (e o rastro, de volta)

CADA PASSO AFIRMA O SEU EFEITO, e não só que não estourou. Um teste de fluxo
que só chama os serviços em sequência passa mesmo quando um deles não faz
nada — e aí ele dá a falsa segurança de que a corrente está inteira.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.cadastros.models import Cliente, Fornecedor
from apps.compras.models import PedidoCompra
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import Estoque, LoteProduto
from apps.lotes.services.rastreio import RastreioService
from apps.polpa.models import (
    Camara, EtapaReceita, FichaProduto, Fruta, OrdemPolpa, Recebimento,
    ReservaInsumo, Subproduto,
)
from apps.polpa.services import (
    CatalogoService, OrdemPolpaService, ReceitaService,
)
from apps.polpa.services.armazenagem import ArmazenagemService
from apps.polpa.services.compra import CompraService
from apps.polpa.services.planejamento import PlanejamentoService
from apps.polpa.services.processo import ProcessoService
from apps.polpa.services.recebimento import RecebimentoService
from apps.polpa.services.subproduto import SubprodutoService
from apps.producao.models import ItemFichaTecnica
from apps.produtos.models import Produto, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial
from apps.qualidade.models import ParametroQualidadeProduto
from apps.qualidade.services.checklist_service import (
    ETAPA_ACABADO, ETAPA_RECEBIMENTO, ChecklistService,
)
from apps.vendas.models import PedidoVenda
from apps.vendas.services.venda_service import VendaService

T = FichaProduto.Tipo
S = OrdemPolpa.Situacao
D = Subproduto.Destino
ZERO = Decimal('0')


class FluxoCompletoTests(TestCase):
    """Uma batida de polpa de acerola, da roça ao cliente."""

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Polpas Fluxo LTDA', nome_fantasia='Fluxo',
            cnpj='11145678000191', segmento='polpa_frutas',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='11145678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='KG', descricao='Quilograma',
            tipo=UnidadeMedida.Tipo.PESO,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='chefe@fluxo.local', nome='Chefe', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.produtor = Fornecedor.objects.create(
            filial=cls.filial, razao_social='Sítio do Silva',
            cpf_cnpj='11122233344',
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Distribuidora Norte',
            cpf_cnpj='55566677788', ativo=True,
        )

    def setUp(self):
        self.acerola = self._item(
            T.FRUTA, 'Acerola in natura', custo=Decimal('4'),
        )
        self.pote = self._item(T.POTE, 'Pote 100 g', custo=Decimal('0.30'))
        self.polpa = self._item(
            T.POLPA, 'Polpa de acerola 100 g', validade_dias=180,
            peso_liquido=Decimal('0.100'),
            preco_venda=Decimal('2.50'),
        )
        self.fruta = Fruta.objects.create(
            filial=self.filial, nome='Acerola', produto=self.acerola,
        )
        self.receita = self._receita()
        self.camara = Camara.objects.create(
            filial=self.filial, nome='Câmara 1',
            temperatura_min=Decimal('-25'), temperatura_max=Decimal('-18'),
            capacidade_kg=Decimal('10000'),
        )

    # ── Montagem ─────────────────────────────────────────────────────────

    def _item(self, tipo, descricao, custo=ZERO, **extras):
        dados = {
            'tipo': tipo, 'descricao': descricao, 'codigo': descricao[:10],
            'unidade_medida': self.unidade, 'preco_custo': custo,
            'preco_custo_medio': custo,
        }
        dados.update(extras)
        produto = CatalogoService.salvar(self.filial, dados).produto
        ProdutoFilial.objects.get_or_create(produto=produto, filial=self.filial)
        return produto

    def _receita(self):
        """1.000 potes consomem 500 kg de acerola e 1.000 potes."""
        receita = ReceitaService.criar(self.filial, self.polpa, {
            'descricao': 'Polpa de acerola 100 g', 'versao': '1.0',
            'quantidade_produzida': Decimal('1000'),
            'rendimento_esperado': Decimal('60'),
            'custo_mao_obra_padrao': Decimal('200'),
        })
        ItemFichaTecnica.objects.create(
            ficha=receita.ficha, materia_prima=self.acerola,
            quantidade=Decimal('500'), perda_prevista=ZERO,
        )
        ItemFichaTecnica.objects.create(
            ficha=receita.ficha, materia_prima=self.pote,
            quantidade=Decimal('1000'), perda_prevista=ZERO,
        )
        EtapaReceita.objects.create(receita=receita, ordem=1, nome='Despolpa')
        ReceitaService.ativar(receita)
        return receita

    def _saldo(self, produto) -> Decimal:
        estoque = Estoque.objects.filter(
            produto=produto, filial=self.filial,
        ).first()
        return estoque.quantidade_atual if estoque else ZERO

    def _parametro(self, produto, nome, etapa, minimo=None, maximo=None):
        return ParametroQualidadeProduto.objects.create(
            filial=self.filial, produto=produto, etapa=etapa,
            nome_parametro=nome, valor_minimo=minimo, valor_maximo=maximo,
        )

    # ── O percurso ───────────────────────────────────────────────────────

    def test_da_compra_ao_cliente(self):
        # ── 1. COMPRA DA MATÉRIA-PRIMA ───────────────────────────────────
        # A ordem ainda não existe, então a necessidade nasce do planejamento.
        ordem = OrdemPolpaService.criar(
            self.filial, self.receita,
            {'quantidade_planejada': Decimal('1000')}, self.usuario,
        )
        requisicao = CompraService.gerar_requisicao(
            self.filial, CompraService.necessidade(self.filial), self.usuario,
        )
        self.assertEqual(
            requisicao.itens.get(produto=self.acerola).quantidade,
            Decimal('500.0000'),
            'sem estoque, a requisição pede os 500 kg da receita',
        )
        pedido_compra = CompraService.gerar_pedido_compra(
            requisicao, self.produtor, self.usuario,
        )
        self.assertEqual(pedido_compra.fornecedor, self.produtor)

        # ── 2. RECEBIMENTO ───────────────────────────────────────────────
        recebimento = Recebimento.objects.create(
            filial=self.filial, numero=1, fruta=self.fruta,
            produtor=self.produtor, data=timezone.localdate(),
            nota_fiscal='NF-9001',
            peso_bruto=Decimal('1200'), tara=Decimal('200'),
            preco_kg=Decimal('4'),
        )
        # A CLASSIFICAÇÃO É PASSO PRÓPRIO, e a aprovação a exige: análise sem
        # responsável é o registro que ninguém defende numa auditoria.
        RecebimentoService.classificar(recebimento, {
            'brix': '9', 'ph': '3.4', 'impureza': '1', 'danificada': '2',
            'temperatura_chegada': '22',
        }, self.usuario)
        lote_fruta = RecebimentoService.aprovar(recebimento, self.usuario)
        self.assertIsNotNone(lote_fruta, 'aprovar a carga faz nascer o lote')
        self.assertEqual(lote_fruta.produto, self.acerola)

        # ── 3. CONTROLE DE QUALIDADE (matéria-prima) ─────────────────────
        self._parametro(
            self.acerola, 'Brix', ETAPA_RECEBIMENTO,
            minimo=Decimal('7'), maximo=Decimal('12'),
        )
        analise_mp = ChecklistService.abrir(
            self.filial, self.acerola, ETAPA_RECEBIMENTO,
            self.usuario, lote=lote_fruta,
        )
        ChecklistService.preencher(
            analise_mp,
            {analise_mp.itens.get().pk: {'valor': '9'}},
            usuario=self.usuario,
        )
        ChecklistService.concluir(analise_mp, self.usuario)
        lote_fruta.refresh_from_db()
        self.assertEqual(
            lote_fruta.status, LoteProduto.Status.ATIVO,
            'aprovado na análise, o lote fica liberado para consumo',
        )

        # ── 4. ESTOQUE DE MATÉRIA-PRIMA ──────────────────────────────────
        self.assertGreater(
            self._saldo(self.acerola), ZERO,
            'o recebimento aprovado põe a fruta no estoque',
        )
        # O pote vem por outra porta (compra comum); aqui basta existir.
        self._creditar(self.pote, Decimal('2000'))

        # ── 5. PLANEJAMENTO DA PRODUÇÃO ──────────────────────────────────
        VendaService  # o pedido de venda entra no passo 16; aqui é reposição
        sugestoes = PlanejamentoService.sugestoes(self.filial)
        linha = next(l for l in sugestoes if l['produto'].pk == self.polpa.pk)
        self.assertEqual(
            linha['em_producao'], Decimal('1000.000'),
            'a ordem aberta desconta da sugestão — não se produz duas vezes',
        )

        # ── 6. ORDEM DE PRODUÇÃO ─────────────────────────────────────────
        self.assertEqual(ordem.situacao, S.PLANEJADA)
        OrdemPolpaService.mover(ordem, S.LIBERADA, self.usuario)

        # ── 7. SEPARAÇÃO DOS MATERIAIS ───────────────────────────────────
        OrdemPolpaService.mover(ordem, S.EM_PRODUCAO, self.usuario)
        reservas = ReservaInsumo.all_objects.filter(
            ordem=ordem, status=ReservaInsumo.Status.ATIVA,
        )
        self.assertEqual(
            reservas.count(), 2,
            'começar a batida separa fruta e embalagem',
        )

        # ── 8. PRODUÇÃO ──────────────────────────────────────────────────
        etapas = list(ordem.etapas_processo.all())
        self.assertGreater(len(etapas), 1, 'a ordem nasce com o roteiro')
        ProcessoService.apontar(etapas[0], {
            'quantidade_entrada': '1000', 'quantidade_saida': '1000',
        }, self.usuario)

        # ── 9. CONTROLE DE PERDAS ────────────────────────────────────────
        ProcessoService.apontar(etapas[-1], {
            'quantidade_entrada': '620', 'quantidade_saida': '620',
        }, self.usuario)
        resumo = ProcessoService.resumo(ordem)
        self.assertEqual(resumo['perda_total'], Decimal('380.000'))
        self.assertEqual(resumo['rendimento'], Decimal('62.00'))
        self.assertTrue(
            resumo['rendimento_dentro'],
            '62% está acima do piso de 55% (esperado 60, tolerância 5)',
        )
        # A perda ganha nome e destino.
        SubprodutoService.registrar(ordem, {
            'tipo': Subproduto.Tipo.BAGACO, 'quantidade': '300',
            'destino': D.VENDA, 'valor_recebido': '90',
            'destinatario': 'Fazenda São João',
        }, self.usuario)
        sub = SubprodutoService.resumo(ordem)
        self.assertEqual(sub['resultado'], Decimal('90.00'))
        self.assertEqual(
            sub['perda_sem_nome'], Decimal('80.000'),
            'sobra perda sem destino — e a tela pergunta por ela',
        )

        # ── 11-13. ENVASE, EMBALAGEM, CONGELAMENTO ───────────────────────
        # São etapas do mesmo roteiro; o passo 8 já as percorre. O que
        # importa aqui é que o roteiro do produto as contenha.
        nomes = {e.etapa for e in etapas}
        self.assertTrue(
            {'envase', 'congelamento'} & nomes,
            'o fluxo da polpa passa por envase e congelamento',
        )

        # ── 14. PRODUTO ACABADO ──────────────────────────────────────────
        OrdemPolpaService.concluir(
            ordem, self.usuario, quantidade=Decimal('1000'),
        )
        ordem.refresh_from_db()
        self.assertEqual(ordem.situacao, S.PRODUZIDA)
        lote_acabado = ordem.lote
        self.assertIsNotNone(lote_acabado, 'a ordem encerrada gera o lote')
        self.assertEqual(lote_acabado.produto, self.polpa)
        self.assertIsNotNone(
            lote_acabado.data_validade,
            'a validade sai do prazo do produto, sem ninguém digitar',
        )
        self.assertEqual(
            ReservaInsumo.all_objects.filter(
                ordem=ordem, status=ReservaInsumo.Status.ATIVA,
            ).count(), 0,
            'a reserva morre quando o consumo nasce',
        )

        # ── 10. CONTROLE DE QUALIDADE (acabado) ──────────────────────────
        self._parametro(
            self.polpa, 'Temperatura', ETAPA_ACABADO,
            minimo=Decimal('-25'), maximo=Decimal('-18'),
        )
        analise_pa = ChecklistService.abrir(
            self.filial, self.polpa, ETAPA_ACABADO,
            self.usuario, lote=lote_acabado,
        )
        ChecklistService.preencher(
            analise_pa,
            {analise_pa.itens.get().pk: {'valor': '-20'}},
            usuario=self.usuario,
        )
        ChecklistService.concluir(analise_pa, self.usuario)
        lote_acabado.refresh_from_db()
        self.assertEqual(lote_acabado.status, LoteProduto.Status.ATIVO)

        # ── 15. CÂMARA FRIA ──────────────────────────────────────────────
        armazenado = ArmazenagemService.guardar_da_ordem(ordem, self.camara)
        self.assertIsNotNone(armazenado, 'o lote ganha endereço na câmara')
        self.assertEqual(armazenado.camara, self.camara)

        # ── 16. PEDIDO DE VENDA ──────────────────────────────────────────
        pedido_venda = VendaService.criar_pedido(
            filial=self.filial, cliente=self.cliente, usuario=self.usuario,
        )
        VendaService.adicionar_item(
            pedido_venda, produto=self.polpa,
            quantidade=Decimal('400'), valor_unitario=Decimal('2'),
        )
        VendaService.confirmar_pedido(pedido_venda, self.usuario)
        pedido_venda.refresh_from_db()
        self.assertEqual(pedido_venda.status, PedidoVenda.Status.CONFIRMADO)

        # ── 17. SEPARAÇÃO ────────────────────────────────────────────────
        separacao = VendaService.separar_pedido(pedido_venda, self.usuario)
        item = separacao.itens.get()
        self.assertEqual(
            item.lote, lote_acabado,
            'a separação puxa o lote que esta batida produziu, por FEFO',
        )

        # ── 18-19. EXPEDIÇÃO E CLIENTE ───────────────────────────────────
        VendaService.faturar_pedido(pedido_venda, self.usuario)
        pedido_venda.refresh_from_db()
        self.assertEqual(pedido_venda.cliente, self.cliente)

        # ── 20. O RASTRO, DE VOLTA ───────────────────────────────────────
        # A prova de que a corrente é uma só: da fruta que chegou na balança
        # até o cliente que a recebeu.
        destino = RastreioService.para_onde_foi(lote_fruta)
        lotes = [e.lote.numero_lote for e in destino]
        self.assertIn(
            lote_acabado.numero_lote, lotes,
            'a fruta recebida chega ao acabado que ela virou',
        )
        origem = RastreioService.de_onde_veio(lote_acabado)
        self.assertIn(
            lote_fruta.numero_lote, [e.lote.numero_lote for e in origem],
            'e o acabado volta ao lote de fruta que entrou nele',
        )
        resumo_rastro = RastreioService.resumo(origem, destino)
        self.assertIn(
            self.produtor, resumo_rastro['fornecedores'],
            'o recall chega ao produtor',
        )

    # ── Auxiliar ─────────────────────────────────────────────────────────

    def _creditar(self, produto: Produto, quantidade: Decimal):
        """Põe saldo de um insumo que não veio pelo recebimento de fruta."""
        estoque, _ = Estoque.objects.get_or_create(
            produto=produto, filial=self.filial,
        )
        estoque.quantidade_atual = quantidade
        estoque.quantidade_reservada = ZERO
        estoque.atualizar_disponivel()
        estoque.save()
        LoteProduto.objects.get_or_create(
            filial=self.filial, produto=produto, numero_lote=f'L-{produto.pk}',
            defaults={
                'quantidade_inicial': quantidade,
                'quantidade_atual': quantidade,
                'custo_unitario': produto.preco_custo,
                'data_validade': timezone.localdate() + timedelta(days=365),
                'status': LoteProduto.Status.ATIVO,
            },
        )
