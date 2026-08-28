"""
Consolidação física, individualização fiscal.

A REGRA QUE ESTES TESTES DEFENDEM

O mesmo caminhão, a mesma viagem e a mesma carga NÃO fazem uma operação
fiscal só. Venda, remessa para venda fora, bonificação e retorno viajam
juntas e continuam sendo quatro operações: cada uma com a sua natureza, o
seu CFOP, o seu documento e o seu movimento de estoque.

POR QUE ISSO PRECISA DE TESTE, E NÃO SÓ DE CUIDADO

A pressão para juntar é permanente e parece razoável: é um caminhão só, é um
cliente só, é uma viagem só — e somar "para simplificar" produz uma nota que
descreve uma operação que não aconteceu. O erro não aparece na tela; aparece
na fiscalização, meses depois, quando já não há como desfazer.

O QUE CADA TESTE CERCA:

  · TRÊS NATUREZAS NA MESMA CARGA VIRAM TRÊS DOCUMENTOS, com três CFOPs
    vindos do cadastro — e cada linha da carga aponta só para o seu;

  · O MDF-e CONSOLIDA SEM ALTERAR NADA: ele é documento de transporte, e
    depois dele cada NF-e continua com a natureza que tinha;

  · O RAZÃO TAMBÉM SEPARA. Bonificação sai como bonificação, venda sai como
    saída — e quando o mesmo produto e lote sobem em duas naturezas, cada
    movimento sabe qual nota o ampara;

  · A NOTA DE UMA NATUREZA NÃO CARREGA ITEM DE OUTRA.
"""
from decimal import Decimal

from django.test import TestCase

from apps.cadastros.models import Cliente, ClienteFilial
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import DadosInvalidosError
from apps.estoque.models import LoteProduto, MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.financeiro.constants.enums import StatusContaReceber
from apps.financeiro.models.formas_pagamento import (
    CondicaoPagamento, FormaPagamento,
)
from apps.financeiro.models.receber_pagar import ContaReceber
from apps.fiscal.models import NaturezaOperacao, RegraNaturezaOperacao
from apps.fiscal.services.natureza_operacao_service import (
    NaturezaOperacaoService,
)
from apps.logistica.models import (
    EntregaBonificacao, ItemCarga, ItemVendaViagem, SaldoCarga, VendaViagem,
    Viagem,
)
from apps.logistica.services.bonificacao_nfe import BonificacaoNFeService
from apps.logistica.services.entrega_bonificacao import (
    EntregaBonificacaoService,
)
from apps.logistica.services.fluxo_viagem import FluxoViagemService
from apps.logistica.services.mdfe_viagem import MDFeViagemService
from apps.logistica.services.remessa_nfe import RemessaVendaForaService
from apps.logistica.services.retorno_nfe import RetornoVendaForaService
from apps.logistica.services.venda_fora_nfe import VendaForaNFeService
from apps.logistica.services.venda_viagem import VendaViagemService
from apps.logistica.services.viagem import ViagemService
from apps.logistica.services.vinculo_remessa import VinculoRemessaService
from apps.produtos.models import Produto, ProdutoFilial
from apps.produtos.models.unidade import UnidadeMedida, UnidadeMedidaFilial

ZERO = Decimal('0')
T = VendaViagem.Tipo


class FiscalBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Fiscal LTDA', nome_fantasia='Fiscal',
            cnpj='81345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='81345678000272',
            uf='RN', cidade='Natal', is_matriz=True,
            endereco='Av. Principal', numero='100', bairro='Centro',
            cep='59000000', inscricao_estadual='123456789',
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='UN', descricao='Unidade',
            tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='fiscal@rota.local', nome='Fiscal', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Mercado da Esquina',
            cpf_cnpj='12345678901', uf='RN', cidade='Natal',
        )
        ClienteFilial.objects.create(cliente=cls.cliente, filial=cls.filial)

        for codigo, descricao, especie, cfop in (
            ('venda', 'Venda', NaturezaOperacao.Especie.VENDA, '5102'),
            ('remessa', 'Remessa', NaturezaOperacao.Especie.REMESSA_VENDA_FORA, '5904'),
            ('vendafora', 'Venda fora', NaturezaOperacao.Especie.VENDA_FORA, '5103'),
            ('retorno', 'Retorno', NaturezaOperacao.Especie.RETORNO_VENDA_FORA, '1904'),
            ('bonif', 'Bonificação', NaturezaOperacao.Especie.BONIFICACAO, '5910'),
        ):
            natureza = NaturezaOperacao.objects.create(
                filial=cls.filial, codigo=codigo, descricao=descricao,
                especie=especie,
                exige_destinatario=especie in (
                    NaturezaOperacao.Especie.VENDA,
                    NaturezaOperacao.Especie.VENDA_FORA,
                    NaturezaOperacao.Especie.BONIFICACAO,
                ),
            )
            RegraNaturezaOperacao.objects.create(natureza=natureza, cfop=cfop)
            setattr(cls, f'natureza_{codigo}', natureza)

        # A prazo gera título; dinheiro na entrega, não.
        cls.a_prazo = FormaPagamento.objects.create(
            empresa=cls.empresa, descricao='Boleto 30 dias', tipo='boleto',
            gera_parcelas=True, prazo_liquidacao_dias=30,
        )
        cls.a_vista = FormaPagamento.objects.create(
            empresa=cls.empresa, descricao='Dinheiro', tipo='dinheiro',
            gera_parcelas=False,
        )
        cls.condicao = CondicaoPagamento.objects.create(
            empresa=cls.empresa, descricao='3x 30/60/90',
            numero_parcelas=3, intervalo_dias=30, dias_primeira_parcela=30,
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        self.produto = self._produto('P1', '5000')
        self.lote = LoteProduto.objects.create(
            filial=self.filial, produto=self.produto, numero_lote='L-1',
            quantidade_inicial=Decimal('1000'), quantidade_atual=Decimal('1000'),
        )

    # ── Fixtures ─────────────────────────────────────────────────────────

    def _produto(self, codigo, saldo):
        produto = Produto.objects.create(
            filial=self.filial, unidade_medida=self.unidade,
            descricao=f'Produto {codigo}', codigo=codigo, ncm='20079900',
            controla_lote=False, preco_venda=Decimal('10'),
            preco_custo=Decimal('4'),
        )
        ProdutoFilial.objects.create(produto=produto, filial=self.filial)
        MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk, filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal(saldo), usuario_id=self.usuario.pk,
            documento_tipo=MovimentacaoEstoque.DocumentoTipo.OUTRAS,
        )
        return produto

    def _viagem(self, itens):
        viagem = Viagem.objects.create(
            filial=self.filial, numero=Viagem.objects.count() + 1,
            motorista_nome='Seu Zé', veiculo_placa='ABC1D23',
            vendedor=self.usuario, responsavel=self.usuario,
        )
        for dados in itens:
            ViagemService.adicionar_item(viagem, {
                'produto': self.produto, 'valor_unitario': '10', **dados,
            })
        ViagemService.fechar_carga(viagem, usuario=self.usuario)
        viagem.status = Viagem.Status.EM_VENDAS
        viagem.save(update_fields=['status'])
        return viagem




    def _carga_das_tres_naturezas(self):
        """Um caminhão, três operações: venda, remessa e bonificação."""
        return self._viagem([
            {'natureza': self.natureza_venda, 'cliente': self.cliente,
             'quantidade': '150'},
            {'natureza': self.natureza_remessa, 'quantidade': '200'},
            {'natureza': self.natureza_bonif, 'cliente': self.cliente,
             'quantidade': '10'},
        ])


class NaturezaPorLinhaTests(FiscalBase):
    """A natureza fica na linha, e não na viagem."""

    def test_a_mesma_carga_leva_tres_naturezas_separadas(self):
        viagem = self._carga_das_tres_naturezas()

        especies = sorted(
            ItemCarga.objects.filter(viagem=viagem)
            .values_list('natureza__especie', flat=True)
        )

        self.assertEqual(especies, ['bonificacao', 'remessa_venda_fora', 'venda'])

    def test_cada_natureza_tem_o_seu_cfop_do_cadastro(self):
        """
        O CFOP não está no código: mudar o cadastro muda a nota, e é isso
        que permite adequar por UF, regime e orientação da contabilidade.
        """
        viagem = self._carga_das_tres_naturezas()

        cfops = {
            item.natureza.especie: NaturezaOperacaoService.para_item(
                natureza=item.natureza, filial=self.filial,
                produto=item.produto, cliente=item.cliente,
                data=viagem.data_saida,
            ).cfop
            for item in ItemCarga.objects.filter(viagem=viagem)
        }

        self.assertEqual(cfops['venda'], '5102')
        self.assertEqual(cfops['remessa_venda_fora'], '5904')
        self.assertEqual(cfops['bonificacao'], '5910')

    def test_a_remessa_so_ampara_as_linhas_de_remessa(self):
        """Somar "para simplificar" produziria uma nota que descreve uma
        operação que não aconteceu."""
        viagem = self._carga_das_tres_naturezas()

        documento = RemessaVendaForaService.emitir(viagem, self.usuario)

        amparados = ItemCarga.objects.filter(documento_fiscal=documento)
        self.assertEqual(amparados.count(), 1)
        self.assertEqual(
            amparados.first().natureza.especie, 'remessa_venda_fora',
        )
        self.assertEqual(documento.valor_produtos, Decimal('2000.00'))

    def test_o_payload_da_remessa_nao_carrega_item_de_outra_natureza(self):
        viagem = self._carga_das_tres_naturezas()

        payload = RemessaVendaForaService.construir_payload(viagem, 1, 1)

        self.assertEqual(len(payload['items']), 1)
        self.assertEqual(payload['items'][0]['quantidade_comercial'], 200.0)


