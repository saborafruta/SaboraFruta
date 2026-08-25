"""
Subprodutos e resíduos: o que sai da batida além do produto.

Não existia nada. `ApontamentoEtapa.perda` é peso que entrou menos peso que
saiu — um número, sem nome e sem destino. Ele responde "quanto sumiu" e não
responde as duas que decidem dinheiro: o que era, e para onde foi.

E são o oposto uma da outra no resultado: bagaço vendido para o pecuarista é
RECEITA; bagaço no caminhão da prefeitura é CUSTO, porque destinação de
resíduo orgânico se paga. Os dois pesam igual na balança, e um relatório de
"perdas" que junta os dois esconde exatamente isso.

O que os testes cercam:

  · NÃO SOMA À PERDA, EXPLICA A PERDA. O peso do subproduto já está dentro da
    perda da etapa; somá-los contaria a mesma casca duas vezes;
  · O DESTINO MUDA O DINHEIRO. Venda entra, descarte sai, doação e uso interno
    são zero — e valor digitado num destino que não o usa é apagado, senão o
    relatório soma uma venda que não houve;
  · REAPROVEITAMENTO E USO INTERNO ENTRAM NO ESTOQUE. Sem isso "aproveitamos a
    casca" é uma frase no relatório, e no mês seguinte se compra ração que já
    estava no pátio;
  · VENDA E DOAÇÃO NÃO ENTRAM. O material saiu no mesmo ato, e creditar para
    baixar depois inventaria um saldo que nunca existiu;
  · PERDA SEM NOME é a pergunta que fica: sobrou peso que ninguém sabe para
    onde foi?
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import DomainError
from apps.estoque.models import Estoque, LoteProduto
from apps.polpa.models import (
    EtapaReceita, FichaProduto, OrdemPolpa, Subproduto,
)
from apps.polpa.services import CatalogoService, OrdemPolpaService, ReceitaService
from apps.polpa.services.processo import ProcessoService
from apps.polpa.services.subproduto import SubprodutoService
from apps.producao.models import ItemFichaTecnica
from apps.produtos.models import UnidadeMedida, UnidadeMedidaFilial

T = FichaProduto.Tipo
S = OrdemPolpa.Situacao
D = Subproduto.Destino
TP = Subproduto.Tipo
ZERO = Decimal('0')


class SubprodutoBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Polpas Subproduto LTDA', nome_fantasia='Sub',
            cnpj='73345678000191', segmento='polpa_frutas',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='73345678000272',
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
            email='chefe@sub.local', nome='Chefe', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )

    def setUp(self):
        self.acabado = self._item(
            T.POLPA, 'Polpa de manga 100 g', validade_dias=180,
            peso_liquido=Decimal('0.100'),
        )
        self.manga = self._item(T.FRUTA, 'Manga in natura', custo=Decimal('2'))
        self.pote = self._item(T.POTE, 'Pote 100 g', custo=Decimal('0.50'))
        # A casca cadastrada como matéria-prima: é o que permite ela voltar
        # ao estoque e ser consumida por outra receita.
        self.casca = self._item(T.INGREDIENTE, 'Casca de manga', custo=Decimal('0.10'))
        self.receita = self._receita()

    def _item(self, tipo, descricao, custo=Decimal('0'), **extras):
        dados = {
            'tipo': tipo, 'descricao': descricao, 'codigo': descricao[:10],
            'unidade_medida': self.unidade, 'preco_custo': custo,
        }
        dados.update(extras)
        return CatalogoService.salvar(self.filial, dados).produto

    def _receita(self):
        receita = ReceitaService.criar(self.filial, self.acabado, {
            'descricao': 'Polpa de manga 100 g', 'versao': '1.0',
            'quantidade_produzida': Decimal('1000'),
            'rendimento_esperado': Decimal('60'),
        })
        ItemFichaTecnica.objects.create(
            ficha=receita.ficha, materia_prima=self.manga,
            quantidade=Decimal('100'), perda_prevista=ZERO,
        )
        ItemFichaTecnica.objects.create(
            ficha=receita.ficha, materia_prima=self.pote,
            quantidade=Decimal('1000'), perda_prevista=ZERO,
        )
        EtapaReceita.objects.create(receita=receita, ordem=1, nome='Despolpa')
        ReceitaService.ativar(receita)
        return receita

    def _estoque(self, produto, quantidade):
        estoque, _ = Estoque.objects.get_or_create(
            produto=produto, filial=self.filial,
        )
        estoque.quantidade_atual = Decimal(quantidade)
        estoque.quantidade_reservada = ZERO
        estoque.atualizar_disponivel()
        estoque.save()
        LoteProduto.objects.create(
            filial=self.filial, produto=produto,
            numero_lote=f'L-{produto.pk}', quantidade_inicial=Decimal(quantidade),
            quantidade_atual=Decimal(quantidade), custo_unitario=produto.preco_custo,
            data_validade=timezone.localdate() + timedelta(days=365),
            status=LoteProduto.Status.ATIVO,
        )

    def _op(self):
        self._estoque(self.manga, '10000')
        self._estoque(self.pote, '100000')
        return OrdemPolpaService.criar(
            self.filial, self.receita,
            {'quantidade_planejada': Decimal('1000')}, self.usuario,
        )

    def _registrar(self, op, **campos):
        dados = {
            'tipo': TP.CASCA, 'quantidade': '500', 'destino': D.DESCARTE,
        }
        dados.update(campos)
        return SubprodutoService.registrar(op, dados, self.usuario)

    def _saldo(self, produto):
        estoque = Estoque.objects.filter(
            produto=produto, filial=self.filial,
        ).first()
        return estoque.quantidade_atual if estoque else ZERO


class RegistroTests(SubprodutoBase):

    def test_registra_os_tipos_da_especificacao(self):
        op = self._op()

        for tipo in (TP.CASCA, TP.SEMENTE, TP.BAGACO, TP.FORA_PADRAO, TP.RESIDUO):
            self._registrar(op, tipo=tipo, quantidade='10')

        self.assertEqual(op.subprodutos.count(), 5)

    def test_registra_os_cinco_destinos(self):
        op = self._op()

        for destino in (D.REAPROVEITAMENTO, D.VENDA, D.USO_INTERNO,
                        D.DOACAO, D.DESCARTE):
            self._registrar(op, destino=destino, quantidade='10')

        self.assertEqual(
            set(op.subprodutos.values_list('destino', flat=True)),
            {D.REAPROVEITAMENTO, D.VENDA, D.USO_INTERNO, D.DOACAO, D.DESCARTE},
        )

    def test_quantidade_zerada_e_recusada(self):
        """
        Não é registro de que não houve casca — é linha que polui a lista e
        distorce a contagem de destinos.
        """
        op = self._op()

        with self.assertRaises(DomainError):
            self._registrar(op, quantidade='0')

    def test_destino_invalido_e_recusado(self):
        op = self._op()

        with self.assertRaises(DomainError):
            self._registrar(op, destino='qualquer_coisa')

    def test_a_virgula_e_aceita(self):
        """Quem pesa na fábrica escreve 12,5."""
        op = self._op()

        subproduto = self._registrar(op, quantidade='12,5')

        self.assertEqual(subproduto.quantidade, Decimal('12.500'))

    def test_o_subproduto_pode_apontar_a_etapa(self):
        op = self._op()
        etapa = op.etapas_processo.first()

        subproduto = self._registrar(op, etapa=etapa.pk)

        self.assertEqual(subproduto.etapa, etapa)


class DinheiroPorDestinoTests(SubprodutoBase):

    def test_venda_guarda_o_valor_recebido(self):
        op = self._op()

        subproduto = self._registrar(
            op, destino=D.VENDA, valor_recebido='300',
            destinatario='Fazenda São João',
        )

        self.assertEqual(subproduto.valor_recebido, Decimal('300.00'))
        self.assertEqual(subproduto.resultado, Decimal('300.00'))

    def test_descarte_guarda_o_custo_de_destinacao(self):
        """
        Destinação de resíduo orgânico se paga — e é o oposto da venda no
        resultado, com o mesmo peso na balança.
        """
        op = self._op()

        subproduto = self._registrar(
            op, destino=D.DESCARTE, custo_destinacao='120',
        )

        self.assertEqual(subproduto.resultado, Decimal('-120.00'))

    def test_valor_em_destino_que_nao_usa_e_apagado(self):
        """
        Alguém preenche "valor recebido", muda o destino para descarte, e o
        relatório somaria uma venda que não houve.
        """
        op = self._op()

        subproduto = self._registrar(
            op, destino=D.DESCARTE, valor_recebido='300', custo_destinacao='50',
        )

        self.assertEqual(subproduto.valor_recebido, ZERO)
        self.assertEqual(subproduto.custo_destinacao, Decimal('50.00'))

    def test_doacao_nao_recebe_valor(self):
        op = self._op()

        subproduto = self._registrar(
            op, destino=D.DOACAO, valor_recebido='999',
            destinatario='Associação de Bairro',
        )

        self.assertEqual(subproduto.valor_recebido, ZERO)
        self.assertEqual(subproduto.destinatario, 'Associação de Bairro')


class EntraNoEstoqueTests(SubprodutoBase):

    def test_reaproveitamento_com_produto_entra_no_estoque(self):
        """
        Sem isto, "aproveitamos a casca" é frase de relatório: o almoxarifado
        não sabe que ela existe, e no mês seguinte se compra ração que já
        estava no pátio.
        """
        op = self._op()

        self._registrar(
            op, destino=D.REAPROVEITAMENTO, quantidade='500',
            produto_estoque=self.casca.pk,
        )

        self.assertEqual(self._saldo(self.casca), Decimal('500.000'))

    def test_uso_interno_tambem_entra(self):
        op = self._op()

        self._registrar(
            op, destino=D.USO_INTERNO, quantidade='200',
            produto_estoque=self.casca.pk,
        )

        self.assertEqual(self._saldo(self.casca), Decimal('200.000'))

    def test_venda_nao_entra_no_estoque(self):
        """
        O material saiu no mesmo ato. Creditar para baixar depois inventaria
        um saldo que nunca existiu.
        """
        op = self._op()

        subproduto = self._registrar(
            op, destino=D.VENDA, quantidade='500',
            produto_estoque=self.casca.pk, valor_recebido='100',
        )

        self.assertEqual(self._saldo(self.casca), ZERO)
        self.assertFalse(subproduto.creditado)

    def test_descarte_nao_entra_no_estoque(self):
        op = self._op()

        self._registrar(
            op, destino=D.DESCARTE, quantidade='500',
            produto_estoque=self.casca.pk,
        )

        self.assertEqual(self._saldo(self.casca), ZERO)

    def test_sem_produto_cadastrado_registra_mas_nao_credita(self):
        """
        O registro vale por si: nem toda casca tem código de produto, e exigir
        cadastro faria o apontamento deixar de ser feito.
        """
        op = self._op()

        subproduto = self._registrar(op, destino=D.REAPROVEITAMENTO)

        self.assertIsNotNone(subproduto.pk)
        self.assertFalse(subproduto.creditado)
        self.assertFalse(subproduto.pendente_de_credito)

    def test_creditar_de_novo_nao_duplica(self):
        op = self._op()
        subproduto = self._registrar(
            op, destino=D.REAPROVEITAMENTO, quantidade='500',
            produto_estoque=self.casca.pk,
        )

        SubprodutoService.creditar_estoque(subproduto, self.usuario)

        self.assertEqual(self._saldo(self.casca), Decimal('500.000'))


class ResumoTests(SubprodutoBase):

    def _pesar(self, op, entrada='1000', saida='400'):
        etapas = list(op.etapas_processo.all())
        ProcessoService.apontar(etapas[0], {
            'quantidade_entrada': entrada, 'quantidade_saida': entrada,
        }, self.usuario)
        ProcessoService.apontar(etapas[-1], {
            'quantidade_entrada': saida, 'quantidade_saida': saida,
        }, self.usuario)

    def test_o_resumo_agrupa_por_destino(self):
        op = self._op()
        self._registrar(op, destino=D.VENDA, quantidade='300', valor_recebido='90')
        self._registrar(op, destino=D.VENDA, quantidade='200', valor_recebido='60')
        self._registrar(op, destino=D.DESCARTE, quantidade='100', custo_destinacao='40')

        resumo = SubprodutoService.resumo(op)

        por_destino = {d['destino']: d for d in resumo['por_destino']}
        self.assertEqual(por_destino[D.VENDA]['quantidade'], Decimal('500.000'))
        self.assertEqual(por_destino[D.VENDA]['resultado'], Decimal('150.00'))
        self.assertEqual(por_destino[D.DESCARTE]['resultado'], Decimal('-40.00'))

    def test_o_resultado_soma_venda_menos_destinacao(self):
        op = self._op()
        self._registrar(op, destino=D.VENDA, quantidade='300', valor_recebido='90')
        self._registrar(op, destino=D.DESCARTE, quantidade='100', custo_destinacao='40')

        self.assertEqual(SubprodutoService.resumo(op)['resultado'], Decimal('50.00'))

    def test_a_ordem_dos_destinos_vai_do_melhor_ao_pior(self):
        """Ver a lista nessa ordem já sugere subir uma linha."""
        op = self._op()
        self._registrar(op, destino=D.DESCARTE, quantidade='10')
        self._registrar(op, destino=D.REAPROVEITAMENTO, quantidade='10')

        resumo = SubprodutoService.resumo(op)

        self.assertEqual(
            [d['destino'] for d in resumo['por_destino']],
            [D.REAPROVEITAMENTO, D.DESCARTE],
        )

    def test_o_subproduto_nao_soma_a_perda_da_etapa(self):
        """
        O peso já está dentro da perda medida. Somá-los contaria a mesma casca
        duas vezes, e o rendimento despencaria no papel sem nada ter mudado no
        chão.
        """
        op = self._op()
        self._pesar(op, entrada='1000', saida='400')  # 600 de perda
        self._registrar(op, destino=D.VENDA, quantidade='500')

        resumo = SubprodutoService.resumo(op)

        self.assertEqual(resumo['perda_medida'], Decimal('600.000'))
        self.assertEqual(resumo['total'], Decimal('500.000'))

    def test_a_perda_sem_nome_e_o_que_sobra(self):
        """
        A pergunta que fica: sobrou peso que ninguém sabe para onde foi?
        """
        op = self._op()
        self._pesar(op, entrada='1000', saida='400')
        self._registrar(op, destino=D.VENDA, quantidade='500')

        self.assertEqual(
            SubprodutoService.resumo(op)['perda_sem_nome'], Decimal('100.000'),
        )

    def test_subproduto_maior_que_a_perda_nao_vira_negativo(self):
        """
        Acontece: a balança da linha e a do resíduo não são a mesma. Um
        negativo ali seria lido como "sobrou perda", que é o oposto.
        """
        op = self._op()
        self._pesar(op, entrada='1000', saida='400')
        self._registrar(op, destino=D.VENDA, quantidade='700')

        self.assertEqual(SubprodutoService.resumo(op)['perda_sem_nome'], ZERO)

    def test_sem_pesagem_a_perda_sem_nome_e_nula(self):
        """
        Nula e não zero: "ninguém pesou" é diferente de "está tudo explicado".
        """
        op = self._op()
        self._registrar(op, destino=D.VENDA, quantidade='500')

        self.assertIsNone(SubprodutoService.resumo(op)['perda_sem_nome'])

    def test_o_resumo_aponta_o_que_falta_creditar(self):
        op = self._op()
        subproduto = self._registrar(
            op, destino=D.REAPROVEITAMENTO, quantidade='100',
            produto_estoque=self.casca.pk,
        )
        subproduto.estoque_creditado_em = None
        subproduto.save(update_fields=['estoque_creditado_em'])

        pendentes = SubprodutoService.resumo(op)['pendentes_de_credito']

        self.assertEqual([p.pk for p in pendentes], [subproduto.pk])


class TelaTests(SubprodutoBase):
    """O registro precisa de onde ser feito — e fica na tela da ordem."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.usuario)

    def _url(self, nome, *args):
        from django.urls import reverse
        return reverse(f'polpa:{nome}', args=args)

    def _abrir(self, op):
        return self.client.get(self._url('ordem-detail', op.pk))

    def test_a_tela_da_ordem_tem_o_bloco(self):
        op = self._op()

        resposta = self._abrir(op)

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Subprodutos e resíduos')
        self.assertContains(resposta, 'REGISTRAR SUBPRODUTO')

    def test_a_tela_oferece_os_cinco_destinos(self):
        op = self._op()

        resposta = self._abrir(op)

        for rotulo in ('Reaproveitamento', 'Venda', 'Uso interno',
                       'Doação', 'Descarte'):
            self.assertContains(resposta, rotulo)

    def test_registrar_pela_tela_grava(self):
        op = self._op()

        self.client.post(self._url('subproduto-registrar', op.pk), {
            'tipo': TP.BAGACO, 'quantidade': '400', 'destino': D.VENDA,
            'valor_recebido': '120', 'destinatario': 'Fazenda São João',
        })

        subproduto = op.subprodutos.get()
        self.assertEqual(subproduto.quantidade, Decimal('400.000'))
        self.assertEqual(subproduto.valor_recebido, Decimal('120.00'))

    def test_o_credito_no_estoque_e_noticia_separada(self):
        """
        Ele muda saldo de um produto que não é o da ordem — na mesma frase,
        faria a pessoa achar que a batida rendeu mais.
        """
        op = self._op()

        resposta = self.client.post(self._url('subproduto-registrar', op.pk), {
            'tipo': TP.CASCA, 'quantidade': '500',
            'destino': D.REAPROVEITAMENTO, 'produto_estoque': self.casca.pk,
        }, follow=True)

        mensagens = [str(m) for m in resposta.context['messages']]
        self.assertTrue(any('entraram no estoque' in m for m in mensagens), mensagens)

    def test_quantidade_invalida_avisa_em_vez_de_estourar(self):
        op = self._op()

        resposta = self.client.post(self._url('subproduto-registrar', op.pk), {
            'tipo': TP.CASCA, 'quantidade': '0', 'destino': D.DESCARTE,
        }, follow=True)

        mensagens = [str(m) for m in resposta.context['messages']]
        self.assertTrue(any('quanto de subproduto' in m for m in mensagens), mensagens)
        self.assertEqual(op.subprodutos.count(), 0)

    def test_nao_apaga_o_que_ja_entrou_no_estoque(self):
        """
        Apagar deixaria o saldo com material sem origem — o caminho certo é
        um ajuste de estoque, que tem rastro próprio.
        """
        op = self._op()
        subproduto = self._registrar(
            op, destino=D.REAPROVEITAMENTO, quantidade='500',
            produto_estoque=self.casca.pk,
        )

        resposta = self.client.post(
            self._url('subproduto-excluir', op.pk, subproduto.pk), follow=True,
        )

        self.assertEqual(op.subprodutos.count(), 1)
        mensagens = [str(m) for m in resposta.context['messages']]
        self.assertTrue(any('ajuste de estoque' in m for m in mensagens), mensagens)

    def test_apaga_o_que_nao_creditou(self):
        op = self._op()
        subproduto = self._registrar(op, destino=D.DESCARTE)

        self.client.post(self._url('subproduto-excluir', op.pk, subproduto.pk))

        self.assertEqual(op.subprodutos.count(), 0)

    def test_a_tela_mostra_a_perda_sem_destino(self):
        op = self._op()
        etapas = list(op.etapas_processo.all())
        ProcessoService.apontar(etapas[0], {
            'quantidade_entrada': '1000', 'quantidade_saida': '1000',
        }, self.usuario)
        ProcessoService.apontar(etapas[-1], {
            'quantidade_entrada': '400', 'quantidade_saida': '400',
        }, self.usuario)
        self._registrar(op, destino=D.VENDA, quantidade='500')

        self.assertContains(self._abrir(op), 'sem destino conhecido')

    def test_nada_de_tag_vaza_para_a_tela(self):
        op = self._op()
        self._registrar(op, destino=D.VENDA, quantidade='100', valor_recebido='30')

        corpo = self._abrir(op).content.decode()

        for marca in ('{%', '%}', '{#', '#}', 'endcomment'):
            self.assertNotIn(marca, corpo, f'tag {marca} vazou para a tela')
