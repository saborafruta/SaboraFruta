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


class ATelaDaOrdemTests(OrdemBase):
    """A ficha da ordem — duas perguntas, e o número de batidas à vista."""

    def test_os_grupos_separam_o_obrigatorio_do_opcional(self):
        resposta = self.client.get(reverse('polpa:ordem-create'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'O que produzir')
        self.assertContains(resposta, 'Quando e quem')

    def test_o_select_vazio_diz_o_que_fazer(self):
        resposta = self.client.get(reverse('polpa:ordem-create'))

        self.assertContains(resposta, 'Selecione a receita')
        self.assertNotContains(resposta, '---------')

    def test_a_tela_recebe_o_rendimento_por_batida_de_cada_receita(self):
        """
        E' o que permite mostrar as batidas ENQUANTO SE DIGITA. Sai do mesmo
        `quantidade_produzida` da ficha que a producao usa para dividir a
        ordem, entao a tela e o servico nao discordam.
        """
        import json

        resposta = self.client.get(reverse('polpa:ordem-create'))

        rende = json.loads(resposta.context['rende_por_batida'])
        self.assertIn(str(self.receita.pk), rende)
        # Comparado pelo VALOR, e nao pelo texto: "1000" e "1000.000" sao o
        # mesmo numero, e prender o teste ao formato o faria reprovar por causa
        # das casas decimais que o banco devolve.
        self.assertEqual(
            Decimal(rende[str(self.receita.pk)]),
            self.receita.ficha.quantidade_produzida,
        )

    def test_os_campos_ficam_ligados_ao_calculo(self):
        """
        Sem `x-model` no widget, o painel de batidas ficaria mudo -- os campos
        sao desenhados pelo parcial generico, que nao sabe desta tela.
        """
        html = self.client.get(reverse('polpa:ordem-create')).content.decode()

        self.assertIn('x-model="receita"', html)
        self.assertIn('x-model.number="quantidade"', html)

    def test_todo_campo_do_formulario_chega_na_tela(self):
        """
        A GARANTIA DO LACO ANTIGO: agrupar por lista escrita a mao e' como se
        perde campo -- ele existe no form, nao aparece na tela, e ninguem
        entende por que nunca e' preenchido.
        """
        from apps.polpa.forms_ordem import OrdemPolpaForm

        html = self.client.get(reverse('polpa:ordem-create')).content.decode()

        for nome in OrdemPolpaForm(filial=self.filial).fields:
            self.assertIn(f'name="{nome}"', html, f'campo "{nome}" sumiu da tela')

    def test_a_tela_nao_vaza_sintaxe_de_template(self):
        """A mesma guarda das outras telas, pelo defeito que ja' foi para producao."""
        html = self.client.get(reverse('polpa:ordem-create')).content.decode()

        for resto in ('{#', '#}', '{%', '%}'):
            self.assertNotIn(resto, html, f'vazou "{resto}" no HTML')


class ATelaDeBatidasTests(OrdemBase):
    """
    Cada batelada com sua formulação, rendimento e lote de saída.

    NÃO EXISTE REGISTRO POR BATIDA no sistema, e a tela não inventa um: ela lê
    a ordem, que aponta a receita, guarda planejado e produzido e gera o lote.
    Quantas batidas ela vale é conta, não registro — e por isso o divisor
    aparece ao lado do número na tela.
    """

    def test_ordem_planejada_ainda_nao_e_batelada(self):
        """
        Nada foi misturado: nao ha' rendimento nem lote. Enche-la aqui faria a
        tela prometer producao que nao aconteceu.
        """
        self._op()

        resposta = self.client.get(reverse('polpa:batida-list'))

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(len(resposta.context['linhas']), 0)

    def test_ordem_em_producao_vira_batelada(self):
        op = self._op()
        op.situacao = S.EM_PRODUCAO
        op.save(update_fields=['situacao'])

        resposta = self.client.get(reverse('polpa:batida-list'))

        self.assertEqual(len(resposta.context['linhas']), 1)

    def test_o_numero_de_batidas_sai_da_divisao(self):
        """
        2.500 unidades numa receita que rende 1.000 por execucao sao TRES
        batidas: a terceira roda incompleta, mas roda.
        """
        op = self._op(quantidade=Decimal('2500'))
        op.situacao = S.EM_PRODUCAO
        op.save(update_fields=['situacao'])

        linha = self.client.get(reverse('polpa:batida-list')).context['linhas'][0]

        self.assertEqual(linha['batidas'], 3)
        self.assertEqual(linha['por_batida'], Decimal('1000'))

    def test_receita_com_rendimento_zerado_nao_estoura(self):
        """
        DIVISAO POR ZERO derrubaria a tela inteira por causa de UMA receita mal
        preenchida. `quantidade_produzida` e' NOT NULL no banco, entao o caso
        que existe de verdade e' o zero -- e ele passa por
        `ReceitaService.ativar`, que so' cobra o campo na ATIVACAO. A linha
        mostra travessao, que e' honesto: nao da' para dividir por zero.
        """
        op = self._op()
        op.situacao = S.EM_PRODUCAO
        op.save(update_fields=['situacao'])
        ficha = op.receita.ficha
        ficha.quantidade_produzida = Decimal('0')
        ficha.save(update_fields=['quantidade_produzida'])

        resposta = self.client.get(reverse('polpa:batida-list'))

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context['linhas'][0]['batidas'], 0)

    def test_rendimento_abaixo_do_esperado_e_marcado(self):
        """
        E' o que se procura nesta tela: a batelada que rendeu menos do que a
        receita promete e' onde o dinheiro sumiu, e some no meio das outras sem
        a marca.
        """
        op = self._op(quantidade=Decimal('1000'))
        op.situacao = S.PRODUZIDA
        op.save(update_fields=['situacao'])
        op.ordem.quantidade_produzida = Decimal('500')   # 50% de 1000
        op.ordem.save(update_fields=['quantidade_produzida'])

        linha = self.client.get(reverse('polpa:batida-list')).context['linhas'][0]

        self.assertEqual(linha['rendimento'], Decimal('50.00'))
        self.assertTrue(linha['abaixo'])

    def test_rendimento_dentro_do_esperado_nao_e_marcado(self):
        op = self._op(quantidade=Decimal('1000'))
        op.situacao = S.PRODUZIDA
        op.save(update_fields=['situacao'])
        op.ordem.quantidade_produzida = Decimal('900')   # 90% > 60% esperado
        op.ordem.save(update_fields=['quantidade_produzida'])

        linha = self.client.get(reverse('polpa:batida-list')).context['linhas'][0]

        self.assertFalse(linha['abaixo'])

    def test_o_resumo_conta_as_bateladas_abaixo(self):
        op = self._op(quantidade=Decimal('1000'))
        op.situacao = S.PRODUZIDA
        op.save(update_fields=['situacao'])
        op.ordem.quantidade_produzida = Decimal('500')
        op.ordem.save(update_fields=['quantidade_produzida'])

        resumo = self.client.get(reverse('polpa:batida-list')).context['resumo']

        self.assertEqual(resumo['bateladas'], 1)
        self.assertEqual(resumo['abaixo'], 1)

    def test_a_rota_do_menu_abre_a_tela_de_verdade(self):
        from django.urls import resolve

        from apps.polpa.views import ItemView

        endereco = reverse('polpa:item', args=['producao', 'batidas'])

        self.assertIsNot(
            getattr(resolve(endereco).func, 'view_class', None), ItemView,
        )
        self.assertEqual(endereco, reverse('polpa:batida-list'))

    def test_a_tela_nao_vaza_sintaxe_de_template(self):
        html = self.client.get(reverse('polpa:batida-list')).content.decode()

        for resto in ('{#', '#}', '{%', '%}'):
            self.assertNotIn(resto, html, f'vazou "{resto}" no HTML')