class RazaoSeparadoTests(FiscalBase):
    """O estoque também distingue as operações."""

    def test_bonificacao_sai_como_bonificacao_e_venda_como_saida(self):
        viagem = self._carga_das_tres_naturezas()

        tipos = sorted(
            MovimentacaoEstoque.objects
            .filter(documento_tipo='viagem', documento_id=viagem.pk)
            .values_list('tipo_operacao', flat=True)
        )

        self.assertEqual(tipos, ['bonificacao', 'saida', 'saida'])

    def test_cada_movimento_sabe_qual_linha_o_gerou(self):
        viagem = self._carga_das_tres_naturezas()

        itens = ItemCarga.objects.filter(viagem=viagem)

        self.assertEqual(itens.filter(movimentacao__isnull=True).count(), 0)
        self.assertEqual(
            len({i.movimentacao_id for i in itens}), itens.count(),
        )

    def test_o_mesmo_produto_em_duas_naturezas_nao_confunde_a_nota(self):
        """
        ESTE É O CASO QUE A CARGA CONSOLIDADA CRIA: o mesmo produto e lote
        sobem no caminhão em duas linhas de naturezas diferentes, e os dois
        movimentos ficam idênticos em tudo menos na natureza. Sem saber qual
        é qual, o extrato do produto ficava sem dizer sob que nota a
        mercadoria saiu.
        """
        viagem = self._viagem([
            {'natureza': self.natureza_venda, 'cliente': self.cliente,
             'quantidade': '150'},
            {'natureza': self.natureza_remessa, 'quantidade': '200'},
        ])

        documento = RemessaVendaForaService.emitir(viagem, self.usuario)

        remessa = ItemCarga.objects.get(
            viagem=viagem, natureza=self.natureza_remessa,
        )
        venda = ItemCarga.objects.get(
            viagem=viagem, natureza=self.natureza_venda,
        )
        remessa.movimentacao.refresh_from_db()
        venda.movimentacao.refresh_from_db()

        self.assertEqual(remessa.movimentacao.documento_fiscal, documento)
        self.assertIsNone(venda.movimentacao.documento_fiscal)


class ConsolidacaoFisicaTests(FiscalBase):
    """O MDF-e junta o transporte, e não as operações."""

    def test_o_mdfe_consolida_sem_mudar_a_natureza_das_notas(self):
        viagem = self._viagem([
            {'natureza': self.natureza_remessa, 'quantidade': '200'},
        ])
        remessa = RemessaVendaForaService.emitir(viagem, self.usuario)
        venda = VendaViagemService.registrar(viagem, {
            'produto': self.produto, 'quantidade': '80',
            'valor_unitario': '10', 'cliente': self.cliente,
        }, usuario=self.usuario)
        nota_da_venda = VendaForaNFeService.emitir(venda, self.usuario)

        antes = (
            remessa.natureza_operacao_descricao,
            nota_da_venda.natureza_operacao_descricao,
        )
        documentos = MDFeViagemService.documentos(viagem)
        remessa.refresh_from_db()
        nota_da_venda.refresh_from_db()

        # As duas entram no mesmo manifesto...
        chaves = {linha['documento'].pk for linha in documentos}
        self.assertIn(remessa.pk, chaves)
        self.assertIn(nota_da_venda.pk, chaves)
        # ...e continuam sendo duas operações diferentes: mesma carga, mesmo
        # veículo, naturezas distintas antes e depois do manifesto.
        self.assertEqual(
            (
                remessa.natureza_operacao_descricao,
                nota_da_venda.natureza_operacao_descricao,
            ),
            antes,
        )
        self.assertNotEqual(
            remessa.natureza_operacao_descricao,
            nota_da_venda.natureza_operacao_descricao,
        )
        self.assertNotEqual(remessa.origem_tipo, nota_da_venda.origem_tipo)

    def test_a_carga_e_uma_so_fisicamente(self):
        """
        A consolidação física é verdadeira e útil: é um caminhão só, e a
        conferência de doca compara o total físico com a contagem.
        """
        viagem = self._carga_das_tres_naturezas()

        resumo = ViagemService.resumo(viagem)

        self.assertEqual(resumo['total_fisico'], Decimal('360'))
        self.assertEqual(len(resumo['por_especie']), 3)
