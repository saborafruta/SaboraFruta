"""
A NF-e de bonificação: operação própria, não venda de valor zero.

O ERRO CLÁSSICO que estes testes impedem é tratar a bonificação como "venda
com desconto de 100%". Ele passa na emissão e reaparece na apuração, com
receita declarada que ninguém recebeu.

O QUE ESTES TESTES CERCAM:

  · A BONIFICAÇÃO NÃO É REGISTRADA COMO VENDA em lugar nenhum — nem no
    documento (`origem_tipo` próprio), nem no relatório que lê documentos
    por origem;

  · O CFOP NÃO É FIXADO. Ele vem da regra que a contabilidade escreveu, e a
    mesma bonificação sai com CFOP diferente conforme UF, operação interna
    ou interestadual, regime, NCM e produto;

  · A ROTINA RECUSA O QUE NÃO É DELA: venda emitida por engano pela rotina
    de bonificação sairia com CFOP de cortesia;

  · MOTIVO ANTES DA NOTA. Sai a nota, vai a mercadoria, e "por que demos
    isso?" fica sem resposta para sempre.
"""
from decimal import Decimal

from django.test import TestCase

from apps.cadastros.models import Cliente, ClienteFilial
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import DadosInvalidosError
from apps.estoque.models import MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.financeiro.models.fiscal import DocumentoFiscal
from apps.fiscal.models import NaturezaOperacao, RegraNaturezaOperacao
from apps.logistica.models import VendaViagem, Viagem
from apps.logistica.services.bonificacao_nfe import BonificacaoNFeService
from apps.logistica.services.venda_fora_nfe import VendaForaNFeService
from apps.logistica.services.venda_viagem import VendaViagemService
from apps.logistica.services.viagem import ViagemService
from apps.produtos.models import Produto, ProdutoFilial
from apps.produtos.models.unidade import UnidadeMedida, UnidadeMedidaFilial

T = VendaViagem.Tipo
M = VendaViagem.Motivo


class BonificacaoNFeBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Cortesia LTDA', nome_fantasia='Cortesia',
            cnpj='61345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='61345678000272',
            uf='RN', cidade='Natal', is_matriz=True,
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
            email='rua@cortesia.local', nome='Rua', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Mercado da Esquina',
            cpf_cnpj='12345678901', uf='RN', cidade='Natal',
        )
        ClienteFilial.objects.create(cliente=cls.cliente, filial=cls.filial)
        cls.de_fora = Cliente.objects.create(
            filial=cls.filial, razao_social='Mercado de Fora',
            cpf_cnpj='98765432100', uf='PB', cidade='João Pessoa',
        )
        ClienteFilial.objects.create(cliente=cls.de_fora, filial=cls.filial)

        cls.remessa = NaturezaOperacao.objects.create(
            filial=cls.filial, codigo='remessa', descricao='Remessa venda fora',
            especie=NaturezaOperacao.Especie.REMESSA_VENDA_FORA,
            exige_destinatario=False,
        )
        RegraNaturezaOperacao.objects.create(natureza=cls.remessa, cfop='5904')

    def setUp(self):
        self.natureza = NaturezaOperacao.objects.create(
            filial=self.filial, codigo='bonif',
            descricao='Bonificação de Mercadorias',
            especie=NaturezaOperacao.Especie.BONIFICACAO,
        )
        # A REGRA GERAL: operação interna, sem recorte nenhum.
        self.regra = RegraNaturezaOperacao.objects.create(
            natureza=self.natureza, cfop='5910',
            natureza_operacao_texto='Bonificacao de Mercadorias',
            csosn='102',
        )
        self.produto = self._produto('P1', '1000', ncm='20079900')
        self.viagem = self._viagem_na_rua()

    # ── Fixtures ─────────────────────────────────────────────────────────

    def _produto(self, codigo, saldo, ncm='20079900'):
        produto = Produto.objects.create(
            filial=self.filial, unidade_medida=self.unidade,
            descricao=f'Produto {codigo}', codigo=codigo, ncm=ncm,
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

    def _viagem_na_rua(self, quantidade='300'):
        viagem = Viagem.objects.create(
            filial=self.filial, numero=Viagem.objects.count() + 1,
            motorista_nome='Seu Zé', veiculo_placa='ABC1D23',
            vendedor=self.usuario, responsavel=self.usuario,
        )
        ViagemService.adicionar_item(viagem, {
            'natureza': self.remessa, 'produto': self.produto,
            'quantidade': quantidade, 'valor_unitario': '10',
        })
        ViagemService.fechar_carga(viagem, usuario=self.usuario)
        viagem.status = Viagem.Status.EM_VENDAS
        viagem.save(update_fields=['status'])
        return viagem

    def _bonificar(self, quantidade='20', cliente=None, motivo=M.COMERCIAL):
        return VendaViagemService.registrar(self.viagem, {
            'tipo': T.BONIFICACAO, 'produto': self.produto,
            'quantidade': quantidade, 'valor_unitario': '10',
            'cliente': cliente or self.cliente, 'motivo': motivo,
        }, usuario=self.usuario)

    def _vender(self, quantidade='20'):
        venda_fora = NaturezaOperacao.objects.create(
            filial=self.filial, codigo='venda-fora', descricao='Venda fora',
            especie=NaturezaOperacao.Especie.VENDA_FORA,
        )
        RegraNaturezaOperacao.objects.create(natureza=venda_fora, cfop='5103')
        return VendaViagemService.registrar(self.viagem, {
            'tipo': T.VENDA, 'produto': self.produto, 'quantidade': quantidade,
            'valor_unitario': '10', 'cliente': self.cliente,
        }, usuario=self.usuario)


class OperacaoPropriaTests(BonificacaoNFeBase):
    """A bonificação não é registrada como venda."""

    def test_a_nota_e_arquivada_como_bonificacao(self):
        bonificacao = self._bonificar()

        documento = BonificacaoNFeService.emitir(bonificacao, self.usuario)

        self.assertEqual(documento.origem_tipo, 'viagem_bonificacao')

    def test_a_nota_de_bonificacao_nao_aparece_entre_as_vendas(self):
        """
        Um relatório que lê documentos por origem contaria mercadoria que
        ninguém pagou como receita do mês.
        """
        self._bonificar()
        bonificacao = VendaViagem.objects.get(tipo=T.BONIFICACAO)
        BonificacaoNFeService.emitir(bonificacao, self.usuario)

        vendas = DocumentoFiscal.objects.filter(origem_tipo='viagem_venda_fora')

        self.assertEqual(vendas.count(), 0)
        self.assertEqual(
            DocumentoFiscal.objects.filter(
                origem_tipo='viagem_bonificacao',
            ).count(),
            1,
        )

    def test_a_venda_continua_no_arquivo_dela(self):
        venda = self._vender()

        documento = VendaForaNFeService.emitir(venda, self.usuario)

        self.assertEqual(documento.origem_tipo, 'viagem_venda_fora')

    def test_a_rotina_de_bonificacao_recusa_uma_venda(self):
        """Emitida por engano, a venda sairia com CFOP de cortesia."""
        venda = self._vender()

        with self.assertRaises(DadosInvalidosError) as erro:
            BonificacaoNFeService.emitir(venda, self.usuario)

        self.assertIn('venda', str(erro.exception).lower())

    def test_a_nota_da_bonificacao_e_encontrada_pela_rotina_dela(self):
        bonificacao = self._bonificar()
        documento = BonificacaoNFeService.emitir(bonificacao, self.usuario)

        self.assertEqual(
            BonificacaoNFeService.nota_da_bonificacao(bonificacao), documento,
        )

    def test_nao_emite_duas_notas_para_a_mesma_bonificacao(self):
        bonificacao = self._bonificar()
        BonificacaoNFeService.emitir(bonificacao, self.usuario)

        with self.assertRaises(DadosInvalidosError):
            BonificacaoNFeService.emitir(bonificacao, self.usuario)


class MotivoTests(BonificacaoNFeBase):
    """Motivo antes da nota."""

    def test_sem_motivo_a_nota_nao_sai(self):
        """
        Sai a nota, vai a mercadoria, e "por que demos isso?" fica sem
        resposta para sempre.
        """
        bonificacao = self._bonificar()
        # Entregas antigas nasceram antes de o motivo ser exigido.
        VendaViagem.objects.filter(pk=bonificacao.pk).update(motivo='')
        bonificacao.refresh_from_db()

        with self.assertRaises(DadosInvalidosError) as erro:
            BonificacaoNFeService.emitir(bonificacao, self.usuario)

        self.assertIn('motivo', str(erro.exception).lower())

    def test_a_recusa_por_falta_de_motivo_nao_queima_numero(self):
        from apps.core.models.empresa import Filial

        bonificacao = self._bonificar()
        VendaViagem.objects.filter(pk=bonificacao.pk).update(motivo='')
        bonificacao.refresh_from_db()
        antes = Filial.objects.get(pk=self.filial.pk).proximo_numero_nfe

        with self.assertRaises(DadosInvalidosError):
            BonificacaoNFeService.emitir(bonificacao, self.usuario)

        self.assertEqual(
            Filial.objects.get(pk=self.filial.pk).proximo_numero_nfe, antes,
        )


class RegrasFiscaisTests(BonificacaoNFeBase):
    """
    O CFOP não é fixado — ele vem da regra cadastrada.

    Cada teste aqui muda UM recorte da parametrização e mostra a nota mudar
    junto, sem tocar em uma linha de código.
    """

    def _cfop(self, bonificacao):
        return BonificacaoNFeService.construir_payload(
            bonificacao, 1, 1,
        )['items'][0]['cfop']

    def test_a_regra_geral_vale_quando_nao_ha_recorte(self):
        self.assertEqual(self._cfop(self._bonificar()), '5910')

    def test_operacao_interestadual_tem_a_sua(self):
        """Cliente de outra UF muda o CFOP sem ninguém escolher nada."""
        RegraNaturezaOperacao.objects.create(
            natureza=self.natureza, cfop='6910', somente_interestadual=True,
        )

        self.assertEqual(
            self._cfop(self._bonificar(cliente=self.de_fora)), '6910',
        )
        # E a interna continua na dela.
        self.assertEqual(self._cfop(self._bonificar()), '5910')

    def test_regra_por_uf_de_destino(self):
        RegraNaturezaOperacao.objects.create(
            natureza=self.natureza, cfop='6911', uf_destino='PB',
        )

        self.assertEqual(
            self._cfop(self._bonificar(cliente=self.de_fora)), '6911',
        )

    def test_regra_por_ncm(self):
        RegraNaturezaOperacao.objects.create(
            natureza=self.natureza, cfop='5912', ncm='20079900',
        )

        self.assertEqual(self._cfop(self._bonificar()), '5912')

    def test_regra_por_regime_tributario(self):
        RegraNaturezaOperacao.objects.create(
            natureza=self.natureza, cfop='5913',
            regime_tributario=self.empresa.regime_tributario,
        )

        self.assertEqual(self._cfop(self._bonificar()), '5913')

    def test_regra_por_produto_vence_a_geral(self):
        """A mais específica ganha — é o que permite a exceção de um item."""
        RegraNaturezaOperacao.objects.create(
            natureza=self.natureza, cfop='5914', produto=self.produto,
        )

        self.assertEqual(self._cfop(self._bonificar()), '5914')

    def test_a_tributacao_tambem_vem_da_regra(self):
        RegraNaturezaOperacao.objects.create(
            natureza=self.natureza, cfop='5915', produto=self.produto,
            csosn='103', cst_pis='08', aliquota_pis=Decimal('1.65'),
        )

        item = BonificacaoNFeService.construir_payload(
            self._bonificar(), 1, 1,
        )['items'][0]

        self.assertEqual(item['icms_situacao_tributaria'], '103')
        self.assertEqual(item['pis_situacao_tributaria'], '08')
        self.assertEqual(item['pis_aliquota_porcentual'], 1.65)

    def test_o_texto_da_natureza_vem_do_cadastro(self):
        """"Bonificação de Mercadorias" é decisão da contabilidade."""
        payload = BonificacaoNFeService.construir_payload(
            self._bonificar(), 1, 1,
        )

        self.assertEqual(
            payload['natureza_operacao'], 'Bonificacao de Mercadorias',
        )

    def test_sem_regra_para_a_operacao_a_nota_nao_sai(self):
        """
        Não existe padrão de emergência: um CFOP chutado passa na emissão e
        aparece na apuração.
        """
        self.regra.delete()
        bonificacao = self._bonificar()

        with self.assertRaises(DadosInvalidosError) as erro:
            BonificacaoNFeService.emitir(bonificacao, self.usuario)

        self.assertIn('regra fiscal', str(erro.exception).lower())

    def test_a_nota_registra_por_que_aquela_regra(self):
        """
        "Por que saiu 6910 nesta nota?" não pode virar arqueologia de banco
        seis meses depois.
        """
        from apps.fiscal.services.natureza_operacao_service import (
            NaturezaOperacaoService,
        )

        fiscal = NaturezaOperacaoService.para_item(
            natureza=self.natureza, filial=self.filial, produto=self.produto,
            cliente=self.de_fora,
        )

        self.assertEqual(fiscal.justificativa['uf_origem'], 'RN')
        self.assertEqual(fiscal.justificativa['uf_destino'], 'PB')
        self.assertTrue(fiscal.justificativa['interestadual'])