class ATelaDePerdasTests(OrdemBase):
    """
    O que saiu da fruta e não virou produto — as duas metades.

    Subproduto tem destino e pode render caixa; perda sumiu e só deixa custo.
    Os dois pesam igual na balança e são o oposto um do outro no resultado.
    """

    def _subproduto(self, **campos):
        from apps.polpa.models import Subproduto

        dados = {
            'filial': self.filial, 'ordem': self.op,
            'tipo': Subproduto.Tipo.CASCA, 'quantidade': Decimal('120'),
            'unidade': 'KG', 'destino': Subproduto.Destino.VENDA,
            'valor_recebido': Decimal('60'), 'custo_destinacao': Decimal('0'),
            'data': timezone.localdate(),
        }
        dados.update(campos)
        return Subproduto.objects.create(**dados)

    def _perda(self, **campos):
        from apps.producao.constants.enums import TipoPerda
        from apps.producao.models import PerdaProducao

        dados = {
            'ordem_producao': self.op.ordem,
            'tipo_perda': TipoPerda.PROCESSO,
            'produto': self.acabado,
            'quantidade': Decimal('30'), 'unidade_medida': 'KG',
            'impacto_custo': Decimal('45'), 'perda_evitavel': True,
            'usuario_registro': self.usuario,
        }
        dados.update(campos)
        return PerdaProducao.objects.create(**dados)

    def setUp(self):
        super().setUp()
        self.op = self._op()

    def test_a_tela_mostra_as_duas_metades_separadas(self):
        self._subproduto()
        self._perda()

        resposta = self.client.get(reverse('polpa:perda-list'))

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(len(resposta.context['subprodutos']), 1)
        self.assertEqual(len(resposta.context['perdas']), 1)

    def test_o_resultado_do_subproduto_nao_se_mistura_com_o_custo_da_perda(self):
        """
        Casca vendida e' receita; polpa derramada e' custo. Somar os dois num
        numero so' e' exatamente a conta que nao se quer.
        """
        self._subproduto(valor_recebido=Decimal('60'))
        self._perda(impacto_custo=Decimal('45'))

        resumo = self.client.get(reverse('polpa:perda-list')).context['resumo']

        self.assertEqual(resumo['resultado'], Decimal('60'))
        self.assertEqual(resumo['custo_perdido'], Decimal('45'))

    def test_descarte_com_custo_de_destinacao_da_resultado_negativo(self):
        """Bagaco no caminhao da prefeitura pesa igual e e' o oposto de bagaco vendido."""
        from apps.polpa.models import Subproduto

        self._subproduto(
            destino=Subproduto.Destino.DESCARTE,
            valor_recebido=Decimal('0'), custo_destinacao=Decimal('80'),
        )

        resumo = self.client.get(reverse('polpa:perda-list')).context['resumo']

        self.assertEqual(resumo['resultado'], Decimal('-80'))

    def test_perda_inevitavel_nao_entra_no_que_se_pode_agir(self):
        """
        Misturar a evitavel com a do processo faria a fabrica perseguir casca
        de manga.
        """
        self._perda(perda_evitavel=True)
        self._perda(perda_evitavel=False)

        resumo = self.client.get(reverse('polpa:perda-list')).context['resumo']

        self.assertEqual(resumo['perdas'], 2)
        self.assertEqual(resumo['evitaveis'], 1)

    def test_o_filtro_de_destino_avisa_que_esconde_as_perdas(self):
        """
        Tipo e destino sao do subproduto. Filtrar perda por eles devolveria
        vazio sempre, e daria a impressao de que nao ha' perda registrada.
        """
        from apps.polpa.models import Subproduto

        self._subproduto()
        self._perda()

        resposta = self.client.get(
            reverse('polpa:perda-list'),
            {'destino': Subproduto.Destino.VENDA},
        )

        self.assertTrue(resposta.context['so_subproduto'])
        self.assertEqual(len(resposta.context['perdas']), 0)
        self.assertContains(resposta, 'a lista de perdas fica de fora')

    def test_o_resumo_bate_com_o_que_a_tabela_mostra(self):
        """
        Somado sobre o que a tela JA' CARREGOU. Somar de novo no banco daria,
        com filtro aplicado, um total no topo e outro na tabela -- divergencia
        que ninguem consegue explicar depois.
        """
        from apps.polpa.models import Subproduto

        self._subproduto(tipo=Subproduto.Tipo.CASCA, quantidade=Decimal('100'))
        self._subproduto(tipo=Subproduto.Tipo.SEMENTE, quantidade=Decimal('50'))

        resposta = self.client.get(
            reverse('polpa:perda-list'), {'tipo': Subproduto.Tipo.CASCA},
        )

        self.assertEqual(len(resposta.context['subprodutos']), 1)
        self.assertEqual(resposta.context['resumo']['kg_subproduto'], Decimal('100'))

    def test_a_rota_do_menu_abre_a_tela_de_verdade(self):
        from django.urls import resolve

        from apps.polpa.views import ItemView

        endereco = reverse('polpa:item', args=['producao', 'perdas'])

        self.assertIsNot(
            getattr(resolve(endereco).func, 'view_class', None), ItemView,
        )
        self.assertEqual(endereco, reverse('polpa:perda-list'))

    def test_a_tela_nao_vaza_sintaxe_de_template(self):
        self._subproduto()
        self._perda()

        html = self.client.get(reverse('polpa:perda-list')).content.decode()

        for resto in ('{#', '#}', '{%', '%}'):
            self.assertNotIn(resto, html, f'vazou "{resto}" no HTML')


