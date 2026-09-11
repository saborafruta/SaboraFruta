"""
Fase 1 do estoque por depósito.

Cobre o model `Deposito`, a resolução do depósito padrão da filial e o
fato de que toda movimentação de estoque passa a carimbar o depósito —
sem que nada mude no comportamento, já que cada filial tem um único
depósito nesta fase.
"""
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import Deposito, Estoque, MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.produtos.models import (
    Produto, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial,
)


class DepositoBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Estoque Depósito LTDA', nome_fantasia='Depósito',
            cnpj='19345678000101',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Filial Depósito',
            nome_fantasia='Matriz', cnpj='19345678000102', uf='RN',
        )
        cls.perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='deposito@teste.local', nome='Dep', password='x' * 12,
            empresa=cls.empresa, filial=cls.filial, perfil=cls.perfil,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='UN', descricao='Unidade',
            tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)

    def _produto(self, descricao='Produto Dep'):
        produto = Produto.objects.create(
            filial=self.filial, unidade_medida=self.unidade, descricao=descricao,
            ncm='20089900', permite_venda_sem_estoque=False,
        )
        ProdutoFilial.objects.create(produto=produto, filial=self.filial)
        return produto


class DepositoModelTests(DepositoBase):

    def test_padrao_id_cria_sob_demanda_e_reusa(self):
        pk = Deposito.padrao_id(self.filial.pk)
        deposito = Deposito.objects.get(pk=pk)
        self.assertTrue(deposito.is_padrao)
        self.assertEqual(deposito.filial_id, self.filial.pk)
        # segunda chamada devolve o mesmo, não cria outro
        self.assertEqual(Deposito.padrao_id(self.filial.pk), pk)
        self.assertEqual(
            Deposito.objects.filter(filial=self.filial).count(), 1,
        )

    def test_so_um_padrao_por_filial(self):
        Deposito.padrao_id(self.filial.pk)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Deposito.objects.create(
                    filial=self.filial, nome='Fábrica', is_padrao=True,
                )

    def test_nome_unico_por_filial(self):
        Deposito.objects.create(filial=self.filial, nome='Fábrica')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Deposito.objects.create(filial=self.filial, nome='Fábrica')

    def test_mesmo_nome_em_filiais_diferentes_e_permitido(self):
        outra = Filial.objects.create(
            empresa=self.empresa, razao_social='Outra', nome_fantasia='Outra',
            cnpj='19345678000188', uf='RN',
        )
        Deposito.objects.create(filial=self.filial, nome='Fábrica')
        Deposito.objects.create(filial=outra, nome='Fábrica')  # não levanta


class DepositoNaMovimentacaoTests(DepositoBase):

    def test_entrada_carimba_deposito_padrao_no_saldo_e_na_movimentacao(self):
        produto = self._produto()
        MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk,
            filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal('10'),
            usuario_id=self.usuario.pk,
            valor_unitario=Decimal('3'),
        )
        padrao_id = Deposito.padrao_id(self.filial.pk)

        estoque = Estoque.objects.get(produto=produto, filial=self.filial)
        self.assertEqual(estoque.deposito_id, padrao_id)

        mov = MovimentacaoEstoque.objects.get(produto=produto)
        self.assertEqual(mov.deposito_id, padrao_id)

    def test_deposito_explicito_e_respeitado(self):
        produto = self._produto()
        fabrica = Deposito.objects.create(
            filial=self.filial, nome='Fábrica', tipo=Deposito.Tipo.PRODUCAO,
        )
        MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk,
            filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal('4'),
            usuario_id=self.usuario.pk,
            valor_unitario=Decimal('2'),
            deposito_id=fabrica.pk,
        )
        # saldo foi para o depósito da fábrica, não para o padrão
        self.assertTrue(
            Estoque.objects.filter(
                produto=produto, filial=self.filial, deposito=fabrica,
                quantidade_atual=Decimal('4'),
            ).exists()
        )
        self.assertFalse(
            Estoque.objects.filter(
                produto=produto, deposito_id=Deposito.padrao_id(self.filial.pk),
            ).exists()
        )

    def test_ajuste_manual_sem_lote_cria_saldo_com_deposito(self):
        produto = self._produto()
        MovimentacaoService.ajustar_manual(
            produto_id=produto.pk,
            filial_id=self.filial.pk,
            quantidade_nova=Decimal('7'),
            usuario_id=self.usuario.pk,
            justificativa='Contagem inicial.',
        )
        estoque = Estoque.objects.get(produto=produto, filial=self.filial)
        self.assertEqual(estoque.quantidade_atual, Decimal('7'))
        self.assertEqual(estoque.deposito_id, Deposito.padrao_id(self.filial.pk))


