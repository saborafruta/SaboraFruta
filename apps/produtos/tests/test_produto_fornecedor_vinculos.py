from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.messages.storage.fallback import FallbackStorage
from django.db import IntegrityError
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.models import Fornecedor, FornecedorFilial
from apps.compras.models import EntradaNF, ItemEntradaNF
from apps.compras.services.entrada_produto_service import MARCADOR_VINCULO_REMOVIDO
from apps.compras.views.entrada import EntradaNFConferenciaView, EntradaNFDesvincularItemView
from apps.core.models import Empresa, Filial, LogSistema, PerfilAcesso, Usuario
from apps.produtos.models import (
    CategoriaProduto,
    CategoriaProdutoFilial,
    Produto,
    ProdutoFilial,
    ProdutoFornecedorEquivalencia,
    UnidadeMedida,
    UnidadeMedidaFilial,
)
from apps.produtos.views.produto import ProdutoFornecedorVinculoDeleteView, ProdutoUpdateView


class ProdutoFornecedorVinculoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Empresa Vinculo Produto LTDA',
            nome_fantasia='Empresa Vinculo Produto',
            cnpj='62345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa,
            razao_social='Filial Vinculo Produto',
            nome_fantasia='Filial Vinculo',
            cnpj='62345678000192',
            uf='RN',
        )
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa,
            nome='Administrador',
            is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='produto-vinculo@inoovated.com',
            nome='Usuario Produto Vinculo',
            password='teste1234',
            empresa=cls.empresa,
            filial=cls.filial,
            perfil=perfil,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa,
            sigla='UN',
            descricao='Unidade',
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        cls.categoria = CategoriaProduto.objects.create(
            empresa=cls.empresa,
            filial=cls.filial,
            nome='Polpas',
        )
        CategoriaProdutoFilial.objects.create(categoria=cls.categoria, filial=cls.filial)
        cls.fornecedor = Fornecedor.objects.create(
            filial=cls.filial,
            tipo_pessoa='J',
            razao_social='Fornecedor XML LTDA',
            nome_fantasia='Fornecedor XML',
            cpf_cnpj='12345678000199',
            uf='RN',
        )
        FornecedorFilial.objects.create(fornecedor=cls.fornecedor, filial=cls.filial)

    def setUp(self):
        self.factory = RequestFactory()

    def request(self, method='get', data=None):
        factory_method = self.factory.post if method == 'post' else self.factory.get
        request = factory_method('/produtos/vinculos/', data or {})
        request.user = self.usuario
        request.filial_ativa = self.filial
        request.session = self.client.session
        request._messages = FallbackStorage(request)
        return request

    def criar_produto(self):
        produto = Produto.objects.create(
            filial=self.filial,
            unidade_medida=self.unidade,
            categoria=self.categoria,
            descricao='Polpa Acerola 300 ML',
            codigo='PA300',
            codigo_barras='7891000000001',
            ncm='20089900',
            preco_venda=Decimal('10.00'),
            preco_custo=Decimal('4.00'),
            ativo=True,
        )
        ProdutoFilial.objects.create(produto=produto, filial=self.filial, ativo=True)
        return produto

    def produto_post_data(self, produto, **overrides):
        data = {
            'descricao': produto.descricao,
            'codigo': produto.codigo,
            'codigo_barras': produto.codigo_barras,
            'descricao_curta': produto.descricao_curta,
            'observacao': produto.observacao,
            'categoria': str(produto.categoria_id or self.categoria.pk),
            'subcategoria': '',
            'marca': '',
            'fornecedor': '',
            'linha_producao': '',
            'tipo_produto': produto.tipo_produto,
            'unidade_medida': str(produto.unidade_medida_id),
            'unidade_medida_compra': '',
            'fator_conversao_compra': '1,00',
            'condicao_armazenamento': produto.condicao_armazenamento,
            'temperatura_minima': '',
            'temperatura_maxima': '',
            'umidade_relativa': '',
            'descricao_completa': produto.descricao_completa,
            'especificacoes_tecnicas': '[]',
            'ncm': produto.ncm,
            'cest': produto.cest,
            'origem_produto': str(produto.origem_produto),
            'classe_fiscal': '',
            'cfop_venda_interna': produto.cfop_venda_interna or '5102',
            'cfop_venda_interestadual': produto.cfop_venda_interestadual or '6102',
            'cfop_venda_exportacao': produto.cfop_venda_exportacao,
            'cfop_compra': produto.cfop_compra or '1102',
            'cfop_devolucao': produto.cfop_devolucao,
            'cfop_devolucao_compra': produto.cfop_devolucao_compra,
            'cst_csosn': produto.cst_csosn,
            'cst_pis': produto.cst_pis,
            'cst_cofins': produto.cst_cofins,
            'cst_ipi': produto.cst_ipi,
            'codigo_enquadramento_ipi': produto.codigo_enquadramento_ipi,
            'aliquota_ipi': '0,00',
            'codigo_beneficio_fiscal_icms': produto.codigo_beneficio_fiscal_icms,
            'modalidade_bc_icms': produto.modalidade_bc_icms,
            'aliquota_icms': '0,00',
            'reducao_bc_icms': '0,00',
            'aliquota_fcp': '0,00',
            'modalidade_bc_icms_st': produto.modalidade_bc_icms_st,
            'mva_icms_st': '0,00',
            'reducao_bc_icms_st': '0,00',
            'aliquota_icms_st': '0,00',
            'aliquota_fcp_st': '0,00',
            'aliquota_pis': '0,00',
            'aliquota_cofins': '0,00',
            'natureza_receita_pis_cofins': produto.natureza_receita_pis_cofins,
            'ex_tipi': produto.ex_tipi,
            'cst_cbs': produto.cst_cbs,
            'classificacao_tributaria_cbs': produto.classificacao_tributaria_cbs,
            'aliquota_cbs': '0,00',
            'reducao_cbs': '0,00',
            'cst_ibs': produto.cst_ibs,
            'classificacao_tributaria_ibs': produto.classificacao_tributaria_ibs,
            'aliquota_ibs_uf': '0,00',
            'aliquota_ibs_municipal': '0,00',
            'reducao_ibs': '0,00',
            'cst_is': produto.cst_is,
            'classificacao_tributaria_is': produto.classificacao_tributaria_is,
            'aliquota_is': '0,00',
            'informacoes_complementares_fiscais': produto.informacoes_complementares_fiscais,
            'beneficios_fiscais_observacoes': produto.beneficios_fiscais_observacoes,
            'preco_custo': str(produto.preco_custo).replace('.', ','),
            'preco_venda': str(produto.preco_venda).replace('.', ','),
            'preco_minimo': str(produto.preco_minimo).replace('.', ','),
            'moeda': produto.moeda,
            'margem_desejada': str(produto.margem_desejada).replace('.', ','),
            'estoque_quantidade': '0,00',
            'estoque_minimo': '0,00',
            'estoque_maximo': '0,00',
            'ponto_reposicao': '0,00',
            'estoque_seguranca': '0,00',
            'lead_time_reposicao_dias': str(produto.lead_time_reposicao_dias),
            'metodo_saida': produto.metodo_saida,
            'localizacao_estoque': produto.localizacao_estoque,
            'dias_aviso_vencimento': str(produto.dias_aviso_vencimento),
            'codigo_balanca': produto.codigo_balanca,
            'tara_padrao': '0,00',
            'variacao_peso_permitida': '5,00',
            'peso_minimo_venda': '0,00',
            'unidade_pesagem': produto.unidade_pesagem,
            'peso_bruto': '',
            'peso_liquido': '',
            'largura': '',
            'altura': '',
            'profundidade': '',
            'unidade_peso': produto.unidade_peso,
            'unidade_dimensao': produto.unidade_dimensao,
            'tipo_embalagem': produto.tipo_embalagem,
            'quantidade_por_embalagem': '1,00',
            'empilhamento_maximo': str(produto.empilhamento_maximo),
            'codigo_barras_extra_1': '',
            'codigo_barras_extra_2': '',
            'codigo_barras_extra_3': '',
            'ativo': 'on',
            'permite_venda_sem_estoque': 'on',
        }
        data.update(overrides)
        return data

    def criar_vinculo(self, produto):
        return ProdutoFornecedorEquivalencia.objects.create(
            produto=produto,
            fornecedor=self.fornecedor,
            fornecedor_cnpj_xml='12345678000199',
            fornecedor_razao_social_xml='Fornecedor XML LTDA',
            codigo_fornecedor='ACER-01',
            descricao_fornecedor='ACEROLA CONGELADA CX',
            ean_utilizado='7891234567890',
            unidade_compra='CX',
            unidade_estoque='UN',
            fator_conversao=Decimal('12.0000'),
            ultimo_custo=Decimal('39.0800'),
            ativo=True,
        )

    def criar_entrada_com_item_vinculado(self, produto):
        entrada = EntradaNF.objects.create(
            filial=self.filial,
            fornecedor=self.fornecedor,
            usuario=self.usuario,
            numero_nf='1001',
            serie_nf='1',
            chave_acesso_nf='',
            data_emissao_nf=timezone.localdate(),
            data_entrada=timezone.now(),
            status=EntradaNF.Status.AGUARDANDO_CONFERENCIA,
            valor_produtos=Decimal('39.08'),
            valor_total=Decimal('39.08'),
            emitente_cnpj_xml='12345678000199',
            emitente_razao_social_xml='Fornecedor XML LTDA',
        )
        return ItemEntradaNF.objects.create(
            entrada=entrada,
            produto=produto,
            numero_item=1,
            quantidade=Decimal('1.000'),
            quantidade_xml=Decimal('1.000'),
            quantidade_estoque=Decimal('12.000'),
            quantidade_recebida=Decimal('12.000'),
            unidade_xml='CX',
            unidade_estoque='UN',
            fator_conversao=Decimal('12.0000'),
            valor_unitario=Decimal('39.0800'),
            valor_bruto=Decimal('39.08'),
            valor_total=Decimal('39.08'),
            ean_xml='7891234567890',
            codigo_produto_fornecedor='ACER-01',
            descricao_xml='ACEROLA CONGELADA CX',
        )

    def test_cadastro_do_produto_exibe_vinculos_de_fornecedor(self):
        produto = self.criar_produto()
        vinculo = self.criar_vinculo(produto)

        response = ProdutoUpdateView.as_view()(self.request(), pk=produto.pk)

        html = response.content.decode()
        self.assertContains(response, 'Vinculos com fornecedores')
        self.assertContains(response, 'Fornecedor XML')
        self.assertContains(response, 'ACER-01')
        self.assertContains(response, 'ACEROLA CONGELADA CX')
        self.assertContains(response, '7891234567890')
        self.assertIn(f'produto-vinculo-delete-{vinculo.pk}', html)

    def test_cadastro_do_produto_abre_mesmo_se_vinculos_falharem(self):
        produto = self.criar_produto()

        with patch(
            'apps.produtos.views.produto.ProdutoFornecedorEquivalencia.objects.select_related',
            side_effect=Exception('schema parcial em producao'),
        ):
            response = ProdutoUpdateView.as_view()(self.request(), pk=produto.pk)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Editar -')
        self.assertContains(response, 'Nenhum vinculo de fornecedor salvo para este produto.')

    def test_produto_com_margem_extrema_salva_sem_estourar_decimal(self):
        produto = self.criar_produto()
        produto.preco_custo = Decimal('100000.00')
        produto.preco_venda = Decimal('1.00')

        produto.calcular_margem()
        produto.save()

        produto.refresh_from_db()
        self.assertEqual(produto.margem_lucro, Decimal('-999.99'))
        self.assertEqual(produto.markup, Decimal('-99.9990'))

    def test_edicao_produto_salva_mesmo_se_ajuste_estoque_falhar(self):
        produto = self.criar_produto()
        data = self.produto_post_data(
            produto,
            descricao='Polpa Acerola Editada',
            estoque_quantidade='5,00',
        )

        with patch(
            'apps.produtos.views.produto.MovimentacaoService.ajustar_manual',
            side_effect=Exception('inventario bloqueado'),
        ):
            response = ProdutoUpdateView.as_view()(
                self.request(method='post', data=data),
                pk=produto.pk,
            )

        produto.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(produto.descricao, 'Polpa Acerola Editada')

    def test_edicao_produto_salva_percentuais_fiscais_vazios_com_zero(self):
        produto = self.criar_produto()
        data = self.produto_post_data(
            produto,
            aliquota_cbs='',
            reducao_cbs='',
            aliquota_ibs_uf='',
            aliquota_ibs_municipal='',
            reducao_ibs='',
            aliquota_is='',
        )

        response = ProdutoUpdateView.as_view()(
            self.request(method='post', data=data),
            pk=produto.pk,
        )

        produto.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(produto.aliquota_cbs, Decimal('0.0000'))
        self.assertEqual(produto.reducao_cbs, Decimal('0.0000'))
        self.assertEqual(produto.aliquota_ibs_uf, Decimal('0.0000'))
        self.assertEqual(produto.aliquota_ibs_municipal, Decimal('0.0000'))
        self.assertEqual(produto.reducao_ibs, Decimal('0.0000'))
        self.assertEqual(produto.aliquota_is, Decimal('0.0000'))

    def test_edicao_produto_salva_parametros_numericos_vazios_com_padrao(self):
        produto = self.criar_produto()
        data = self.produto_post_data(
            produto,
            fator_conversao_compra='',
            aliquota_ipi='',
            estoque_minimo='',
            estoque_maximo='',
            ponto_reposicao='',
            estoque_seguranca='',
            tara_padrao='',
            variacao_peso_permitida='',
            peso_minimo_venda='',
            quantidade_por_embalagem='',
            empilhamento_maximo='',
        )

        response = ProdutoUpdateView.as_view()(
            self.request(method='post', data=data),
            pk=produto.pk,
        )

        produto.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(produto.fator_conversao_compra, Decimal('1.000000'))
        self.assertEqual(produto.aliquota_ipi, Decimal('0.00'))
        self.assertEqual(produto.estoque_minimo, Decimal('0.000'))
        self.assertEqual(produto.estoque_maximo, Decimal('0.000'))
        self.assertEqual(produto.ponto_reposicao, Decimal('0.000'))
        self.assertEqual(produto.estoque_seguranca, Decimal('0.000'))
        self.assertEqual(produto.tara_padrao, Decimal('0.000'))
        self.assertEqual(produto.variacao_peso_permitida, Decimal('5.00'))
        self.assertEqual(produto.peso_minimo_venda, Decimal('0.000'))
        self.assertEqual(produto.quantidade_por_embalagem, Decimal('1.000'))
        self.assertEqual(produto.empilhamento_maximo, 0)

    def test_edicao_produto_falha_sem_retornar_500(self):
        produto = self.criar_produto()
        data = self.produto_post_data(produto, descricao='Polpa sem 500')

        with patch.object(Produto, 'save', side_effect=Exception('falha de banco')):
            response = ProdutoUpdateView.as_view()(
                self.request(method='post', data=data),
                pk=produto.pk,
            )

        self.assertEqual(response.status_code, 200)

    def test_edicao_produto_informa_campo_quando_banco_rejeita_nulo(self):
        produto = self.criar_produto()
        data = self.produto_post_data(produto, descricao='Polpa sem campo fiscal')

        with patch.object(
            Produto,
            'save',
            side_effect=IntegrityError('null value in column "aliquota_cbs" violates not-null constraint'),
        ):
            response = ProdutoUpdateView.as_view()(
                self.request(method='post', data=data),
                pk=produto.pk,
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Aliquota CBS')
        self.assertContains(response, 'Informe 0,00')

    def test_remover_vinculo_desativa_equivalencia_sem_apagar_historico(self):
        produto = self.criar_produto()
        vinculo = self.criar_vinculo(produto)

        response = ProdutoFornecedorVinculoDeleteView.as_view()(
            self.request(method='post'),
            pk=produto.pk,
            vinculo_pk=vinculo.pk,
        )

        vinculo.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertFalse(vinculo.ativo)
        self.assertTrue(
            LogSistema.objects.filter(
                modulo='produtos',
                registro_id=produto.pk,
                dados_novos__evento='Vinculo de fornecedor removido',
            ).exists()
        )

    def test_remover_vinculo_libera_item_de_entrada_aberta_para_nova_vinculacao(self):
        produto = self.criar_produto()
        vinculo = self.criar_vinculo(produto)
        item = self.criar_entrada_com_item_vinculado(produto)

        ProdutoFornecedorVinculoDeleteView.as_view()(
            self.request(method='post'),
            pk=produto.pk,
            vinculo_pk=vinculo.pk,
        )

        item.refresh_from_db()
        item.entrada.refresh_from_db()
        self.assertIsNone(item.produto_id)
        self.assertIn(MARCADOR_VINCULO_REMOVIDO, item.observacao)
        self.assertEqual(item.entrada.status, EntradaNF.Status.AGUARDANDO_VINCULOS)

    def test_remover_vinculo_libera_item_mesmo_com_ean_alterado(self):
        produto = self.criar_produto()
        vinculo = self.criar_vinculo(produto)
        item = self.criar_entrada_com_item_vinculado(produto)
        item.ean_xml = '7890000000999'
        item.codigo_produto_fornecedor = ''
        item.save(update_fields=['ean_xml', 'codigo_produto_fornecedor', 'updated_at'])

        ProdutoFornecedorVinculoDeleteView.as_view()(
            self.request(method='post'),
            pk=produto.pk,
            vinculo_pk=vinculo.pk,
        )

        item.refresh_from_db()
        self.assertIsNone(item.produto_id)
        self.assertIn(MARCADOR_VINCULO_REMOVIDO, item.observacao)

    def test_conferencia_libera_item_ainda_vinculado_a_equivalencia_removida(self):
        produto = self.criar_produto()
        vinculo = self.criar_vinculo(produto)
        vinculo.ativo = False
        vinculo.save(update_fields=['ativo', 'updated_at'])
        item = self.criar_entrada_com_item_vinculado(produto)

        response = EntradaNFConferenciaView.as_view()(self.request(), pk=item.entrada_id)

        item.refresh_from_db()
        item.entrada.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(item.produto_id)
        self.assertIn(MARCADOR_VINCULO_REMOVIDO, item.observacao)
        self.assertEqual(item.entrada.status, EntradaNF.Status.AGUARDANDO_VINCULOS)

    def test_conferencia_exibe_icone_para_editar_produto_em_sobreposicao(self):
        produto = self.criar_produto()
        self.criar_vinculo(produto)
        item = self.criar_entrada_com_item_vinculado(produto)

        response = EntradaNFConferenciaView.as_view()(self.request(), pk=item.entrada_id)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'entrada-desvincular-form-{item.pk}')
        self.assertContains(response, reverse('compras:entrada-desvincular-item', args=[item.entrada_id, item.pk]))
        self.assertContains(response, f'{reverse("produtos:produto-update", args=[produto.pk])}?popup=1')
        self.assertContains(response, 'data-product-edit-open')
        self.assertNotContains(response, 'Abrir cadastro:')

    def test_edicao_produto_popup_retorna_mensagem_para_conferencia(self):
        produto = self.criar_produto()
        data = self.produto_post_data(produto, descricao='Polpa Acerola Popup')
        request = self.request(method='post', data=data)
        request.GET = {'popup': '1'}

        response = ProdutoUpdateView.as_view()(request, pk=produto.pk)

        produto.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'entradaProdutoAtualizado')
        self.assertEqual(produto.descricao, 'Polpa Acerola Popup')

    def test_desvincular_item_na_conferencia_impede_revinculo_automatico(self):
        produto = self.criar_produto()
        self.criar_vinculo(produto)
        item = self.criar_entrada_com_item_vinculado(produto)

        response = EntradaNFDesvincularItemView.as_view()(
            self.request(method='post'),
            pk=item.entrada_id,
            item_id=item.pk,
        )

        item.refresh_from_db()
        item.entrada.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('compras:entrada-conferencia', args=[item.entrada_id]))
        self.assertIsNone(item.produto_id)
        self.assertIn(MARCADOR_VINCULO_REMOVIDO, item.observacao)
        self.assertEqual(item.entrada.status, EntradaNF.Status.AGUARDANDO_VINCULOS)

        EntradaNFConferenciaView.as_view()(self.request(), pk=item.entrada_id)

        item.refresh_from_db()
        self.assertIsNone(item.produto_id)

    def test_conferencia_revincula_item_manual_quando_produto_recebe_ean_da_nota(self):
        produto = self.criar_produto()
        self.criar_vinculo(produto)
        item = self.criar_entrada_com_item_vinculado(produto)
        item.ean_xml = '7890000000999'
        item.save(update_fields=['ean_xml', 'updated_at'])

        EntradaNFDesvincularItemView.as_view()(
            self.request(method='post'),
            pk=item.entrada_id,
            item_id=item.pk,
        )
        item.refresh_from_db()
        self.assertIsNone(item.produto_id)
        self.assertIn(MARCADOR_VINCULO_REMOVIDO, item.observacao)

        produto.codigo_barras = item.ean_xml
        produto.save(update_fields=['codigo_barras', 'updated_at'])
        Produto.objects.filter(pk=produto.pk).update(updated_at=item.updated_at + timedelta(seconds=1))

        EntradaNFConferenciaView.as_view()(self.request(), pk=item.entrada_id)

        item.refresh_from_db()
        self.assertEqual(item.produto_id, produto.pk)
        self.assertNotIn(MARCADOR_VINCULO_REMOVIDO, item.observacao)

    def test_conferencia_nao_repete_mensagem_sem_equivalencia_no_lote(self):
        produto = self.criar_produto()
        self.criar_vinculo(produto)
        item = self.criar_entrada_com_item_vinculado(produto)
        EntradaNFDesvincularItemView.as_view()(
            self.request(method='post'),
            pk=item.entrada_id,
            item_id=item.pk,
        )

        response = EntradaNFConferenciaView.as_view()(self.request(), pk=item.entrada_id)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Produto sem equivalencia interna.')

    def test_conferencia_vincula_automaticamente_item_pendente_por_ean_do_produto(self):
        produto = self.criar_produto()
        item = self.criar_entrada_com_item_vinculado(produto)
        item.produto = None
        item.ean_xml = produto.codigo_barras
        item.fator_conversao = Decimal('1.0000')
        item.quantidade_estoque = Decimal('1.000')
        item.quantidade_recebida = Decimal('1.000')
        item.quantidade = Decimal('1.000')
        item.save(update_fields=[
            'produto',
            'ean_xml',
            'fator_conversao',
            'quantidade_estoque',
            'quantidade_recebida',
            'quantidade',
            'updated_at',
        ])

        response = EntradaNFConferenciaView.as_view()(self.request(), pk=item.entrada_id)

        item.refresh_from_db()
        item.entrada.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(item.produto, produto)
        self.assertEqual(item.fator_conversao, Decimal('1'))
        self.assertEqual(item.entrada.status, EntradaNF.Status.AGUARDANDO_CONFERENCIA)
