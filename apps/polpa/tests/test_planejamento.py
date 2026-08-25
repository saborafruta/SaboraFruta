"""
O PPCP: necessidade, calendário, quadro e capacidade.

O QUE ESTES TESTES CERCAM:

  · A CONTA DA NECESSIDADE, inteira: pedidos + mínimo + previsão − estoque −
    o que já está em produção. O último termo é o que quase todo sistema
    esquece, e sem ele a sugestão manda produzir de novo o que já está na
    linha — é assim que se enche a câmara de produto vencendo;

  · A PREVISÃO É HISTÓRICO, não profecia. Sem histórico ela é ZERO e a tela
    diz isso: inventar demanda para produto novo enche a câmara de item que
    ninguém pediu;

  · O QUADRO E O CALENDÁRIO SÃO LEITURAS da mesma ordem. Se algum deles
    guardar estado próprio, passa a existir uma segunda verdade sobre onde a
    produção está;

  · SEM CAPACIDADE CADASTRADA a ocupação é `None`. 0% seria lido como
    recurso livre e 100% como lotado — as duas leituras erradas levam à
    decisão errada.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.models import Cliente, ClienteFilial
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import Estoque
from apps.polpa.models import FichaProduto, OrdemPolpa, Recurso
from apps.polpa.services import (
    CatalogoService, OrdemPolpaService, PlanejamentoService, ReceitaService,
)
from apps.producao.models import ItemFichaTecnica
from apps.produtos.models import UnidadeMedida, UnidadeMedidaFilial
from apps.vendas.models import ItemPedidoVenda, PedidoVenda

T = FichaProduto.Tipo
S = OrdemPolpa.Situacao


class PlanejamentoBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Polpas PCP LTDA', nome_fantasia='PCP',
            cnpj='23345678000191', segmento='polpa_frutas',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='23345678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='UN', descricao='Unidade',
            tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Mercado Central', cpf_cnpj='12345678901',
        )
        ClienteFilial.objects.create(cliente=cls.cliente, filial=cls.filial)
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='chefe@pcp.local', nome='Chefe', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        self.produto = self._acabado('Polpa de manga 100 g', 'PM100')
        self.manga = self._item(T.FRUTA, 'Manga in natura', 'MANGA')
        self.receita = self._receita(self.produto)

    def _acabado(self, descricao, codigo, minimo=Decimal('0')):
        ficha = CatalogoService.salvar(self.filial, {
            'tipo': T.POLPA, 'descricao': descricao, 'codigo': codigo,
            'unidade_medida': self.unidade, 'validade_dias': 180,
            'peso_liquido': Decimal('0.100'), 'estoque_minimo': minimo,
        })
        return ficha.produto

    def _item(self, tipo, descricao, codigo):
        ficha = CatalogoService.salvar(self.filial, {
            'tipo': tipo, 'descricao': descricao, 'codigo': codigo,
            'unidade_medida': self.unidade,
        })
        return ficha.produto

    def _receita(self, produto):
        receita = ReceitaService.criar(self.filial, produto, {
            'descricao': str(produto), 'versao': '1.0',
            'quantidade_produzida': Decimal('1000'),
            'rendimento_esperado': Decimal('60'),
        })
        ItemFichaTecnica.objects.create(
            ficha=receita.ficha, materia_prima=self.manga,
            quantidade=Decimal('100'), perda_prevista=Decimal('0'),
        )
        from apps.polpa.models import EtapaReceita

        EtapaReceita.objects.create(receita=receita, ordem=1, nome='Despolpa')
        ReceitaService.ativar(receita)
        return receita

    def _estoque(self, produto, quantidade):
        estoque, _ = Estoque.objects.get_or_create(produto=produto, filial=self.filial)
        estoque.quantidade_atual = quantidade
        estoque.quantidade_reservada = Decimal('0')
        estoque.atualizar_disponivel()
        estoque.save()
        return estoque

    def _pedido(self, produto, quantidade, status, atendida=Decimal('0'), dias_atras=0):
        pedido = PedidoVenda.objects.create(
            filial=self.filial, numero_pedido=f'PV{PedidoVenda.objects.count() + 1}',
            cliente=self.cliente, usuario=self.usuario, status=status,
            data_emissao=timezone.now() - timedelta(days=dias_atras),
        )
        ItemPedidoVenda.objects.create(
            pedido=pedido, produto=produto, quantidade=quantidade,
            quantidade_atendida=atendida, valor_unitario=Decimal('1'),
            # `valor_bruto` e `valor_total` são obrigatórios no item do
            # pedido — a fixture preenche o que a tela de venda calcularia.
            valor_bruto=quantidade, valor_total=quantidade,
        )
        return pedido

    def _linha(self, sugestoes, produto):
        return next(l for l in sugestoes if l['produto'] == produto)


class NecessidadeTests(PlanejamentoBase):
    """A conta que diz o que produzir."""

    def test_pedido_em_aberto_vira_necessidade(self):
        self._pedido(self.produto, Decimal('500'), PedidoVenda.Status.CONFIRMADO)

        linha = self._linha(PlanejamentoService.sugestoes(self.filial), self.produto)

        self.assertEqual(linha['pedidos'], Decimal('500'))
        self.assertEqual(linha['necessidade'], Decimal('500'))

    def test_o_que_ja_foi_faturado_nao_e_produzido_de_novo(self):
        """
        `quantidade - quantidade_atendida`: o que saiu já baixou do estoque.
        """
        self._pedido(
            self.produto, Decimal('500'), PedidoVenda.Status.PARCIALMENTE_FATURADO,
            atendida=Decimal('200'),
        )

        linha = self._linha(PlanejamentoService.sugestoes(self.filial), self.produto)

        self.assertEqual(linha['pedidos'], Decimal('300'))

    def test_estoque_desconta_da_necessidade(self):
        self._pedido(self.produto, Decimal('500'), PedidoVenda.Status.CONFIRMADO)
        self._estoque(self.produto, Decimal('300'))

        linha = self._linha(PlanejamentoService.sugestoes(self.filial), self.produto)

        self.assertEqual(linha['necessidade'], Decimal('200'))

    def test_o_estoque_minimo_entra_na_conta(self):
        produto = self._acabado('Polpa de acerola', 'PAC', minimo=Decimal('100'))

        linha = self._linha(PlanejamentoService.sugestoes(self.filial), produto)

        self.assertEqual(linha['minimo'], Decimal('100'))
        self.assertEqual(linha['necessidade'], Decimal('100'))

    def test_a_ordem_em_aberto_desconta_da_sugestao(self):
        """
        É o termo que quase todo sistema esquece — e sem ele a fábrica
        produz o dobro do que vende.
        """
        self._pedido(self.produto, Decimal('1000'), PedidoVenda.Status.CONFIRMADO)
        OrdemPolpaService.criar(
            self.filial, self.receita,
            {'quantidade_planejada': Decimal('600')}, self.usuario,
        )

        linha = self._linha(PlanejamentoService.sugestoes(self.filial), self.produto)

        self.assertEqual(linha['em_producao'], Decimal('600'))
        self.assertEqual(linha['necessidade'], Decimal('400'))

    def test_ordem_encerrada_nao_desconta_mais(self):
        self._pedido(self.produto, Decimal('1000'), PedidoVenda.Status.CONFIRMADO)
        op = OrdemPolpaService.criar(
            self.filial, self.receita,
            {'quantidade_planejada': Decimal('600')}, self.usuario,
        )
        OrdemPolpaService.mover(op, S.CANCELADA, self.usuario, {'motivo': 'Engano'})

        linha = self._linha(PlanejamentoService.sugestoes(self.filial), self.produto)

        self.assertEqual(linha['em_producao'], Decimal('0'))

    def test_a_previsao_sai_do_historico_faturado(self):
        """Média diária dos últimos 90 dias, projetada no horizonte."""
        self._pedido(
            self.produto, Decimal('900'), PedidoVenda.Status.FATURADO, dias_atras=10,
        )

        linha = self._linha(
            PlanejamentoService.sugestoes(self.filial, horizonte=30), self.produto,
        )

        # 900 em 90 dias = 10/dia → 300 em 30 dias.
        self.assertEqual(linha['media_diaria'], Decimal('10.000'))
        self.assertEqual(linha['previsao'], Decimal('300.000'))

    def test_produto_sem_historico_nao_ganha_previsao_inventada(self):
        """Inventar demanda é como se enche a câmara de item que ninguém pediu."""
        linha = self._linha(PlanejamentoService.sugestoes(self.filial), self.produto)

        self.assertEqual(linha['previsao'], Decimal('0'))
        self.assertTrue(linha['sem_historico'])

    def test_pedido_em_rascunho_nao_conta(self):
        """Orçamento não é compromisso — produzir por ele é produzir no escuro."""
        self._pedido(self.produto, Decimal('500'), PedidoVenda.Status.RASCUNHO)

        linha = self._linha(PlanejamentoService.sugestoes(self.filial), self.produto)

        self.assertEqual(linha['pedidos'], Decimal('0'))

    def test_produto_coberto_continua_na_lista(self):
        """
        Ver que um produto está coberto é informação — se ele some, alguém
        vai perguntar se foi esquecido.
        """
        self._estoque(self.produto, Decimal('5000'))

        linhas = PlanejamentoService.sugestoes(self.filial)

        self.assertEqual(self._linha(linhas, self.produto)['necessidade'], Decimal('0'))

    def test_produto_sem_receita_ativa_e_marcado(self):
        produto = self._acabado('Polpa de caju', 'PCJ', minimo=Decimal('50'))

        linha = self._linha(PlanejamentoService.sugestoes(self.filial), produto)

        self.assertIsNone(linha['receita'])


class CalendarioTests(PlanejamentoBase):
    """Quando produzir."""

    def _op(self, quando=None, recurso=None):
        op = OrdemPolpaService.criar(
            self.filial, self.receita,
            {'quantidade_planejada': Decimal('1000')}, self.usuario,
        )
        if quando:
            PlanejamentoService.programar(op, quando, recurso)
        return op

    def test_a_ordem_programada_cai_no_dia(self):
        hoje = timezone.localdate()
        self._op(timezone.now())

        dias = PlanejamentoService.calendario(
            self.filial, hoje, hoje + timedelta(days=6),
        )

        do_dia = next(d for d in dias if d['dia'] == hoje)
        self.assertEqual(len(do_dia['ordens']), 1)
        self.assertEqual(do_dia['carga'], Decimal('1000'))

    def test_ordem_sem_data_nao_some_do_sistema(self):
        """Ordem sem dia marcado é a que ninguém lembra de fazer."""
        self._op()

        self.assertEqual(PlanejamentoService.sem_data(self.filial).count(), 1)

    def test_programar_nao_libera(self):
        """
        Programar diz quando se pretende fazer; liberar diz que pode
        começar. Juntar as duas faria toda ordem no calendário virar
        autorização de consumo.
        """
        op = self._op(timezone.now())

        op.refresh_from_db()
        self.assertEqual(op.situacao, S.PLANEJADA)

    def test_ordem_cancelada_sai_do_calendario(self):
        op = self._op(timezone.now())
        OrdemPolpaService.mover(op, S.CANCELADA, self.usuario, {'motivo': 'Engano'})

        hoje = timezone.localdate()
        dias = PlanejamentoService.calendario(self.filial, hoje, hoje + timedelta(days=6))

        self.assertEqual(sum(len(d['ordens']) for d in dias), 0)


class CapacidadeTests(PlanejamentoBase):
    """Cabe no dia?"""

    def _recurso(self, capacidade=None):
        return Recurso.objects.create(
            filial=self.filial, nome='Despolpadeira 1',
            tipo=Recurso.Tipo.MAQUINA, capacidade_dia=capacidade,
        )

    def test_a_carga_programada_conta_no_recurso(self):
        recurso = self._recurso(Decimal('1000'))
        op = OrdemPolpaService.criar(
            self.filial, self.receita,
            {'quantidade_planejada': Decimal('3000')}, self.usuario,
        )
        PlanejamentoService.programar(op, timezone.now(), recurso)

        hoje = timezone.localdate()
        dados = PlanejamentoService.carga_por_recurso(
            self.filial, hoje, hoje + timedelta(days=2),
        )[0]

        linha = dados['linhas'][0]
        self.assertEqual(linha['programado'], Decimal('3000'))
        # 3 dias × 1.000 = 3.000 de capacidade → 100%.
        self.assertEqual(linha['ocupacao'], Decimal('100.0'))
        self.assertFalse(linha['estourou'])

    def test_estouro_de_capacidade_e_marcado(self):
        recurso = self._recurso(Decimal('500'))
        op = OrdemPolpaService.criar(
            self.filial, self.receita,
            {'quantidade_planejada': Decimal('3000')}, self.usuario,
        )
        PlanejamentoService.programar(op, timezone.now(), recurso)

        hoje = timezone.localdate()
        dados = PlanejamentoService.carga_por_recurso(self.filial, hoje, hoje)[0]

        self.assertTrue(dados['linhas'][0]['estourou'])

    def test_sem_capacidade_a_ocupacao_e_nula(self):
        """0% seria lido como livre; 100%, como lotado. As duas erradas."""
        self._recurso(None)

        hoje = timezone.localdate()
        dados = PlanejamentoService.carga_por_recurso(self.filial, hoje, hoje)[0]

        self.assertIsNone(dados['linhas'][0]['ocupacao'])

    def test_ordem_sem_recurso_aparece_a_parte(self):
        """Programada sem recurso não pode sumir dentro de uma linha qualquer."""
        self._recurso(Decimal('1000'))
        op = OrdemPolpaService.criar(
            self.filial, self.receita,
            {'quantidade_planejada': Decimal('800')}, self.usuario,
        )
        PlanejamentoService.programar(op, timezone.now())

        hoje = timezone.localdate()
        dados = PlanejamentoService.carga_por_recurso(self.filial, hoje, hoje)[0]

        self.assertEqual(dados['sem_recurso'], Decimal('800'))


class QuadroTests(PlanejamentoBase):
    """O quadro é uma leitura, não um segundo lugar onde a ordem vive."""

    def test_as_colunas_sao_as_situacoes_da_op(self):
        colunas = PlanejamentoService.kanban(self.filial)

        chaves = [c['chave'] for c in colunas]
        self.assertNotIn(S.CANCELADA, chaves)
        self.assertIn(S.PLANEJADA, chaves)
        self.assertIn(S.PRODUZIDA, chaves)

    def test_a_ordem_aparece_na_coluna_da_situacao(self):
        op = OrdemPolpaService.criar(
            self.filial, self.receita,
            {'quantidade_planejada': Decimal('1000')}, self.usuario,
        )
        OrdemPolpaService.mover(op, S.LIBERADA, self.usuario)

        colunas = PlanejamentoService.kanban(self.filial)

        liberadas = next(c for c in colunas if c['chave'] == S.LIBERADA)
        self.assertEqual(liberadas['total'], 1)
        self.assertEqual(liberadas['quantidade'], Decimal('1000'))


class TelasPcpTests(PlanejamentoBase):
    """As telas do PCP."""

    def test_as_tres_telas_abrem(self):
        for rota in ('polpa:planejamento', 'polpa:calendario', 'polpa:kanban',
                     'polpa:recurso-list'):
            with self.subTest(rota=rota):
                resposta = self.client.get(reverse(rota))
                self.assertEqual(resposta.status_code, 200)

    def test_as_rotas_do_menu_nao_caem_no_placeholder(self):
        from django.urls import resolve

        from apps.polpa.views import ItemView

        for item in ('planejamento', 'calendario', 'quadro', 'recursos'):
            with self.subTest(item=item):
                achado = resolve(reverse('polpa:item', args=['pcp', item]))
                self.assertIsNot(
                    getattr(achado.func, 'view_class', None), ItemView,
                )

    def test_a_tela_mostra_as_parcelas_da_conta(self):
        """
        Mostrar só o resultado faz a pessoa não confiar no número — e com
        razão, porque ela não tem como conferir.
        """
        self._pedido(self.produto, Decimal('500'), PedidoVenda.Status.CONFIRMADO)

        resposta = self.client.get(reverse('polpa:planejamento'))

        self.assertContains(resposta, 'Pedidos')
        self.assertContains(resposta, 'Previsão')
        self.assertContains(resposta, 'Em produção')
        self.assertContains(resposta, 'Polpa de manga 100 g')

    def test_gerar_op_pela_sugestao(self):
        self._pedido(self.produto, Decimal('500'), PedidoVenda.Status.CONFIRMADO)

        resposta = self.client.post(reverse('polpa:planejamento-gerar'), {
            'receita': self.receita.pk, 'quantidade': '500', 'horizonte': '30',
        })

        self.assertEqual(resposta.status_code, 302)
        op = OrdemPolpa.objects.for_filial(self.filial).first()
        self.assertIsNotNone(op)
        # NASCE PLANEJADA: sugerir não é autorizar consumo.
        self.assertEqual(op.situacao, S.PLANEJADA)

    def test_programar_pela_tela(self):
        op = OrdemPolpaService.criar(
            self.filial, self.receita,
            {'quantidade_planejada': Decimal('100')}, self.usuario,
        )
        recurso = Recurso.objects.create(
            filial=self.filial, nome='Linha 1', capacidade_dia=Decimal('1000'),
        )

        self.client.post(reverse('polpa:ordem-programar', args=[op.pk]), {
            'dia': timezone.localdate().isoformat(), 'recurso': recurso.pk,
        })

        op.refresh_from_db()
        self.assertIsNotNone(op.ordem.data_inicio_prevista)
        self.assertEqual(op.recurso, recurso)

    def test_cadastrar_recurso_pela_tela(self):
        resposta = self.client.post(reverse('polpa:recurso-create'), {
            'nome': 'Envasadora', 'tipo': Recurso.Tipo.MAQUINA,
            'capacidade_dia': '2000', 'horas_dia': '8', 'setup_minutos': '30',
            'ativo': 'on',
        })

        self.assertEqual(resposta.status_code, 302)
        self.assertTrue(
            Recurso.objects.for_filial(self.filial).filter(nome='Envasadora').exists()
        )
