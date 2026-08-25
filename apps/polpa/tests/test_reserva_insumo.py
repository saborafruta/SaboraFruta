"""
A matéria-prima separada para a batida.

Nada segurava insumo para uma OP em andamento. Numa fábrica de polpa isso
dói mais que em outras: a mesma câmara atende a batida de polpa, a de açaí e
a de sorvete no mesmo dia, e as três "tinham" a mesma manga — a última a
chegar na balança é que descobria que não tinha. E como a baixa só acontece
no ENCERRAMENTO da ordem, a janela em que o saldo mente é a batida inteira,
não um instante.

O que os testes cercam:

  · SEPARA AO COMEÇAR A BATIDA, lendo a mesma `necessidade()` que a tela
    mostra — recalcular aqui daria uma segunda definição da conta, e no dia
    em que divergissem a reserva separaria uma quantidade e a produção
    cobraria outra;
  · A RESERVA MORRE QUANDO O CONSUMO NASCE. É a metade que falta da conta: o
    encerramento baixa o insumo de verdade, e uma reserva sobrevivente
    deixaria o mesmo material reservado E consumido, com o disponível
    negativo sem nada errado ter acontecido;
  · CANCELOU, A FRUTA VOLTA. Reserva de batida que não vai acontecer some do
    disponível para sempre, e ninguém desconfia de um número que só encolhe;
  · NÃO INVENTA MATERIAL. Reserva maior que o saldo é promessa que a câmara
    não cumpre;
  · NÃO TRAVA A LINHA. Problema de estoque não impede registrar que a batida
    começou — ela vai acontecer de qualquer jeito, e o que se perde ao travar
    é o registro.
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import Estoque, LoteProduto
from apps.polpa.models import EtapaReceita, FichaProduto, OrdemPolpa, ReservaInsumo
from apps.polpa.services import CatalogoService, OrdemPolpaService, ReceitaService
from apps.producao.models import ItemFichaTecnica
from apps.produtos.models import UnidadeMedida, UnidadeMedidaFilial

T = FichaProduto.Tipo
S = OrdemPolpa.Situacao
ZERO = Decimal('0')


class ReservaBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Polpas Reserva LTDA', nome_fantasia='Reserva',
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
            email='chefe@reserva.local', nome='Chefe', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )

    def setUp(self):
        self.acabado = self._item(
            T.POLPA, 'Polpa de manga 100 g', validade_dias=180,
            peso_liquido=Decimal('0.100'), quantidade_por_embalagem=Decimal('50'),
        )
        self.manga = self._item(T.FRUTA, 'Manga in natura', custo=Decimal('1.50'))
        self.pote = self._item(T.POTE, 'Pote 100 g', custo=Decimal('0.20'))
        self.receita = self._receita()

    # ── Montagem, na forma que o vertical já usa ─────────────────────────

    def _item(self, tipo, descricao, custo=Decimal('0'), **extras):
        dados = {
            'tipo': tipo, 'descricao': descricao, 'codigo': descricao[:10],
            'unidade_medida': self.unidade, 'preco_custo': custo,
        }
        dados.update(extras)
        return CatalogoService.salvar(self.filial, dados).produto

    def _receita(self):
        """1000 kg de produto consomem 1200 kg de manga e 10000 potes."""
        receita = ReceitaService.criar(self.filial, self.acabado, {
            'descricao': 'Polpa de manga 100 g', 'versao': '1.0',
            'quantidade_produzida': Decimal('1000'),
            'rendimento_esperado': Decimal('60'),
        })
        ItemFichaTecnica.objects.create(
            ficha=receita.ficha, materia_prima=self.manga,
            quantidade=Decimal('1200'), perda_prevista=Decimal('0'),
        )
        ItemFichaTecnica.objects.create(
            ficha=receita.ficha, materia_prima=self.pote,
            quantidade=Decimal('10000'), perda_prevista=Decimal('0'),
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
        return estoque

    def _camara_cheia(self, manga='5000', pote='50000'):
        self._estoque(self.manga, manga)
        self._estoque(self.pote, pote)

    def _op(self, quantidade=Decimal('1000'), **campos):
        dados = {'quantidade_planejada': quantidade}
        dados.update(campos)
        return OrdemPolpaService.criar(self.filial, self.receita, dados, self.usuario)

    def _iniciar(self, op):
        OrdemPolpaService.mover(op, S.LIBERADA, self.usuario)
        return OrdemPolpaService.mover(op, S.EM_PRODUCAO, self.usuario)

    def _reservas(self, op):
        return ReservaInsumo.all_objects.filter(
            ordem=op, status=ReservaInsumo.Status.ATIVA,
        )

    def _disponivel(self, produto):
        return Estoque.objects.get(
            produto=produto, filial=self.filial,
        ).quantidade_disponivel


class SeparaAoComecarTests(ReservaBase):

    def test_iniciar_a_batida_separa_o_insumo(self):
        self._camara_cheia()
        op = self._op()

        self._iniciar(op)

        produtos = {r.produto_id for r in self._reservas(op)}
        self.assertEqual(produtos, {self.manga.pk, self.pote.pk})

    def test_separa_o_que_a_necessidade_manda(self):
        """A mesma conta da tela: 1200 kg de manga para 1000 kg de produto."""
        self._camara_cheia()
        op = self._op(quantidade=Decimal('1000'))

        self._iniciar(op)

        self.assertEqual(
            self._reservas(op).get(produto=self.manga).quantidade,
            Decimal('1200.0000'),
        )

    def test_o_fator_da_quantidade_planejada_vale(self):
        """Meia batida separa metade — a conta não é a da ficha crua."""
        self._camara_cheia()
        op = self._op(quantidade=Decimal('500'))

        self._iniciar(op)

        self.assertEqual(
            self._reservas(op).get(produto=self.manga).quantidade,
            Decimal('600.0000'),
        )

    def test_o_disponivel_do_estoque_cai(self):
        """
        É o efeito que importa: sem isto, a batida de açaí ainda "tem" a
        manga que esta já está usando.
        """
        self._camara_cheia()
        op = self._op()

        self._iniciar(op)

        self.assertEqual(self._disponivel(self.manga), Decimal('3800.000'))

    def test_a_ordem_fica_carimbada(self):
        self._camara_cheia()
        op = self._op()

        self._iniciar(op)

        op.refresh_from_db()
        self.assertIsNotNone(op.insumos_reservados_em)
        self.assertTrue(op.insumo_reservado)

    def test_despausar_nao_separa_tudo_de_novo(self):
        self._camara_cheia()
        op = self._op()
        self._iniciar(op)
        OrdemPolpaService.mover(op, S.PAUSADA, self.usuario, {'motivo': 'Quebrou'})

        OrdemPolpaService.mover(op, S.EM_PRODUCAO, self.usuario)

        self.assertEqual(self._reservas(op).count(), 2)
        self.assertEqual(self._disponivel(self.manga), Decimal('3800.000'))

    def test_liberar_sozinho_nao_separa(self):
        """
        Liberar é autorizar a batida, não começá-la. Separar aqui seguraria
        fruta de uma OP que pode ficar o dia inteiro na fila.
        """
        self._camara_cheia()
        op = self._op()

        OrdemPolpaService.mover(op, S.LIBERADA, self.usuario)

        self.assertEqual(self._reservas(op).count(), 0)
        self.assertEqual(self._disponivel(self.manga), Decimal('5000.000'))


class NaoInventaMaterialTests(ReservaBase):

    def test_sem_saldo_nao_reserva(self):
        """
        Reserva maior que o saldo é promessa que a câmara não cumpre — e
        sumiria do disponível de todas as outras batidas do dia.
        """
        self._estoque(self.pote, '50000')  # manga sem saldo nenhum
        op = self._op()

        self._iniciar(op)

        self.assertEqual(self._reservas(op).filter(produto=self.manga).count(), 0)
        self.assertEqual(self._reservas(op).filter(produto=self.pote).count(), 1)

    def test_com_saldo_parcial_separa_o_que_da(self):
        """
        Faltar fruta não é motivo para deixar o pote solto no almoxarifado —
        e o que falta continua aparecendo na necessidade.
        """
        self._estoque(self.manga, '500')
        self._estoque(self.pote, '50000')
        op = self._op()

        self._iniciar(op)

        self.assertEqual(
            self._reservas(op).get(produto=self.manga).quantidade,
            Decimal('500.0000'),
        )
        self.assertEqual(self._disponivel(self.manga), ZERO)

    def test_a_tela_mostra_o_que_foi_separado(self):
        """
        Sem esta coluna, "em estoque" aparece já descontado da própria reserva
        desta ordem — e quem olha conclui que o material sumiu, quando ele
        está justamente guardado para esta batida.
        """
        self._camara_cheia()
        op = self._op()
        self._iniciar(op)

        linhas = OrdemPolpaService.necessidade(op)['ingredientes']
        manga = next(l for l in linhas if l['produto'].pk == self.manga.pk)

        self.assertEqual(manga['reservado'], Decimal('1200.0000'))

    def test_sem_reserva_a_coluna_vem_zerada(self):
        self._camara_cheia()
        op = self._op()

        linhas = OrdemPolpaService.necessidade(op)['ingredientes']

        self.assertTrue(all(l['reservado'] == ZERO for l in linhas))

    def test_o_que_falta_continua_aparecendo_na_necessidade(self):
        self._estoque(self.manga, '500')
        self._estoque(self.pote, '50000')
        op = self._op()
        self._iniciar(op)

        faltas = OrdemPolpaService.necessidade(op)['faltas']

        self.assertTrue(any(l['produto'].pk == self.manga.pk for l in faltas))


class ReservaMorreComOConsumoTests(ReservaBase):

    def test_concluir_libera_a_reserva(self):
        """
        A metade que falta da conta: o encerramento baixa o insumo de
        verdade, e a reserva sobrevivente deixaria o mesmo material reservado
        E consumido.
        """
        self._camara_cheia()
        op = self._op()
        self._iniciar(op)

        OrdemPolpaService.concluir(op, self.usuario, quantidade=Decimal('1000'))

        self.assertEqual(self._reservas(op).count(), 0)

    def test_a_reserva_fica_marcada_como_consumida(self):
        """Consumida virou produto; cancelada voltou para a câmara."""
        self._camara_cheia()
        op = self._op()
        self._iniciar(op)

        OrdemPolpaService.concluir(op, self.usuario, quantidade=Decimal('1000'))

        marcas = set(
            ReservaInsumo.all_objects.filter(ordem=op)
            .values_list('status', flat=True)
        )
        self.assertEqual(marcas, {ReservaInsumo.Status.CONSUMIDA})

    def test_o_reservado_do_estoque_zera(self):
        """
        O defeito que a liberação evita: reservado E consumido ao mesmo tempo
        derrubaria o disponível sem nada errado ter acontecido.
        """
        self._camara_cheia()
        op = self._op()
        self._iniciar(op)

        OrdemPolpaService.concluir(op, self.usuario, quantidade=Decimal('1000'))

        estoque = Estoque.objects.get(produto=self.manga, filial=self.filial)
        self.assertEqual(estoque.quantidade_reservada, ZERO)
        self.assertEqual(estoque.quantidade_disponivel, estoque.quantidade_atual)

    def test_cancelar_devolve_a_fruta(self):
        self._camara_cheia()
        op = self._op()
        self._iniciar(op)
        self.assertEqual(self._disponivel(self.manga), Decimal('3800.000'))

        OrdemPolpaService.mover(
            op, S.CANCELADA, self.usuario, {'motivo': 'Fruta chegou estragada'},
        )

        self.assertEqual(self._reservas(op).count(), 0)
        self.assertEqual(self._disponivel(self.manga), Decimal('5000.000'))

    def test_cancelada_e_consumida_nao_se_confundem(self):
        self._camara_cheia()
        op = self._op()
        self._iniciar(op)

        OrdemPolpaService.mover(op, S.CANCELADA, self.usuario, {'motivo': 'X'})

        marcas = set(
            ReservaInsumo.all_objects.filter(ordem=op)
            .values_list('status', flat=True)
        )
        self.assertEqual(marcas, {ReservaInsumo.Status.CANCELADA})


class NaoTravaALinhaTests(ReservaBase):

    def test_falha_na_reserva_nao_impede_a_batida_de_comecar(self):
        self._camara_cheia()
        op = self._op()

        with patch.object(
            OrdemPolpaService, 'reservar_insumos',
            side_effect=RuntimeError('banco fora'),
        ):
            self._iniciar(op)

        op.refresh_from_db()
        self.assertEqual(op.situacao, S.EM_PRODUCAO)

    def test_falha_deixa_sem_carimbo_para_tentar_de_novo(self):
        self._camara_cheia()
        op = self._op()

        with patch.object(
            OrdemPolpaService, 'reservar_insumos',
            side_effect=RuntimeError('banco fora'),
        ):
            self._iniciar(op)

        op.refresh_from_db()
        self.assertIsNone(op.insumos_reservados_em)

        OrdemPolpaService.mover(op, S.PAUSADA, self.usuario, {'motivo': 'x'})
        OrdemPolpaService.mover(op, S.EM_PRODUCAO, self.usuario)

        self.assertEqual(self._reservas(op).count(), 2)