class TransferenciaInternaTests(DepositoBase):

    def _saldo(self, produto, deposito):
        return (
            Estoque.objects.filter(
                produto=produto, filial=self.filial, deposito=deposito,
            ).values_list('quantidade_atual', flat=True).first()
            or Decimal('0')
        )

    def setUp(self):
        self.produto = self._produto('Camiseta')
        self.loja_id = Deposito.padrao_id(self.filial.pk)
        self.loja = Deposito.objects.get(pk=self.loja_id)
        self.fabrica = Deposito.objects.create(
            filial=self.filial, nome='Fábrica', tipo=Deposito.Tipo.PRODUCAO,
            permite_venda=False,
        )
        MovimentacaoService.registrar_movimentacao(
            produto_id=self.produto.pk, filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal('20'), usuario_id=self.usuario.pk,
            valor_unitario=Decimal('5'),
        )  # entra no depósito padrão (loja)

    def test_move_saldo_entre_depositos_sem_mexer_no_total_da_filial(self):
        MovimentacaoService.transferir_entre_depositos(
            produto_id=self.produto.pk, filial_id=self.filial.pk,
            deposito_origem_id=self.loja_id, deposito_destino_id=self.fabrica.pk,
            quantidade=Decimal('8'), usuario_id=self.usuario.pk,
        )
        self.assertEqual(self._saldo(self.produto, self.loja), Decimal('12'))
        self.assertEqual(self._saldo(self.produto, self.fabrica), Decimal('8'))

        movs = MovimentacaoEstoque.objects.filter(
            produto=self.produto,
            tipo_operacao__in=[
                MovimentacaoEstoque.TipoOperacao.TRANSFERENCIA_INTERNA_SAIDA,
                MovimentacaoEstoque.TipoOperacao.TRANSFERENCIA_INTERNA_ENTRADA,
            ],
        )
        self.assertEqual(movs.count(), 2)
        saida = movs.get(tipo_operacao=MovimentacaoEstoque.TipoOperacao.TRANSFERENCIA_INTERNA_SAIDA)
        self.assertEqual(saida.deposito_id, self.loja_id)
        self.assertEqual(saida.deposito_destino_id, self.fabrica.pk)

    def test_recusa_mesmo_deposito(self):
        from apps.core.services.exceptions import DadosInvalidosError
        with self.assertRaises(DadosInvalidosError):
            MovimentacaoService.transferir_entre_depositos(
                produto_id=self.produto.pk, filial_id=self.filial.pk,
                deposito_origem_id=self.loja_id, deposito_destino_id=self.loja_id,
                quantidade=Decimal('1'), usuario_id=self.usuario.pk,
            )

    def test_recusa_saldo_insuficiente(self):
        from apps.core.services.exceptions import EstoqueInsuficienteError
        with self.assertRaises(EstoqueInsuficienteError):
            MovimentacaoService.transferir_entre_depositos(
                produto_id=self.produto.pk, filial_id=self.filial.pk,
                deposito_origem_id=self.loja_id, deposito_destino_id=self.fabrica.pk,
                quantidade=Decimal('999'), usuario_id=self.usuario.pk,
            )

    def test_recusa_deposito_de_outra_filial(self):
        from apps.core.services.exceptions import DadosInvalidosError
        outra = Filial.objects.create(
            empresa=self.empresa, razao_social='Outra F', nome_fantasia='Outra',
            cnpj='19345678000199', uf='RN',
        )
        dep_outra = Deposito.objects.create(filial=outra, nome='X')
        with self.assertRaises(DadosInvalidosError):
            MovimentacaoService.transferir_entre_depositos(
                produto_id=self.produto.pk, filial_id=self.filial.pk,
                deposito_origem_id=self.loja_id, deposito_destino_id=dep_outra.pk,
                quantidade=Decimal('1'), usuario_id=self.usuario.pk,
            )