class ATelaDeQualidadeTests(OrdemBase):
    """
    A fila da qualidade.

    REPROVAR É METADE DA DECISÃO. A outra metade é dizer o que fazer com o
    material — bloquear, descartar, reprocessar, devolver. Sem isso o lote fica
    parado sem dono, e ninguém sabe se pode mexer nele. É o único estado que a
    tela destaca.
    """

    def _analise(self, **campos):
        from apps.qualidade.constants.enums import ResultadoAnalise, TipoAnalise
        from apps.qualidade.models import AnaliseQualidade

        dados = {
            'filial': self.filial,
            'tipo_analise': TipoAnalise.PRODUTO_ACABADO,
            'parametros': {'brix': 12.5},
            'resultado': ResultadoAnalise.PENDENTE,
            'responsavel_tecnico': self.usuario,
            'data_analise': timezone.now(),
        }
        dados.update(campos)
        return AnaliseQualidade.objects.create(**dados)

    def test_a_fila_lista_as_analises_da_filial(self):
        self._analise()

        resposta = self.client.get(reverse('polpa:qualidade-analises'))

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(len(resposta.context['linhas']), 1)

    def test_reprovada_sem_acao_e_o_que_a_tela_destaca(self):
        """
        E' o lote barrado que ninguem decidiu o que fazer: fica parado sem
        dono, e ninguem sabe se pode mexer nele.
        """
        from apps.qualidade.constants.enums import ResultadoAnalise

        self._analise(resultado=ResultadoAnalise.REPROVADO, acao_reprovacao='')

        resposta = self.client.get(reverse('polpa:qualidade-analises'))

        self.assertEqual(resposta.context['resumo']['sem_acao'], 1)
        self.assertTrue(resposta.context['linhas'][0]['barrada_sem_decisao'])
        self.assertContains(resposta, 'Sem ação definida')

    def test_reprovada_com_acao_nao_e_destacada(self):
        """Decidido nao e' pendencia -- destacar tudo e' nao destacar nada."""
        from apps.qualidade.constants.enums import AcaoReprovacao, ResultadoAnalise

        self._analise(
            resultado=ResultadoAnalise.REPROVADO,
            acao_reprovacao=AcaoReprovacao.DESCARTE,
        )

        resposta = self.client.get(reverse('polpa:qualidade-analises'))

        self.assertEqual(resposta.context['resumo']['sem_acao'], 0)
        self.assertFalse(resposta.context['linhas'][0]['barrada_sem_decisao'])

    def test_aprovada_com_ressalva_conta_separada_da_aprovada(self):
        """
        Sao resultados diferentes: ressalva libera o lote MAS registra desvio.
        Somar as duas esconderia quantos lotes sairam com pendencia.
        """
        from apps.qualidade.constants.enums import ResultadoAnalise

        self._analise(resultado=ResultadoAnalise.APROVADO)
        self._analise(resultado=ResultadoAnalise.APROVADO_COM_RESSALVA)

        resumo = self.client.get(reverse('polpa:qualidade-analises')).context['resumo']

        self.assertEqual(resumo['aprovadas'], 1)
        self.assertEqual(resumo['ressalva'], 1)

    def test_o_checklist_mostra_o_que_falta_medir(self):
        """
        Item pendente e' medicao que ninguem fez; nao conforme sem acao
        corretiva e' desvio anotado e esquecido.
        """
        from apps.qualidade.models import ItemAnalise

        analise = self._analise()
        ItemAnalise.objects.create(
            analise=analise, nome_parametro='Brix',
            situacao=ItemAnalise.Situacao.PENDENTE, obrigatorio=True,
        )
        ItemAnalise.objects.create(
            analise=analise, nome_parametro='pH',
            situacao=ItemAnalise.Situacao.NAO_CONFORME, acao_corretiva='',
        )

        linha = self.client.get(
            reverse('polpa:qualidade-analises'),
        ).context['linhas'][0]

        self.assertEqual(linha['itens'], 2)
        self.assertEqual(linha['pendentes'], 1)
        self.assertEqual(linha['sem_acao_corretiva'], 1)

    def test_o_filtro_por_resultado_recorta_a_fila(self):
        from apps.qualidade.constants.enums import ResultadoAnalise

        self._analise(resultado=ResultadoAnalise.APROVADO)
        self._analise(resultado=ResultadoAnalise.REPROVADO)

        resposta = self.client.get(
            reverse('polpa:qualidade-analises'),
            {'resultado': ResultadoAnalise.REPROVADO},
        )

        self.assertEqual(len(resposta.context['linhas']), 1)
        self.assertEqual(resposta.context['resumo']['total'], 1)

    def test_a_rota_do_menu_abre_a_tela_de_verdade(self):
        from django.urls import resolve

        from apps.polpa.views import ItemView

        endereco = reverse('polpa:item', args=['qualidade', 'analises'])

        self.assertIsNot(
            getattr(resolve(endereco).func, 'view_class', None), ItemView,
        )
        self.assertEqual(endereco, reverse('polpa:qualidade-analises'))

    def test_a_tela_nao_vaza_sintaxe_de_template(self):
        self._analise()

        html = self.client.get(reverse('polpa:qualidade-analises')).content.decode()

        for resto in ('{#', '#}', '{%', '%}'):
            self.assertNotIn(resto, html, f'vazou "{resto}" no HTML')


