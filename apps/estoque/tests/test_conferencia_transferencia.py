from decimal import Decimal

from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.core.models import (
    Empresa,
    Filial,
    Notificacao,
    NotificacaoLeitura,
    PerfilAcesso,
    Usuario,
)
from apps.core.services.exceptions import DadosInvalidosError
from apps.core.views.notificacoes import NotificacaoAbrirView
from apps.estoque.models import (
    ConferenciaTransferencia,
    Estoque,
    MovimentacaoEstoque,
)
from apps.estoque.services.conferencia_transferencia import (
    concluir_conferencia,
    criar_conferencia_transferencia,
    garantir_conferencias_recebidas,
)
from apps.core.services.notificacao_service import (
    reabrir_notificacao_transferencia,
)
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.estoque.services.transferencia_cancelamento import cancelar_transferencia
from apps.produtos.models import (
    Produto,
    ProdutoFilial,
    UnidadeMedida,
    UnidadeMedidaFilial,
)


class ConferenciaTransferenciaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Empresa Teste LTDA',
            nome_fantasia='Empresa Teste',
            cnpj='12345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.origem = Filial.objects.create(
            empresa=cls.empresa,
            razao_social='Origem LTDA',
            nome_fantasia='Origem',
            cnpj='12345678000192',
            uf='RN',
        )
        cls.destino = Filial.objects.create(
            empresa=cls.empresa,
            razao_social='Destino LTDA',
            nome_fantasia='Destino',
            cnpj='12345678000273',
            uf='RN',
        )
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa,
            nome='Administrador',
            is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='conferencia@inoovated.com',
            nome='Usuario Conferencia',
            password='teste1234',
            empresa=cls.empresa,
            filial=cls.origem,
            perfil=perfil,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa,
            sigla='UN',
            descricao='Unidade',
            tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(
            unidade=cls.unidade,
            filial=cls.origem,
        )
        UnidadeMedidaFilial.objects.create(
            unidade=cls.unidade,
            filial=cls.destino,
        )

    def criar_produto(self, descricao):
        produto = Produto.objects.create(
            filial=self.origem,
            unidade_medida=self.unidade,
            descricao=descricao,
            ncm='20089900',
            permite_venda_sem_estoque=False,
            preco_custo=Decimal('2.00'),
        )
        ProdutoFilial.objects.create(produto=produto, filial=self.origem)
        ProdutoFilial.objects.create(produto=produto, filial=self.destino)
        return produto

    def criar_transferencia(self, produto, quantidade='5'):
        MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk,
            filial_id=self.origem.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal('10'),
            usuario_id=self.usuario.pk,
            valor_unitario=Decimal('2'),
        )
        saida, _ = MovimentacaoService.transferir_entre_filiais(
            produto_id=produto.pk,
            filial_origem_id=self.origem.pk,
            filial_destino_id=self.destino.pk,
            quantidade=Decimal(quantidade),
            usuario_id=self.usuario.pk,
            permitir_sem_lote=True,
            documento_numero=f'TRF-CONF-{produto.pk}',
        )
        return criar_conferencia_transferencia(
            documento_numero=saida.documento_numero,
            filial_origem=self.origem,
            filial_destino=self.destino,
            usuario=self.usuario,
        )

    def test_criacao_gera_itens_e_notificacao_para_destino(self):
        produto = self.criar_produto('Produto enviado')
        conferencia = self.criar_transferencia(produto)

        self.assertEqual(conferencia.itens.count(), 1)
        self.assertEqual(conferencia.itens.get().quantidade_enviada, Decimal('5'))
        self.assertTrue(Notificacao.objects.filter(
            filial=self.destino,
            tipo=Notificacao.Tipo.TRANSFERENCIA_RECEBIDA,
            referencia_id=str(conferencia.pk),
        ).exists())

    def test_recupera_transferencia_antiga_sem_conferencia_e_notificacao(self):
        produto = self.criar_produto('Produto de transferencia antiga')
        MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk,
            filial_id=self.origem.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal('10'),
            usuario_id=self.usuario.pk,
            valor_unitario=Decimal('2'),
        )
        saida, _ = MovimentacaoService.transferir_entre_filiais(
            produto_id=produto.pk,
            filial_origem_id=self.origem.pk,
            filial_destino_id=self.destino.pk,
            quantidade=Decimal('5'),
            usuario_id=self.usuario.pk,
            permitir_sem_lote=True,
            documento_numero='TRF-ANTIGA-SEM-CONFERENCIA',
        )

        self.assertFalse(ConferenciaTransferencia.objects.filter(
            documento_numero=saida.documento_numero,
        ).exists())

        criadas = garantir_conferencias_recebidas(self.destino)

        self.assertEqual(len(criadas), 1)
        conferencia = criadas[0]
        self.assertEqual(conferencia.itens.count(), 1)
        self.assertTrue(Notificacao.objects.filter(
            filial=self.destino,
            tipo=Notificacao.Tipo.TRANSFERENCIA_RECEBIDA,
            referencia_id=str(conferencia.pk),
            ativa=True,
        ).exists())

    def test_falta_parcial_ajusta_estoque_do_destino(self):
        produto = self.criar_produto('Produto com falta')
        conferencia = self.criar_transferencia(produto)
        item = conferencia.itens.get()

        concluir_conferencia(
            conferencia_id=conferencia.pk,
            filial_destino=self.destino,
            usuario=self.usuario,
            itens={
                str(item.pk): {
                    'ocorrencia': 'faltante',
                    'quantidade_recebida': '3',
                    'observacao': 'Faltaram duas unidades',
                },
            },
        )

        conferencia.refresh_from_db()
        estoque = Estoque.objects.get(produto=produto, filial=self.destino)
        self.assertEqual(
            conferencia.status,
            ConferenciaTransferencia.Status.COM_DIVERGENCIA,
        )
        self.assertEqual(estoque.quantidade_atual, Decimal('3'))
        self.assertTrue(MovimentacaoEstoque.objects.filter(
            filial=self.destino,
            produto=produto,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.AJUSTE_MENOS,
            quantidade=Decimal('2'),
        ).exists())

        with self.assertRaisesMessage(
            DadosInvalidosError,
            'Estorne a conferência antes de cancelar',
        ):
            cancelar_transferencia(
                conferencia.documento_numero,
                self.origem,
                self.usuario,
            )

    def test_recebimento_correto_conclui_sem_ajuste(self):
        produto = self.criar_produto('Produto correto')
        conferencia = self.criar_transferencia(produto)
        item = conferencia.itens.get()

        concluir_conferencia(
            conferencia_id=conferencia.pk,
            filial_destino=self.destino,
            usuario=self.usuario,
            itens={
                str(item.pk): {
                    'ocorrencia': 'ok',
                    'quantidade_recebida': '5',
                    'observacao': '',
                },
            },
        )

        conferencia.refresh_from_db()
        self.assertEqual(
            conferencia.status,
            ConferenciaTransferencia.Status.CONFERIDA,
        )
        self.assertFalse(MovimentacaoEstoque.objects.filter(
            filial=self.destino,
            documento_numero=f'CONF-{conferencia.pk}',
        ).exists())

    def test_item_trocado_retira_esperado_e_adiciona_recebido(self):
        esperado = self.criar_produto('Produto esperado')
        recebido = self.criar_produto('Produto recebido no lugar')
        conferencia = self.criar_transferencia(esperado)
        item = conferencia.itens.get()

        concluir_conferencia(
            conferencia_id=conferencia.pk,
            filial_destino=self.destino,
            usuario=self.usuario,
            itens={
                str(item.pk): {
                    'ocorrencia': 'trocado',
                    'quantidade_recebida': '0',
                    'produto_recebido_id': str(recebido.pk),
                    'quantidade_produto_recebido': '5',
                    'observacao': 'Veio outro produto',
                },
            },
        )

        estoque_esperado = Estoque.objects.get(
            produto=esperado,
            filial=self.destino,
        )
        estoque_recebido = Estoque.objects.get(
            produto=recebido,
            filial=self.destino,
        )
        self.assertEqual(estoque_esperado.quantidade_atual, Decimal('0'))
        self.assertEqual(estoque_recebido.quantidade_atual, Decimal('5'))

    def test_abrir_notificacao_marca_como_lida_e_redireciona(self):
        produto = self.criar_produto('Produto notificado')
        conferencia = self.criar_transferencia(produto)
        notificacao = Notificacao.objects.get(
            filial=self.destino,
            tipo=Notificacao.Tipo.TRANSFERENCIA_RECEBIDA,
            referencia_id=str(conferencia.pk),
        )
        request = RequestFactory().get(
            reverse('core:notificacao-abrir', kwargs={'pk': notificacao.pk}),
        )
        request.user = self.usuario
        request.filial_ativa = self.destino

        response = NotificacaoAbrirView.as_view()(request, pk=notificacao.pk)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse(
                'estoque:transferencia-conferencia-detail',
                kwargs={'pk': conferencia.pk},
            ),
        )
        self.assertTrue(NotificacaoLeitura.objects.filter(
            notificacao=notificacao,
            usuario=self.usuario,
        ).exists())

    def test_reativacao_reabre_notificacao_ja_lida(self):
        produto = self.criar_produto('Produto reativado')
        conferencia = self.criar_transferencia(produto)
        notificacao = Notificacao.objects.get(
            filial=self.destino,
            tipo=Notificacao.Tipo.TRANSFERENCIA_RECEBIDA,
            referencia_id=str(conferencia.pk),
        )
        NotificacaoLeitura.objects.create(
            notificacao=notificacao,
            usuario=self.usuario,
        )

        reabrir_notificacao_transferencia(conferencia)

        notificacao.refresh_from_db()
        self.assertTrue(notificacao.ativa)
        self.assertFalse(
            NotificacaoLeitura.objects.filter(
                notificacao=notificacao,
                usuario=self.usuario,
            ).exists(),
        )