class ProducaoNoDepositoTests(DepositoBase):
    """Fase 3: a produção consome/reserva no depósito de fábrica."""

    def setUp(self):
        self.produto = self._produto('Tecido')
        self.padrao_id = Deposito.padrao_id(self.filial.pk)

    def test_producao_id_cai_no_padrao_sem_deposito_de_fabrica(self):
        self.assertEqual(
            Deposito.producao_id(self.filial.pk), self.padrao_id,
        )

    def test_producao_id_usa_o_deposito_tipo_producao(self):
        fabrica = Deposito.objects.create(
            filial=self.filial, nome='Fábrica', tipo=Deposito.Tipo.PRODUCAO,
        )
        self.assertEqual(Deposito.producao_id(self.filial.pk), fabrica.pk)

    def test_producao_id_ignora_fabrica_inativa_ou_sem_producao(self):
        Deposito.objects.create(
            filial=self.filial, nome='Fábrica velha', tipo=Deposito.Tipo.PRODUCAO,
            ativo=False,
        )
        Deposito.objects.create(
            filial=self.filial, nome='Showroom', tipo=Deposito.Tipo.PRODUCAO,
            permite_producao=False,
        )
        self.assertEqual(Deposito.producao_id(self.filial.pk), self.padrao_id)

    def test_reserva_e_liberacao_no_deposito_de_fabrica(self):
        fabrica = Deposito.objects.create(
            filial=self.filial, nome='Fábrica', tipo=Deposito.Tipo.PRODUCAO,
        )
        # tecido entra na fábrica
        MovimentacaoService.registrar_movimentacao(
            produto_id=self.produto.pk, filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal('30'), usuario_id=self.usuario.pk,
            valor_unitario=Decimal('4'), deposito_id=fabrica.pk,
        )
        MovimentacaoService.reservar_estoque(
            produto_id=self.produto.pk, filial_id=self.filial.pk,
            quantidade=Decimal('12'), deposito_id=fabrica.pk,
        )
        est = Estoque.objects.get(
            produto=self.produto, filial=self.filial, deposito=fabrica,
        )
        self.assertEqual(est.quantidade_reservada, Decimal('12'))

        MovimentacaoService.liberar_reserva(
            produto_id=self.produto.pk, filial_id=self.filial.pk,
            quantidade=Decimal('5'), deposito_id=fabrica.pk,
        )
        est.refresh_from_db()
        self.assertEqual(est.quantidade_reservada, Decimal('7'))

    def test_liberar_reserva_nao_quebra_com_dois_depositos(self):
        """Regressão: antes da Fase 3, .get(produto, filial) estourava
        MultipleObjectsReturned quando o produto tinha saldo em 2 depósitos."""
        fabrica = Deposito.objects.create(filial=self.filial, nome='Fábrica')
        for dep in (self.padrao_id, fabrica.pk):
            MovimentacaoService.registrar_movimentacao(
                produto_id=self.produto.pk, filial_id=self.filial.pk,
                tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
                quantidade=Decimal('10'), usuario_id=self.usuario.pk,
                valor_unitario=Decimal('4'), deposito_id=dep,
            )
        MovimentacaoService.reservar_estoque(
            produto_id=self.produto.pk, filial_id=self.filial.pk,
            quantidade=Decimal('3'), deposito_id=fabrica.pk,
        )
        # sem deposito_id explícito cai no padrão — não pode estourar
        MovimentacaoService.liberar_reserva(
            produto_id=self.produto.pk, filial_id=self.filial.pk,
            quantidade=Decimal('1'), tolerar_ausente=True,
        )
