from decimal import Decimal

from django.test import TestCase

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import Deposito, Estoque, MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.producao.models import FichaTecnica, ItemFichaTecnica, OrdemProducao
from apps.producao.services.op_service import OrdemProducaoService
from apps.produtos.models import (
    Produto, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial,
)


class FluxoProducaoSimplesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Industria Limpeza Teste LTDA',
            nome_fantasia='Industria Limpeza',
            cnpj='71345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa,
            razao_social='Fabrica Limpeza',
            nome_fantasia='Fabrica Limpeza',
            cnpj='71345678000192',
            uf='RN',
        )
        cls.perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Administrador Industrial', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='producao.limpeza@inoovated.com',
            nome='Operador Industrial',
            password='teste1234',
            empresa=cls.empresa,
            filial=cls.filial,
            perfil=cls.perfil,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='UN', descricao='Unidade',
            tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        cls.litro = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='L', descricao='Litro',
            tipo=UnidadeMedida.Tipo.VOLUME,
        )
        cls.quilo = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='KG', descricao='Quilograma',
            tipo=UnidadeMedida.Tipo.PESO,
        )
        for unidade in (cls.unidade, cls.litro, cls.quilo):
            UnidadeMedidaFilial.objects.create(unidade=unidade, filial=cls.filial)

        cls.agua = cls._produto(
            'Água tratada', cls.litro, Produto.Finalidade.MATERIA_PRIMA,
            permite_venda=False, custo=Decimal('1.00'),
        )
        cls.bicarbonato = cls._produto(
            'Bicarbonato de sódio', cls.quilo, Produto.Finalidade.MATERIA_PRIMA,
            permite_venda=False, custo=Decimal('10.00'),
        )
        cls.embalagem = cls._produto(
            'Embalagem 800g', cls.unidade, Produto.Finalidade.EMBALAGEM,
            permite_venda=False, custo=Decimal('0.50'),
        )
        cls.sabao = cls._produto(
            'Sabão em pó 800g', cls.unidade, Produto.Finalidade.ACABADO,
            permite_venda=True, custo=Decimal('0'),
        )

        cls.ficha = FichaTecnica.objects.create(
            filial=cls.filial,
            produto_acabado=cls.sabao,
            descricao='Fórmula Sabão em pó 800g',
            quantidade_produzida=Decimal('1'),
            custo_mao_obra_padrao=Decimal('2.00'),
            custo_indireto_padrao=Decimal('1.00'),
            status=FichaTecnica.Status.ATIVA,
        )
        ItemFichaTecnica.objects.create(
            ficha=cls.ficha, materia_prima=cls.agua, quantidade=Decimal('0.1000'),
        )
        ItemFichaTecnica.objects.create(
            ficha=cls.ficha, materia_prima=cls.bicarbonato, quantidade=Decimal('0.0500'),
        )
        ItemFichaTecnica.objects.create(
            ficha=cls.ficha, materia_prima=cls.embalagem, quantidade=Decimal('1.0000'),
        )

    @classmethod
    def _produto(cls, descricao, unidade, finalidade, *, permite_venda, custo):
        produto = Produto.objects.create(
            filial=cls.filial,
            unidade_medida=unidade,
            descricao=descricao,
            ncm='34022000',
            finalidade=finalidade,
            permite_venda=permite_venda,
            preco_custo=custo,
            preco_custo_medio=custo,
            preco_venda=Decimal('12.00') if permite_venda else Decimal('0'),
        )
        ProdutoFilial.objects.create(produto=produto, filial=cls.filial)
        return produto

    def _entrada(self, produto, quantidade, custo):
        return MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk,
            filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=quantidade,
            usuario_id=self.usuario.pk,
            valor_unitario=custo,
            deposito_id=Deposito.producao_id(self.filial.pk),
        )

    def test_encerramento_abate_formula_e_da_entrada_no_produto_acabado(self):
        self._entrada(self.agua, Decimal('20'), Decimal('1.00'))
        self._entrada(self.bicarbonato, Decimal('10'), Decimal('10.00'))
        self._entrada(self.embalagem, Decimal('150'), Decimal('0.50'))

        op = OrdemProducaoService.criar_op(
            ficha_id=self.ficha.pk,
            quantidade_planejada=Decimal('100'),
            filial=self.filial,
            usuario=self.usuario,
        )
        OrdemProducaoService.abrir(op, self.usuario)
        OrdemProducaoService.iniciar(op, self.usuario)
        OrdemProducaoService.encerrar(
            op,
            self.usuario,
            quantidade_produzida=Decimal('98'),
            numero_lote_gerado='SABAO-TESTE-001',
        )

        deposito_id = Deposito.producao_id(self.filial.pk)
        saldos = {
            estoque.produto_id: estoque.quantidade_atual
            for estoque in Estoque.objects.filter(
                filial=self.filial, deposito_id=deposito_id,
            )
        }
        self.assertEqual(saldos[self.agua.pk], Decimal('10.000'))
        self.assertEqual(saldos[self.bicarbonato.pk], Decimal('5.000'))
        self.assertEqual(saldos[self.embalagem.pk], Decimal('50.000'))
        self.assertEqual(saldos[self.sabao.pk], Decimal('98.000'))

        op.refresh_from_db()
        self.assertEqual(op.status, OrdemProducao.Status.ENCERRADA)
        self.assertEqual(op.rendimento, Decimal('98.00'))
        self.assertEqual(op.custo_materia_prima, Decimal('110.00'))
        self.assertEqual(op.custo_mao_obra, Decimal('200.00'))
        self.assertEqual(op.custo_indireto, Decimal('100.00'))
        self.assertEqual(op.custo_total, Decimal('410.00'))
        self.assertEqual(op.lote_gerado.numero_lote, 'SABAO-TESTE-001')
        self.assertEqual(op.lote_gerado.quantidade_atual, Decimal('98.000'))

        movimentos_op = MovimentacaoEstoque.objects.filter(
            documento_tipo=MovimentacaoEstoque.DocumentoTipo.ORDEM_PRODUCAO,
            documento_id=op.pk,
        )
        self.assertEqual(
            movimentos_op.filter(
                tipo_operacao=MovimentacaoEstoque.TipoOperacao.PRODUCAO_SAIDA,
            ).count(),
            3,
        )
        self.assertEqual(
            movimentos_op.filter(
                tipo_operacao=MovimentacaoEstoque.TipoOperacao.PRODUCAO_ENTRADA,
            ).count(),
            1,
        )