class ATelaDeRastreabilidadeTests(OrdemBase):
    """
    A porta do recall.

    A TRAVESSIA JÁ ERA TESTADA em `test_fluxo_completo`, ponta a ponta: da
    fruta na balança ao cliente que a recebeu. O que estes testes cobrem é o
    que a TELA tem de próprio — achar o lote, escolher, e não deixar vazar lote
    de outra filial.
    """

    def _lote(self, numero='LT-0001', produto=None, filial=None):
        from apps.estoque.models import LoteProduto

        return LoteProduto.objects.create(
            filial=filial or self.filial, produto=produto or self.acabado,
            numero_lote=numero, quantidade_inicial=Decimal('100'),
            quantidade_atual=Decimal('100'), custo_unitario=Decimal('1'),
            data_validade=timezone.localdate() + timedelta(days=180),
            status=LoteProduto.Status.ATIVO,
        )

    def test_sem_busca_a_tela_nao_lista_tudo(self):
        """
        Rastro de lote aleatorio nao serve para nada, e quem chega aqui ja'
        sabe qual lote esta' em questao.
        """
        self._lote()

        resposta = self.client.get(reverse('polpa:qualidade-rastreabilidade'))

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(len(resposta.context['candidatos']), 0)
        self.assertIsNone(resposta.context['lote'])

    def test_acha_o_lote_pelo_numero(self):
        self._lote(numero='LT-4471')

        resposta = self.client.get(
            reverse('polpa:qualidade-rastreabilidade'), {'busca': '4471'},
        )

        self.assertEqual(len(resposta.context['candidatos']), 1)

    def test_acha_o_lote_pelo_produto(self):
        """Quem liga reclamando diz o produto, nao o numero do lote."""
        self._lote(numero='LT-0002')

        resposta = self.client.get(
            reverse('polpa:qualidade-rastreabilidade'),
            {'busca': self.acabado.descricao[:10]},
        )

        self.assertGreaterEqual(len(resposta.context['candidatos']), 1)

    def test_escolher_o_lote_monta_o_rastro(self):
        lote = self._lote()

        resposta = self.client.get(
            reverse('polpa:qualidade-rastreabilidade'), {'lote': lote.pk},
        )

        self.assertEqual(resposta.context['lote'], lote)
        self.assertIsNotNone(resposta.context['resumo'])
        self.assertContains(resposta, 'De onde veio')
        self.assertContains(resposta, 'Para onde foi')

    def test_o_proprio_lote_nao_se_repete_nas_listas(self):
        """
        O elo de nivel 0 e' o proprio lote, e ele ja' esta' no cabecalho --
        repeti-lo nas duas colunas diria a mesma coisa tres vezes.
        """
        lote = self._lote()

        resposta = self.client.get(
            reverse('polpa:qualidade-rastreabilidade'), {'lote': lote.pk},
        )

        for elo in resposta.context['origem'] + resposta.context['destino']:
            self.assertGreater(elo.nivel, 0)

    def test_lote_de_outra_filial_nao_e_rastreavel(self):
        """
        Rastro atravessa fornecedor e cliente. Vazar por filial mostraria a
        carteira de outra unidade para quem so' queria conferir um lote.
        """
        outra = Filial.objects.create(
            empresa=self.empresa, razao_social='Segunda',
            cnpj='13345678000353', uf='RN', cidade='Mossoro',
        )
        alheio = self._lote(numero='LT-ALHEIO', filial=outra)

        resposta = self.client.get(
            reverse('polpa:qualidade-rastreabilidade'), {'lote': alheio.pk},
        )

        self.assertIsNone(resposta.context['lote'])

    def test_lote_inexistente_nao_derruba_a_tela(self):
        resposta = self.client.get(
            reverse('polpa:qualidade-rastreabilidade'), {'lote': '999999'},
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertIsNone(resposta.context['lote'])

    def test_lote_com_texto_no_lugar_do_numero_nao_derruba(self):
        """`?lote=abc` chega de link colado errado; nao pode virar erro 500."""
        resposta = self.client.get(
            reverse('polpa:qualidade-rastreabilidade'), {'lote': 'abc'},
        )

        self.assertEqual(resposta.status_code, 200)

    def test_a_rota_do_menu_abre_a_tela_de_verdade(self):
        from django.urls import resolve

        from apps.polpa.views import ItemView

        endereco = reverse('polpa:item', args=['qualidade', 'rastreabilidade'])

        self.assertIsNot(
            getattr(resolve(endereco).func, 'view_class', None), ItemView,
        )
        self.assertEqual(endereco, reverse('polpa:qualidade-rastreabilidade'))

    def test_a_tela_nao_vaza_sintaxe_de_template(self):
        lote = self._lote()

        html = self.client.get(
            reverse('polpa:qualidade-rastreabilidade'), {'lote': lote.pk},
        ).content.decode()

        for resto in ('{#', '#}', '{%', '%}'):
            self.assertNotIn(resto, html, f'vazou "{resto}" no HTML')


class ATelaDeNaoConformidadesTests(OrdemBase):
    """
    Desvio anotado sem tratativa é pior que desvio não anotado: dá a impressão
    de que alguém cuidou.
    """

    def _desvio(self, parametro='Brix', acao='', **campos):
        from apps.qualidade.constants.enums import ResultadoAnalise, TipoAnalise
        from apps.qualidade.models import AnaliseQualidade, ItemAnalise

        analise = AnaliseQualidade.objects.create(
            filial=self.filial, tipo_analise=TipoAnalise.PRODUTO_ACABADO,
            parametros={}, resultado=ResultadoAnalise.REPROVADO,
            responsavel_tecnico=self.usuario, data_analise=timezone.now(),
        )
        dados = {
            'analise': analise, 'nome_parametro': parametro,
            'situacao': ItemAnalise.Situacao.NAO_CONFORME,
            'valor_numero': Decimal('9'), 'valor_minimo': Decimal('12'),
            'acao_corretiva': acao,
        }
        dados.update(campos)
        return ItemAnalise.objects.create(**dados)

    # ── A fila ───────────────────────────────────────────────────────────

    def test_a_fila_abre_pelos_sem_tratativa(self):
        """E' a fila de trabalho; o que ja' foi tratado e' consulta."""
        self._desvio(parametro='Brix', acao='')
        self._desvio(parametro='pH', acao='Lote reprocessado')

        resposta = self.client.get(reverse('polpa:qualidade-nao-conformidades'))

        itens = resposta.context['itens']
        self.assertEqual(len(itens), 1)
        self.assertEqual(itens[0].nome_parametro, 'Brix')

    def test_da_para_ver_as_ja_tratadas(self):
        self._desvio(parametro='pH', acao='Lote reprocessado')

        resposta = self.client.get(
            reverse('polpa:qualidade-nao-conformidades'), {'situacao': 'tratadas'},
        )

        self.assertEqual(len(resposta.context['itens']), 1)

    def test_o_resumo_conta_todos_e_nao_so_o_filtro(self):
        """
        O topo responde "quanto falta". Um total que muda com o filtro nao
        responde isso.
        """
        self._desvio(parametro='Brix', acao='')
        self._desvio(parametro='pH', acao='Reprocessado')

        resposta = self.client.get(reverse('polpa:qualidade-nao-conformidades'))

        self.assertEqual(len(resposta.context['itens']), 1)
        self.assertEqual(resposta.context['resumo']['total'], 2)
        self.assertEqual(resposta.context['resumo']['sem_acao'], 1)
        self.assertEqual(resposta.context['resumo']['tratadas'], 1)

    def test_conforme_nao_e_desvio(self):
        from apps.qualidade.models import ItemAnalise

        self._desvio(situacao=ItemAnalise.Situacao.CONFORME)

        resposta = self.client.get(reverse('polpa:qualidade-nao-conformidades'))

        self.assertEqual(len(resposta.context['itens']), 0)

    # ── A tratativa ──────────────────────────────────────────────────────

    def test_registrar_a_tratativa_carimba_quem_e_quando(self):
        """
        Guardar so' o texto deixaria a auditoria com uma frase sem dono -- e
        responsavel e' o primeiro registro que a fiscalizacao pede.
        """
        desvio = self._desvio()

        self.client.post(reverse('polpa:qualidade-nao-conformidades'), {
            'item': desvio.pk, 'acao_corretiva': 'Carga devolvida ao produtor',
        })

        desvio.refresh_from_db()
        self.assertEqual(desvio.acao_corretiva, 'Carga devolvida ao produtor')
        self.assertEqual(desvio.acao_responsavel_id, self.usuario.pk)
        self.assertIsNotNone(desvio.acao_em)

    def test_tratativa_vazia_nao_grava_e_avisa(self):
        """Sem o que foi feito, o desvio segue em aberto."""
        desvio = self._desvio()

        resposta = self.client.post(
            reverse('polpa:qualidade-nao-conformidades'),
            {'item': desvio.pk, 'acao_corretiva': '   '},
            follow=True,
        )

        desvio.refresh_from_db()
        self.assertEqual(desvio.acao_corretiva, '')
        self.assertContains(resposta, 'Escreva o que foi feito')

    def test_texto_igual_ao_que_ja_esta_nao_recarimba_a_data(self):
        """
        Reabrir a tela e salvar sem mudar nada moveria a data para hoje, e a
        auditoria perderia QUANDO o desvio foi de fato tratado.
        """
        desvio = self._desvio(acao='Reprocessado')
        desvio.acao_em = timezone.now() - timedelta(days=3)
        desvio.save(update_fields=['acao_em'])
        antes = desvio.acao_em

        self.client.post(reverse('polpa:qualidade-nao-conformidades'), {
            'item': desvio.pk, 'acao_corretiva': 'Reprocessado',
        })

        desvio.refresh_from_db()
        self.assertEqual(desvio.acao_em, antes)

    def test_desvio_de_outra_filial_nao_e_tratavel(self):
        """
        `ItemAnalise` nao tem filial propria -- ela pendura na analise. Sem o
        recorte, um id colado a mao trataria desvio de outra unidade.
        """
        from apps.qualidade.constants.enums import ResultadoAnalise, TipoAnalise
        from apps.qualidade.models import AnaliseQualidade, ItemAnalise

        outra = Filial.objects.create(
            empresa=self.empresa, razao_social='Segunda',
            cnpj='13345678000434', uf='RN', cidade='Mossoro',
        )
        analise = AnaliseQualidade.objects.create(
            filial=outra, tipo_analise=TipoAnalise.PRODUTO_ACABADO,
            parametros={}, resultado=ResultadoAnalise.REPROVADO,
            responsavel_tecnico=self.usuario, data_analise=timezone.now(),
        )
        alheio = ItemAnalise.objects.create(
            analise=analise, nome_parametro='Brix',
            situacao=ItemAnalise.Situacao.NAO_CONFORME,
        )

        resposta = self.client.post(
            reverse('polpa:qualidade-nao-conformidades'),
            {'item': alheio.pk, 'acao_corretiva': 'Nao deveria gravar'},
        )

        alheio.refresh_from_db()
        self.assertEqual(resposta.status_code, 404)
        self.assertEqual(alheio.acao_corretiva, '')

    def test_a_rota_do_menu_abre_a_tela_de_verdade(self):
        from django.urls import resolve

        from apps.polpa.views import ItemView

        endereco = reverse('polpa:item', args=['qualidade', 'nao-conformidades'])

        self.assertIsNot(
            getattr(resolve(endereco).func, 'view_class', None), ItemView,
        )
        self.assertEqual(endereco, reverse('polpa:qualidade-nao-conformidades'))

    def test_a_tela_nao_vaza_sintaxe_de_template(self):
        self._desvio()

        html = self.client.get(
            reverse('polpa:qualidade-nao-conformidades'),
        ).content.decode()

        for resto in ('{#', '#}', '{%', '%}'):
            self.assertNotIn(resto, html, f'vazou "{resto}" no HTML')


class OLaudoTests(OrdemBase):
    """
    O laudo é o documento que a fábrica assina dizendo que o lote foi
    analisado, deu determinado resultado, e que ela responde por isso.

    GERADO, E NÃO GUARDADO: o PDF é montado na hora a partir da análise. As
    regras do documento vivem no serviço, e é lá que os testes batem — separar
    `dados()` do `pdf()` existe justamente para não precisar abrir binário para
    conferir regra.
    """

    def _analise(self, resultado=None, **campos):
        from apps.qualidade.constants.enums import ResultadoAnalise, TipoAnalise
        from apps.qualidade.models import AnaliseQualidade

        dados = {
            'filial': self.filial, 'tipo_analise': TipoAnalise.PRODUTO_ACABADO,
            'parametros': {}, 'responsavel_tecnico': self.usuario,
            'data_analise': timezone.now(),
            'resultado': resultado or ResultadoAnalise.APROVADO,
        }
        dados.update(campos)
        return AnaliseQualidade.objects.create(**dados)

    def _parametro(self, analise, **campos):
        from apps.qualidade.models import ItemAnalise

        dados = {
            'analise': analise, 'nome_parametro': 'Brix',
            'valor_numero': Decimal('14'), 'valor_minimo': Decimal('12'),
            'situacao': ItemAnalise.Situacao.CONFORME,
        }
        dados.update(campos)
        return ItemAnalise.objects.create(**dados)

    # ── A regra que o documento impõe ────────────────────────────────────

    def test_analise_pendente_nao_vira_laudo(self):
        """
        Assinar que o lote foi analisado quando ninguem concluiu e o oposto do
        que o documento serve para fazer.
        """
        from apps.core.services.exceptions import DomainError
        from apps.qualidade.constants.enums import ResultadoAnalise
        from apps.qualidade.services.laudo_service import LaudoService

        analise = self._analise(resultado=ResultadoAnalise.PENDENTE)

        self.assertFalse(LaudoService.pode_emitir(analise))
        with self.assertRaises(DomainError):
            LaudoService.dados(analise)

    def test_reprovado_tambem_gera_laudo(self):
        """
        Laudo nao e certificado de aprovacao: e o registro da analise. O lote
        reprovado precisa do documento tanto quanto o aprovado, porque e ele
        que acompanha a devolucao.
        """
        from apps.qualidade.constants.enums import ResultadoAnalise
        from apps.qualidade.services.laudo_service import LaudoService

        analise = self._analise(resultado=ResultadoAnalise.REPROVADO)

        self.assertTrue(LaudoService.pode_emitir(analise))

    def test_o_desvio_sem_tratativa_sai_marcado_e_nao_omitido(self):
        """
        Omitir seria o sistema ajudando a esconder. E justamente o desvio, com
        a acao ao lado, que prova que a fabrica viu e tratou.
        """
        from apps.qualidade.models import ItemAnalise
        from apps.qualidade.services.laudo_service import LaudoService

        analise = self._analise()
        self._parametro(
            analise, nome_parametro='pH',
            situacao=ItemAnalise.Situacao.NAO_CONFORME, acao_corretiva='',
        )

        dados = LaudoService.dados(analise)

        self.assertEqual(len(dados['nao_conformes']), 1)
        self.assertEqual(len(dados['desvios_sem_acao']), 1)

    def test_o_numero_do_laudo_e_derivado_e_estavel(self):
        """
        Contador proprio precisaria de tabela e daria buracos quando alguem
        gerasse e nao usasse. Derivado, aponta de volta para a analise.
        """
        from apps.qualidade.services.laudo_service import LaudoService

        analise = self._analise()

        numero = LaudoService.numero(analise)
        self.assertEqual(numero, LaudoService.numero(analise))
        self.assertIn(str(analise.pk), numero)

    def test_analise_sem_checklist_usa_os_parametros_livres(self):
        """Laudo de analise antiga sairia em branco sem esta queda."""
        from apps.qualidade.services.laudo_service import LaudoService

        analise = self._analise(parametros={'brix': 12.5, 'ph': 3.8})

        dados = LaudoService.dados(analise)

        self.assertEqual(dados['parametros_livres'], {'brix': 12.5, 'ph': 3.8})

    def test_com_checklist_os_parametros_livres_ficam_de_fora(self):
        """Mostrar os dois diria a mesma medicao duas vezes, em dois formatos."""
        from apps.qualidade.services.laudo_service import LaudoService

        analise = self._analise(parametros={'brix': 12.5})
        self._parametro(analise)

        dados = LaudoService.dados(analise)

        self.assertEqual(dados['parametros_livres'], {})
        self.assertEqual(len(dados['itens']), 1)

    # ── O PDF e a tela ───────────────────────────────────────────────────

    def test_o_pdf_sai_pdf(self):
        analise = self._analise()
        self._parametro(analise)

        resposta = self.client.get(
            reverse('polpa:qualidade-laudo-pdf', args=[analise.pk]),
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta['Content-Type'], 'application/pdf')
        self.assertTrue(resposta.content.startswith(b'%PDF'))

    def test_pdf_de_pendente_avisa_em_vez_de_entregar_papel_em_branco(self):
        from apps.qualidade.constants.enums import ResultadoAnalise

        analise = self._analise(resultado=ResultadoAnalise.PENDENTE)

        resposta = self.client.get(
            reverse('polpa:qualidade-laudo-pdf', args=[analise.pk]), follow=True,
        )

        self.assertContains(resposta, 'conclua a análise')

    def test_laudo_de_outra_filial_nao_sai(self):
        analise = self._analise()
        outra = Filial.objects.create(
            empresa=self.empresa, razao_social='Segunda',
            cnpj='13345678000515', uf='RN', cidade='Mossoro',
        )
        analise.filial = outra
        analise.save(update_fields=['filial'])

        resposta = self.client.get(
            reverse('polpa:qualidade-laudo-pdf', args=[analise.pk]),
        )

        self.assertEqual(resposta.status_code, 404)

    def test_a_lista_esconde_pendentes_mas_diz_quantas_sao(self):
        """Sem o aviso, quem procura o laudo conclui que a analise sumiu."""
        from apps.qualidade.constants.enums import ResultadoAnalise

        self._analise(resultado=ResultadoAnalise.PENDENTE)
        self._analise(resultado=ResultadoAnalise.APROVADO)

        resposta = self.client.get(reverse('polpa:qualidade-laudos'))

        self.assertEqual(len(resposta.context['linhas']), 1)
        self.assertEqual(resposta.context['pendentes'], 1)
        self.assertContains(resposta, 'ainda pendente')

    def test_a_lista_avisa_do_desvio_sem_tratativa_antes_de_emitir(self):
        """O cliente vai perguntar; e melhor descobrir aqui."""
        from apps.qualidade.models import ItemAnalise

        analise = self._analise()
        self._parametro(
            analise, situacao=ItemAnalise.Situacao.NAO_CONFORME,
            acao_corretiva='',
        )

        resposta = self.client.get(reverse('polpa:qualidade-laudos'))

        self.assertEqual(resposta.context['linhas'][0]['sem_acao'], 1)
        self.assertContains(resposta, 'vai sair no laudo')

    def test_a_rota_do_menu_abre_a_tela_de_verdade(self):
        from django.urls import resolve

        from apps.polpa.views import ItemView

        endereco = reverse('polpa:item', args=['qualidade', 'laudos'])

        self.assertIsNot(
            getattr(resolve(endereco).func, 'view_class', None), ItemView,
        )
        self.assertEqual(endereco, reverse('polpa:qualidade-laudos'))

    def test_a_tela_nao_vaza_sintaxe_de_template(self):
        self._analise()

        html = self.client.get(reverse('polpa:qualidade-laudos')).content.decode()

        for resto in ('{#', '#}', '{%', '%}'):
            self.assertNotIn(resto, html, 'vazou sintaxe de template no HTML')
