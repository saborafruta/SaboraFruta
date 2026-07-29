from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import (
    DadosInvalidosError,
    EstoqueInsuficienteError,
)
from apps.estoque.models import (
    AlertaVencimento, Estoque, Inventario, LoteProduto, MovimentacaoEstoque,
)
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.estoque.services.transferencia_cancelamento import (
    cancelar_transferencia,
    reativar_transferencia,
)
from apps.financeiro.constants.enums import (
    StatusDocumentoFiscal,
    TipoDocumentoFiscal,
)
from apps.financeiro.models import DocumentoFiscal
from apps.produtos.models import Produto, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial


class MovimentacaoServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Empresa Teste LTDA',
            nome_fantasia='Empresa Teste',
            cnpj='12345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa,
            razao_social='Filial Teste',
            nome_fantasia='Matriz',
            cnpj='12345678000192',
            uf='RN',
        )
        cls.filial_destino = Filial.objects.create(
            empresa=cls.empresa,
            razao_social='Filial Destino',
            nome_fantasia='Destino',
            cnpj='12345678000193',
            uf='RN',
        )
        cls.perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa,
            nome='Administrador',
            is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='estoque-test@inoovated.com',
            nome='Usuario Estoque',
            password='teste1234',
            empresa=cls.empresa,
            filial=cls.filial,
            perfil=cls.perfil,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa,
            sigla='UN',
            descricao='Unidade',
            tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial_destino)

    def criar_produto(self, descricao='Produto Teste', controla_lote=False, filial=None):
        filial = filial or self.filial
        produto = Produto.objects.create(
            filial=filial,
            unidade_medida=self.unidade,
            descricao=descricao,
            ncm='20089900',
            controla_lote=controla_lote,
            controla_validade=controla_lote,
            permite_venda_sem_estoque=False,
        )
        ProdutoFilial.objects.create(produto=produto, filial=filial)
        return produto

    def criar_lote(self, produto, numero='LT-001', quantidade='0', validade=None, filial=None):
        return LoteProduto.objects.create(
            produto=produto,
            filial=filial or self.filial,
            numero_lote=numero,
            data_validade=validade,
            quantidade_inicial=Decimal(quantidade),
            quantidade_atual=Decimal(quantidade),
            custo_unitario=Decimal('2.5000'),
        )

    def test_produto_com_controle_de_lote_exige_lote_na_movimentacao(self):
        produto = self.criar_produto(controla_lote=True)

        with self.assertRaisesMessage(DadosInvalidosError, 'Produto controla lote'):
            MovimentacaoService.registrar_movimentacao(
                produto_id=produto.pk,
                filial_id=self.filial.pk,
                tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
                quantidade=Decimal('1'),
                usuario_id=self.usuario.pk,
            )

    def test_produto_sem_lote_pode_sair_sem_lote_pelo_fefo(self):
        produto = self.criar_produto(controla_lote=False)
        MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk,
            filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal('10'),
            usuario_id=self.usuario.pk,
            valor_unitario=Decimal('3'),
        )

        movimentacoes = MovimentacaoService.registrar_saida_fefo(
            produto_id=produto.pk,
            filial_id=self.filial.pk,
            quantidade=Decimal('4'),
            usuario_id=self.usuario.pk,
        )

        estoque = Estoque.objects.get(produto=produto, filial=self.filial)
        self.assertEqual(len(movimentacoes), 1)
        self.assertIsNone(movimentacoes[0].lote_id)
        self.assertEqual(estoque.quantidade_atual, Decimal('6.000'))
        self.assertEqual(estoque.quantidade_disponivel, Decimal('6.000'))

    def test_lote_de_outra_filial_nao_movimenta_produto(self):
        produto = self.criar_produto(controla_lote=True)
        lote_local = self.criar_lote(produto, numero='LT-LOCAL')
        MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk,
            filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal('5'),
            usuario_id=self.usuario.pk,
            lote_id=lote_local.pk,
        )
        lote = self.criar_lote(
            produto,
            numero='LT-DEST',
            quantidade='5',
            filial=self.filial_destino,
        )

        with self.assertRaisesMessage(DadosInvalidosError, 'nao pertence ao produto/filial'):
            MovimentacaoService.registrar_movimentacao(
                produto_id=produto.pk,
                filial_id=self.filial.pk,
                tipo_operacao=MovimentacaoEstoque.TipoOperacao.SAIDA,
                quantidade=Decimal('1'),
                usuario_id=self.usuario.pk,
                lote_id=lote.pk,
            )

    def test_saida_fefo_ignora_lote_vencido_e_consume_lote_vigente(self):
        produto = self.criar_produto(controla_lote=True)
        vencido = self.criar_lote(
            produto,
            numero='VENCIDO',
            validade=timezone.now().date() - timedelta(days=1),
        )
        vigente = self.criar_lote(
            produto,
            numero='VIGENTE',
            validade=timezone.now().date() + timedelta(days=10),
        )
        MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk,
            filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal('8'),
            usuario_id=self.usuario.pk,
            lote_id=vencido.pk,
        )
        MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk,
            filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal('5'),
            usuario_id=self.usuario.pk,
            lote_id=vigente.pk,
        )

        movimentacoes = MovimentacaoService.registrar_saida_fefo(
            produto_id=produto.pk,
            filial_id=self.filial.pk,
            quantidade=Decimal('3'),
            usuario_id=self.usuario.pk,
        )

        vencido.refresh_from_db()
        vigente.refresh_from_db()
        self.assertEqual(len(movimentacoes), 1)
        self.assertEqual(movimentacoes[0].lote_id, vigente.pk)
        self.assertEqual(vencido.quantidade_atual, Decimal('8.000'))
        self.assertEqual(vigente.quantidade_atual, Decimal('2.000'))

    def test_movimentacao_atualiza_alerta_de_vencimento_do_lote(self):
        produto = self.criar_produto(controla_lote=True)
        lote = self.criar_lote(
            produto,
            numero='ALERTA-LOTE',
            validade=timezone.now().date() + timedelta(days=5),
        )

        MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk,
            filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal('5'),
            usuario_id=self.usuario.pk,
            lote_id=lote.pk,
        )
        alerta = AlertaVencimento.objects.get(lote=lote, resolvido=False)
        self.assertEqual(alerta.quantidade_em_risco, Decimal('5.000'))
        self.assertEqual(alerta.nivel_risco, AlertaVencimento.NivelRisco.ALTO)

        MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk,
            filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.SAIDA,
            quantidade=Decimal('5'),
            usuario_id=self.usuario.pk,
            lote_id=lote.pk,
        )

        self.assertFalse(AlertaVencimento.objects.filter(lote=lote, resolvido=False).exists())

    def test_reserva_de_produto_com_lote_exige_lote_vigente_disponivel(self):
        produto = self.criar_produto(controla_lote=True)
        lote = self.criar_lote(
            produto,
            numero='VENCIDO',
            validade=timezone.now().date() - timedelta(days=1),
        )
        MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk,
            filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal('5'),
            usuario_id=self.usuario.pk,
            lote_id=lote.pk,
        )

        with self.assertRaises(EstoqueInsuficienteError):
            MovimentacaoService.reservar_estoque(
                produto_id=produto.pk,
                filial_id=self.filial.pk,
                quantidade=Decimal('1'),
            )

        estoque = Estoque.objects.get(produto=produto, filial=self.filial)
        self.assertEqual(estoque.quantidade_reservada, Decimal('0.000'))
        self.assertEqual(estoque.quantidade_disponivel, Decimal('5.000'))

    def test_inventario_bloqueia_movimentacao_comum_e_reserva(self):
        produto = self.criar_produto(controla_lote=False)
        Inventario.objects.create(
            filial=self.filial,
            descricao='Contagem geral',
            bloquear_movimentacoes=True,
            data_inicio=timezone.now(),
            usuario_inicio=self.usuario,
        )

        with self.assertRaisesMessage(DadosInvalidosError, 'inventario aberto'):
            MovimentacaoService.registrar_movimentacao(
                produto_id=produto.pk,
                filial_id=self.filial.pk,
                tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
                quantidade=Decimal('5'),
                usuario_id=self.usuario.pk,
            )
        with self.assertRaisesMessage(DadosInvalidosError, 'inventario aberto'):
            MovimentacaoService.reservar_estoque(
                produto_id=produto.pk,
                filial_id=self.filial.pk,
                quantidade=Decimal('1'),
                permitir_sem_estoque=True,
            )

    def test_ajuste_do_proprio_inventario_nao_e_bloqueado(self):
        produto = self.criar_produto(controla_lote=False)
        inventario = Inventario.objects.create(
            filial=self.filial,
            descricao='Contagem geral',
            bloquear_movimentacoes=True,
            data_inicio=timezone.now(),
            usuario_inicio=self.usuario,
        )

        mov = MovimentacaoService.ajustar_manual(
            produto_id=produto.pk,
            filial_id=self.filial.pk,
            quantidade_nova=Decimal('7'),
            usuario_id=self.usuario.pk,
            justificativa='Ajuste do inventario',
            documento_tipo=MovimentacaoEstoque.DocumentoTipo.INVENTARIO,
            documento_id=inventario.pk,
        )

        estoque = Estoque.objects.get(produto=produto, filial=self.filial)
        self.assertEqual(mov.documento_tipo, MovimentacaoEstoque.DocumentoTipo.INVENTARIO)
        self.assertEqual(estoque.quantidade_atual, Decimal('7.000'))

    def test_transferencia_exige_produto_vinculado_a_filial_destino(self):
        produto = self.criar_produto(controla_lote=False)
        MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk,
            filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal('5'),
            usuario_id=self.usuario.pk,
        )

        with self.assertRaisesMessage(DadosInvalidosError, 'filial de destino'):
            MovimentacaoService.transferir_entre_filiais(
                produto_id=produto.pk,
                filial_origem_id=self.filial.pk,
                filial_destino_id=self.filial_destino.pk,
                quantidade=Decimal('2'),
                usuario_id=self.usuario.pk,
            )

        estoque = Estoque.objects.get(produto=produto, filial=self.filial)
        self.assertEqual(estoque.quantidade_atual, Decimal('5.000'))
        self.assertFalse(
            Estoque.objects.filter(produto=produto, filial=self.filial_destino).exists()
        )

    def test_transferencia_rastreia_documento_e_lote_no_destino(self):
        produto = self.criar_produto(controla_lote=True)
        ProdutoFilial.objects.create(produto=produto, filial=self.filial_destino)
        lote_origem = self.criar_lote(
            produto,
            numero='TRF-LOTE',
            validade=timezone.now().date() + timedelta(days=20),
        )
        lote_destino = self.criar_lote(
            produto,
            numero='TRF-LOTE',
            quantidade='0',
            validade=timezone.now().date() + timedelta(days=20),
            filial=self.filial_destino,
        )
        lote_destino.status = LoteProduto.Status.ESGOTADO
        lote_destino.save(update_fields=['status'])

        MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk,
            filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal('5'),
            usuario_id=self.usuario.pk,
            lote_id=lote_origem.pk,
            valor_unitario=Decimal('2.50'),
        )

        mov_saida, mov_entrada = MovimentacaoService.transferir_entre_filiais(
            produto_id=produto.pk,
            filial_origem_id=self.filial.pk,
            filial_destino_id=self.filial_destino.pk,
            quantidade=Decimal('3'),
            usuario_id=self.usuario.pk,
            lote_id=lote_origem.pk,
            observacao='Reposicao filial destino',
        )

        estoque_origem = Estoque.objects.get(produto=produto, filial=self.filial)
        estoque_destino = Estoque.objects.get(produto=produto, filial=self.filial_destino)
        lote_origem.refresh_from_db()
        lote_destino.refresh_from_db()

        self.assertEqual(mov_saida.documento_numero, mov_entrada.documento_numero)
        self.assertTrue(mov_saida.documento_numero.startswith('TRF-'))
        self.assertEqual(mov_saida.documento_id, mov_entrada.pk)
        self.assertEqual(mov_entrada.documento_id, mov_saida.pk)
        self.assertEqual(mov_saida.filial_destino_id, self.filial_destino.pk)
        self.assertEqual(mov_entrada.lote_id, lote_destino.pk)
        self.assertEqual(estoque_origem.quantidade_atual, Decimal('2.000'))
        self.assertEqual(estoque_destino.quantidade_atual, Decimal('3.000'))
        self.assertEqual(lote_origem.quantidade_atual, Decimal('2.000'))
        self.assertEqual(lote_destino.quantidade_atual, Decimal('3.000'))
        self.assertEqual(lote_destino.status, LoteProduto.Status.ATIVO)

    def test_reativar_transferencia_refaz_estoque_e_limpa_cancelamento(self):
        produto = self.criar_produto(controla_lote=False)
        ProdutoFilial.objects.create(produto=produto, filial=self.filial_destino)
        MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk,
            filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal('5'),
            usuario_id=self.usuario.pk,
        )
        mov_saida, _ = MovimentacaoService.transferir_entre_filiais(
            produto_id=produto.pk,
            filial_origem_id=self.filial.pk,
            filial_destino_id=self.filial_destino.pk,
            quantidade=Decimal('2'),
            usuario_id=self.usuario.pk,
            permitir_sem_lote=True,
            vincular_destino=True,
            documento_numero='TRF-TESTE-REATIVAR',
        )

        cancelar_transferencia(
            mov_saida.documento_numero,
            self.filial,
            self.usuario,
        )
        documento = DocumentoFiscal.objects.create(
            filial=self.filial,
            tipo_documento=TipoDocumentoFiscal.NFE,
            origem_tipo='transferencia_estoque',
            origem_id=mov_saida.pk,
            numero=1,
            serie=5,
            emitente_cnpj=self.filial.cnpj,
            status=StatusDocumentoFiscal.AUTORIZADA,
            valor_total=Decimal('2.00'),
            usuario=self.usuario,
        )
        MovimentacaoEstoque.objects.filter(
            documento_numero=mov_saida.documento_numero,
        ).update(documento_fiscal=documento)
        reativar_transferencia(
            mov_saida.documento_numero,
            self.filial,
            self.usuario,
        )

        mov_saida.refresh_from_db()
        estoque_origem = Estoque.objects.get(produto=produto, filial=self.filial)
        estoque_destino = Estoque.objects.get(
            produto=produto,
            filial=self.filial_destino,
        )
        self.assertFalse(mov_saida.transferencia_cancelada)
        self.assertIsNone(mov_saida.transferencia_cancelada_em)
        self.assertEqual(estoque_origem.quantidade_atual, Decimal('3.000'))
        self.assertEqual(estoque_destino.quantidade_atual, Decimal('2.000'))
        self.assertEqual(
            MovimentacaoEstoque.objects.filter(
                documento_numero='RAT-TRF-TESTE-REATIV',
            ).count(),
            2,
        )
