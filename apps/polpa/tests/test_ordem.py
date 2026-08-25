"""
A ordem de produção: estados, necessidade, lote e validade.

O QUE ESTES TESTES CERCAM:

  · NÃO EXISTE SEGUNDA OP. A ordem do vertical anda junto com a
    `producao.OrdemProducao`, que é quem consome estoque e cria o lote. Se
    as duas puderem discordar sobre onde a ordem está, o saldo do ERP vira
    ficção;

  · OS SETE ESTADOS têm caminho definido. Produzir sem liberar, retomar uma
    ordem cancelada ou pausar sem motivo são os enganos que a tabela de
    transições fecha — a alternativa é um `if` em cada tela, e é no `if`
    esquecido que o caminho errado se abre;

  · A NECESSIDADE É CALCULADA, não gravada: sai da versão da receita presa
    na OP e da quantidade planejada, e por isso dá sempre o mesmo número;

  · LIBERAR NÃO TRAVA POR FALTA. A fruta chega durante o dia; travar faria
    a fábrica registrar a OP depois de produzir, que é o mesmo que não
    registrar. A trava real está no encerramento, onde o estoque some;

  · A VALIDADE DO LOTE SAI DO PRAZO DO PRODUTO. Era ela que faltava no ERP:
    a data existia no lote, mas ninguém a calculava.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import DomainError
from apps.estoque.models import Estoque, LoteProduto
from apps.polpa.models import EtapaReceita, FichaProduto, OrdemPolpa
from apps.polpa.services import CatalogoService, OrdemPolpaService, ReceitaService
from apps.producao.models import ItemFichaTecnica, OrdemProducao
from apps.produtos.models import UnidadeMedida, UnidadeMedidaFilial

T = FichaProduto.Tipo
S = OrdemPolpa.Situacao


class OrdemBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Polpas OP LTDA', nome_fantasia='OP',
            cnpj='13345678000191', segmento='polpa_frutas',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='13345678000272',
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
            email='chefe@op.local', nome='Chefe', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        self.acabado = self._item(
            T.POLPA, 'Polpa de manga 100 g', validade_dias=180,
            peso_liquido=Decimal('0.100'), quantidade_por_embalagem=Decimal('50'),
        )
        self.manga = self._item(T.FRUTA, 'Manga in natura', custo=Decimal('1.50'))
        self.pote = self._item(T.POTE, 'Pote 100 g', custo=Decimal('0.20'))
        self.receita = self._receita()

    def _item(self, tipo, descricao, custo=Decimal('0'), **extras):
        dados = {
            'tipo': tipo, 'descricao': descricao, 'codigo': descricao[:10],
            'unidade_medida': self.unidade, 'preco_custo': custo,
        }
        dados.update(extras)
        ficha = CatalogoService.salvar(self.filial, dados)
        return ficha.produto

    def _receita(self, ativar=True):
        receita = ReceitaService.criar(self.filial, self.acabado, {
            'descricao': 'Polpa de manga 100 g', 'versao': '1.0',
            'quantidade_produzida': Decimal('1000'),
            'rendimento_esperado': Decimal('60'),
        })
        ItemFichaTecnica.objects.create(
            ficha=receita.ficha, materia_prima=self.manga,
            quantidade=Decimal('100'), perda_prevista=Decimal('0'),
        )
        ItemFichaTecnica.objects.create(
            ficha=receita.ficha, materia_prima=self.pote,
            quantidade=Decimal('1000'), perda_prevista=Decimal('0'),
        )
        EtapaReceita.objects.create(receita=receita, ordem=1, nome='Despolpa')
        if ativar:
            ReceitaService.ativar(receita)
        return receita

    def _op(self, quantidade=Decimal('1000'), **campos):
        dados = {'quantidade_planejada': quantidade}
        dados.update(campos)
        return OrdemPolpaService.criar(self.filial, self.receita, dados, self.usuario)

    def _estoque(self, produto, quantidade):
        """Coloca saldo disponível para a OP encontrar."""
        estoque, _ = Estoque.objects.get_or_create(
            produto=produto, filial=self.filial,
        )
        estoque.quantidade_atual = quantidade
        estoque.quantidade_reservada = Decimal('0')
        # O disponível é derivado (atual − reservada) e recalculado pelo
        # próprio modelo — gravá-lo à mão daria um saldo que não confere
        # com as duas colunas que o produzem.
        estoque.atualizar_disponivel()
        estoque.save()
        LoteProduto.objects.create(
            filial=self.filial, produto=produto,
            numero_lote=f'L-{produto.pk}', quantidade_inicial=quantidade,
            quantidade_atual=quantidade, custo_unitario=produto.preco_custo,
            data_validade=timezone.localdate() + timedelta(days=365),
            status=LoteProduto.Status.ATIVO,
        )
        return estoque


class AberturaTests(OrdemBase):
    """A ordem nasce de uma receita ativa."""

    def test_criar_op_cria_a_ordem_do_erp(self):
        op = self._op()

        self.assertEqual(OrdemProducao.objects.count(), 1)
        self.assertEqual(op.ordem.ficha_tecnica, self.receita.ficha)
        self.assertEqual(op.situacao, S.PLANEJADA)
        self.assertEqual(op.ordem.status, OrdemProducao.Status.RASCUNHO)

    def test_o_numero_traz_o_ano(self):
        op = self._op()

        ano = timezone.localdate().year
        self.assertTrue(op.numero.startswith(f'OP-{ano}-'))

    def test_a_numeracao_nao_repete(self):
        primeira = self._op()
        segunda = self._op()

        self.assertNotEqual(primeira.numero, segunda.numero)

    def test_receita_em_rascunho_nao_produz(self):
        """
        Uma versão em rascunho é justamente a que alguém está mexendo —
        produzir por ela é produzir por uma fórmula não decidida.
        """
        self.acabado2 = self._item(
            T.POLPA, 'Polpa de acerola', validade_dias=180, codigo='PAC',
        )
        rascunho = ReceitaService.criar(self.filial, self.acabado2, {
            'descricao': 'Acerola', 'versao': '1.0',
            'quantidade_produzida': Decimal('100'),
        })

        with self.assertRaises(DomainError) as erro:
            OrdemPolpaService.criar(
                self.filial, rascunho,
                {'quantidade_planejada': Decimal('10')}, self.usuario,
            )

        self.assertIn('não está ativa', str(erro.exception))

    def test_quantidade_zerada_nao_abre_op(self):
        with self.assertRaises(DomainError):
            self._op(quantidade=Decimal('0'))

    def test_a_receita_fica_presa_na_op(self):
        """
        A versão ativa de hoje pode não ser a que produziu — e é a versão que
        explica o lote seis meses depois.
        """
        op = self._op()
        nova = ReceitaService.nova_versao(self.receita)
        ReceitaService.ativar(nova)

        op.refresh_from_db()
        self.assertEqual(op.receita, self.receita)


class NecessidadeTests(OrdemBase):
    """O que a ordem precisa, e o que existe."""

    def test_a_necessidade_escala_pela_quantidade_planejada(self):
        op = self._op(quantidade=Decimal('2000'))

        necessidade = OrdemPolpaService.necessidade(op)

        por_nome = {
            l['produto'].descricao: l for l in
            necessidade['ingredientes'] + necessidade['embalagens']
        }
        # A receita rende 1.000 un com 100 kg; 2.000 un pedem 200 kg.
        self.assertEqual(por_nome['Manga in natura']['necessario'], Decimal('200.000'))
        self.assertEqual(por_nome['Pote 100 g']['necessario'], Decimal('2000.000'))

    def test_ingrediente_e_embalagem_vem_separados(self):
        """Quem separa a fruta na câmara não é quem separa o pote."""
        op = self._op()

        necessidade = OrdemPolpaService.necessidade(op)

        self.assertEqual(len(necessidade['ingredientes']), 1)
        self.assertEqual(len(necessidade['embalagens']), 1)

    def test_a_falta_aparece_quando_o_estoque_nao_cobre(self):
        op = self._op()
        self._estoque(self.manga, Decimal('40'))

        necessidade = OrdemPolpaService.necessidade(op)

        faltas = {f['produto'].descricao: f['falta'] for f in necessidade['faltas']}
        self.assertEqual(faltas['Manga in natura'], Decimal('60.000'))

    def test_sem_falta_quando_o_estoque_cobre(self):
        op = self._op()
        self._estoque(self.manga, Decimal('500'))
        self._estoque(self.pote, Decimal('5000'))

        necessidade = OrdemPolpaService.necessidade(op)

        self.assertEqual(necessidade['faltas'], [])

    def test_a_perda_prevista_entra_na_necessidade(self):
        """Comprar só o líquido é o que faz faltar fruta no meio da batida."""
        op = self._op()
        item = op.ordem.ficha_tecnica.itens.get(materia_prima=self.manga)
        item.perda_prevista = Decimal('10')
        item.save()

        necessidade = OrdemPolpaService.necessidade(op)

        manga = next(
            l for l in necessidade['ingredientes']
            if l['produto'] == self.manga
        )
        self.assertEqual(manga['necessario'], Decimal('110.000'))


class EstadoTests(OrdemBase):
    """Os sete estados e os caminhos entre eles."""

    def test_liberar_nao_trava_por_falta_de_estoque(self):
        """
        A fruta chega durante o dia. Travar faria a fábrica registrar a OP
        depois de produzir — que é o mesmo que não registrar.
        """
        op = self._op()

        necessidade = OrdemPolpaService.liberar(op, self.usuario)

        op.refresh_from_db()
        self.assertEqual(op.situacao, S.LIBERADA)
        self.assertTrue(necessidade['faltas'])

    def test_a_situacao_anda_junto_com_o_status_do_erp(self):
        """Duas verdades sobre onde a OP está é o pior resultado possível."""
        op = self._op()

        OrdemPolpaService.mover(op, S.LIBERADA, self.usuario)

        op.ordem.refresh_from_db()
        self.assertEqual(op.ordem.status, OrdemProducao.Status.ABERTA)

    def test_pausada_continua_em_producao_para_o_erp(self):
        op = self._op()
        OrdemPolpaService.mover(op, S.LIBERADA, self.usuario)
        OrdemPolpaService.mover(op, S.EM_PRODUCAO, self.usuario)

        OrdemPolpaService.mover(op, S.PAUSADA, self.usuario, {'motivo': 'Acabou a fruta'})

        op.ordem.refresh_from_db()
        self.assertEqual(op.situacao, S.PAUSADA)
        self.assertEqual(op.ordem.status, OrdemProducao.Status.EM_PRODUCAO)

    def test_nao_pula_de_planejada_para_producao(self):
        """Produzir sem liberar é o engano que a tabela de transições fecha."""
        op = self._op()

        with self.assertRaises(DomainError):
            OrdemPolpaService.mover(op, S.EM_PRODUCAO, self.usuario)

    def test_pausar_exige_motivo(self):
        op = self._op()
        OrdemPolpaService.mover(op, S.LIBERADA, self.usuario)
        OrdemPolpaService.mover(op, S.EM_PRODUCAO, self.usuario)

        with self.assertRaises(DomainError) as erro:
            OrdemPolpaService.mover(op, S.PAUSADA, self.usuario)

        self.assertIn('parou', str(erro.exception))

    def test_o_tempo_parado_e_somado_e_nao_substituido(self):
        """
        Uma OP que parou três vezes tem três pausas, e o total é o que
        explica por que a batida levou o dobro do previsto.
        """
        op = self._op()
        OrdemPolpaService.mover(op, S.LIBERADA, self.usuario)
        OrdemPolpaService.mover(op, S.EM_PRODUCAO, self.usuario)

        for _ in range(2):
            OrdemPolpaService.mover(op, S.PAUSADA, self.usuario, {'motivo': 'Manutencao'})
            op.pausada_em = timezone.now() - timedelta(minutes=30)
            op.save(update_fields=['pausada_em'])
            OrdemPolpaService.mover(op, S.EM_PRODUCAO, self.usuario)

        self.assertGreaterEqual(op.minutos_parados, 60)

    def test_iniciar_grava_a_data_de_inicio(self):
        op = self._op()
        OrdemPolpaService.mover(op, S.LIBERADA, self.usuario)

        OrdemPolpaService.mover(op, S.EM_PRODUCAO, self.usuario)

        op.ordem.refresh_from_db()
        self.assertIsNotNone(op.ordem.data_inicio_real)

    def test_cancelar_exige_motivo_e_encerra(self):
        op = self._op()

        with self.assertRaises(DomainError):
            OrdemPolpaService.mover(op, S.CANCELADA, self.usuario)

        OrdemPolpaService.mover(op, S.CANCELADA, self.usuario, {'motivo': 'Pedido cancelado'})
        op.refresh_from_db()
        self.assertTrue(op.encerrada)
        self.assertIn('Pedido cancelado', op.observacao)

    def test_ordem_encerrada_nao_se_move_mais(self):
        op = self._op()
        OrdemPolpaService.mover(op, S.CANCELADA, self.usuario, {'motivo': 'Engano'})

        with self.assertRaises(DomainError):
            OrdemPolpaService.mover(op, S.LIBERADA, self.usuario)

    def test_produzida_nao_e_uma_troca_de_situacao(self):
        """
        Encerrar consome estoque e cria lote: passar por "mudar situação"
        deixaria a produção sem baixa e sem lote.
        """
        op = self._op()
        OrdemPolpaService.mover(op, S.LIBERADA, self.usuario)
        OrdemPolpaService.mover(op, S.EM_PRODUCAO, self.usuario)

        with self.assertRaises(DomainError) as erro:
            OrdemPolpaService.mover(op, S.PRODUZIDA, self.usuario)

        self.assertIn('encerramento', str(erro.exception))


class ConclusaoTests(OrdemBase):
    """O lote que nasce da produção."""

    def _pronta(self):
        op = self._op()
        self._estoque(self.manga, Decimal('500'))
        self._estoque(self.pote, Decimal('5000'))
        OrdemPolpaService.mover(op, S.LIBERADA, self.usuario)
        OrdemPolpaService.mover(op, S.EM_PRODUCAO, self.usuario)
        return op

    def test_concluir_cria_o_lote_com_validade_do_produto(self):
        """
        Era isto que faltava no ERP: a data existia no lote, mas ninguém a
        calculava — e por isso era digitada à mão a cada produção.
        """
        op = self._pronta()

        OrdemPolpaService.concluir(op, self.usuario, Decimal('1000'))

        op.refresh_from_db()
        self.assertEqual(op.situacao, S.PRODUZIDA)
        lote = op.lote
        self.assertIsNotNone(lote)
        self.assertEqual(
            lote.data_validade, timezone.localdate() + timedelta(days=180),
        )

    def test_concluir_baixa_a_materia_prima(self):
        """Produção que não mexe no saldo faz o estoque do ERP virar ficção."""
        op = self._pronta()
        antes = Estoque.objects.get(
            produto=self.manga, filial=self.filial,
        ).quantidade_atual

        OrdemPolpaService.concluir(op, self.usuario, Decimal('1000'))

        depois = Estoque.objects.get(
            produto=self.manga, filial=self.filial,
        ).quantidade_atual
        self.assertEqual(antes - depois, Decimal('100.000'))

    def test_produto_sem_prazo_gera_lote_sem_validade(self):
        """
        Uma data inventada aqui viraria etiqueta impressa, e ninguém saberia
        que foi inventada. A tela avisa em voz alta.
        """
        ficha = self.acabado.ficha_polpa
        ficha.validade_dias = None
        ficha.save(update_fields=['validade_dias'])
        op = self._pronta()

        OrdemPolpaService.concluir(op, self.usuario, Decimal('1000'))

        op.refresh_from_db()
        self.assertIsNone(op.lote.data_validade)

    def test_nao_conclui_ordem_planejada(self):
        op = self._op()

        with self.assertRaises(DomainError):
            OrdemPolpaService.concluir(op, self.usuario, Decimal('10'))

    def test_quantidade_zerada_nao_encerra(self):
        op = self._pronta()

        with self.assertRaises(DomainError):
            OrdemPolpaService.concluir(op, self.usuario, Decimal('0'))

    def test_a_ordem_em_qualidade_pode_ser_concluida(self):
        """
        Entre produzir e vender existe a análise — e é dela que a OP volta
        para ser encerrada.
        """
        op = self._pronta()
        OrdemPolpaService.mover(op, S.QUALIDADE, self.usuario)

        OrdemPolpaService.concluir(op, self.usuario, Decimal('1000'))

        op.refresh_from_db()
        self.assertEqual(op.situacao, S.PRODUZIDA)


class TelasOrdemTests(OrdemBase):
    """As telas da produção."""

    def test_a_lista_abre_no_que_esta_em_aberto(self):
        aberta = self._op()
        encerrada = self._op()
        OrdemPolpaService.mover(encerrada, S.CANCELADA, self.usuario, {'motivo': 'Engano'})

        resposta = self.client.get(reverse('polpa:ordem-list'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, aberta.numero)
        self.assertNotContains(resposta, encerrada.numero)

    def test_com_todas_a_encerrada_aparece(self):
        encerrada = self._op()
        OrdemPolpaService.mover(encerrada, S.CANCELADA, self.usuario, {'motivo': 'Engano'})

        resposta = self.client.get(reverse('polpa:ordem-list') + '?todas=1')

        self.assertContains(resposta, encerrada.numero)

    def test_a_rota_do_menu_nao_cai_no_placeholder(self):
        from django.urls import resolve

        from apps.polpa.views import ItemView

        achado = resolve(reverse('polpa:item', args=['producao', 'ordens']))

        self.assertIsNot(getattr(achado.func, 'view_class', None), ItemView)

    def test_o_detalhe_mostra_a_necessidade_e_a_validade_prevista(self):
        op = self._op()

        resposta = self.client.get(reverse('polpa:ordem-detail', args=[op.pk]))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Necessidade de insumos')
        self.assertContains(resposta, 'Manga in natura')
        self.assertContains(resposta, 'Pote 100 g')
        self.assertIsNotNone(resposta.context['validade_prevista'])

    def test_liberar_pela_tela_avisa_o_que_falta(self):
        op = self._op()

        resposta = self.client.post(
            reverse('polpa:ordem-mover', args=[op.pk]),
            {'destino': S.LIBERADA}, follow=True,
        )

        op.refresh_from_db()
        self.assertEqual(op.situacao, S.LIBERADA)
        mensagens = [str(m) for m in resposta.context['messages']]
        self.assertTrue(any('Falta em estoque' in m for m in mensagens))

    def test_criar_op_pela_tela(self):
        resposta = self.client.post(reverse('polpa:ordem-create'), {
            'receita': self.receita.pk, 'quantidade_planejada': '500',
        })

        self.assertEqual(resposta.status_code, 302)
        op = OrdemPolpa.objects.for_filial(self.filial).first()
        self.assertIsNotNone(op)
        self.assertEqual(op.quantidade_planejada, Decimal('500'))

    def test_termino_antes_do_inicio_e_recusado(self):
        resposta = self.client.post(reverse('polpa:ordem-create'), {
            'receita': self.receita.pk, 'quantidade_planejada': '500',
            'data_inicio_prevista': '2026-08-25T08:00',
            'data_fim_prevista': '2026-08-24T08:00',
        })

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'antes do início')
