"""Ajuste manual de estoque com apresentacao (ex: contar "5 caixas" em vez
de digitar direto na unidade base) -- mesmo espirito da conexao ja feita
em apps/compras/tests/test_apresentacao_compra.py e
apps/vendas/tests/test_devolucao.py, agora em
MovimentacaoService.ajustar_manual."""
from decimal import Decimal

from django.test import TestCase

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import DadosInvalidosError
from apps.estoque.models import Estoque, MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.produtos.models import Produto, ProdutoApresentacao, ProdutoFilial, UnidadeMedida


class AjusteManualApresentacaoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Empresa Ajuste Apresentacao LTDA', cnpj='52345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL, codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Filial Ajuste Apresentacao', cnpj='52345678000192', uf='RN',
        )
        cls.perfil = PerfilAcesso.objects.create(empresa=cls.empresa, nome='Admin', is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email='ajuste-apresentacao@inoovated.com', nome='Operador', password='teste1234',
            empresa=cls.empresa, filial=cls.filial, perfil=cls.perfil,
        )
        cls.un = UnidadeMedida.objects.create(empresa=cls.empresa, sigla='UN', descricao='Unidade')
        cls.cx = UnidadeMedida.objects.create(empresa=cls.empresa, sigla='CX', descricao='Caixa')

    def criar_produto(self, descricao='Produto Ajuste'):
        produto = Produto.objects.create(
            filial=self.filial, unidade_medida=self.un, descricao=descricao,
            ncm='39235000', controla_lote=False,
        )
        ProdutoFilial.objects.create(produto=produto, filial=self.filial)
        return produto

    def test_ajuste_de_5_caixas12_define_estoque_em_60_un(self):
        produto = self.criar_produto()
        Estoque.objects.create(produto=produto, filial=self.filial, quantidade_atual=Decimal('10'))
        caixa12 = ProdutoApresentacao.objects.create(
            produto=produto, unidade=self.cx, descricao='Caixa 12', fator_conversao=12,
        )

        mov = MovimentacaoService.ajustar_manual(
            produto_id=produto.pk, filial_id=self.filial.pk,
            quantidade_nova=Decimal('5'), usuario_id=self.usuario.pk,
            justificativa='Contagem fisica em caixas.', apresentacao=caixa12,
        )

        self.assertEqual(mov.quantidade_posterior, Decimal('60.000'))
        self.assertEqual(mov.apresentacao, caixa12)
        self.assertEqual(mov.apresentacao_fator_conversao, Decimal('12.000000'))
        self.assertEqual(mov.quantidade_comercial, Decimal('5.000'))

        estoque = Estoque.objects.get(produto=produto, filial=self.filial)
        self.assertEqual(estoque.quantidade_atual, Decimal('60.000'))

    def test_ajuste_sem_apresentacao_continua_em_unidade_base(self):
        produto = self.criar_produto()
        Estoque.objects.create(produto=produto, filial=self.filial, quantidade_atual=Decimal('10'))

        mov = MovimentacaoService.ajustar_manual(
            produto_id=produto.pk, filial_id=self.filial.pk,
            quantidade_nova=Decimal('25'), usuario_id=self.usuario.pk,
            justificativa='Contagem fisica direta.',
        )

        self.assertEqual(mov.quantidade_posterior, Decimal('25.000'))
        self.assertIsNone(mov.apresentacao)
        self.assertIsNone(mov.apresentacao_fator_conversao)
        self.assertIsNone(mov.quantidade_comercial)

    def test_apresentacao_de_outro_produto_e_rejeitada(self):
        produto = self.criar_produto()
        outro_produto = self.criar_produto(descricao='Outro Produto Ajuste')
        Estoque.objects.create(produto=produto, filial=self.filial, quantidade_atual=Decimal('10'))
        apresentacao_outro = ProdutoApresentacao.objects.create(
            produto=outro_produto, unidade=self.cx, descricao='Caixa 12', fator_conversao=12,
        )

        with self.assertRaises(DadosInvalidosError):
            MovimentacaoService.ajustar_manual(
                produto_id=produto.pk, filial_id=self.filial.pk,
                quantidade_nova=Decimal('5'), usuario_id=self.usuario.pk,
                justificativa='Contagem invalida.', apresentacao=apresentacao_outro,
            )

    def test_apresentacao_que_nao_permite_estoque_e_rejeitada(self):
        produto = self.criar_produto()
        Estoque.objects.create(produto=produto, filial=self.filial, quantidade_atual=Decimal('10'))
        display_apenas = ProdutoApresentacao.objects.create(
            produto=produto, unidade=self.cx, descricao='Display 6 caixas', fator_conversao=6,
            permite_estoque=False,
        )

        with self.assertRaises(DadosInvalidosError):
            MovimentacaoService.ajustar_manual(
                produto_id=produto.pk, filial_id=self.filial.pk,
                quantidade_nova=Decimal('2'), usuario_id=self.usuario.pk,
                justificativa='Contagem invalida.', apresentacao=display_apenas,
            )
