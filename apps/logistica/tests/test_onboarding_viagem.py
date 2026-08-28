"""
Os primeiros passos da viagem.

O ERRO SEMPRE APARECE NA PIOR HORA. Falta de natureza cadastrada não atrapalha
ninguém enquanto a carga é montada: ela aparece na doca, com o caminhão
encostado e a mercadoria já baixada do estoque, no clique de emitir a nota.

O QUE ESTES TESTES CERCAM:

  · A CONFERÊNCIA CHAMA QUEM DECIDE. Ela pergunta ao mesmo código que vai
    recusar a emissão depois — uma segunda versão da regra diria "tudo
    pronto" no dia em que a primeira mudasse;

  · NATUREZA SEM REGRA NÃO ESTÁ PRONTA: ela existe, o cadastro parece feito,
    e a emissão para do mesmo jeito por falta de CFOP;

  · OBRIGATÓRIO E RECOMENDADO SÃO SEPARADOS. Sem motorista cadastrado a nota
    sai igual — misturar os dois ensinaria a ignorar a lista inteira;

  · O CONVITE SOME quando a filial já rodou viagens e o essencial está
    pronto. Insistir com checklist depois disso vira ruído.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

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
from apps.logistica.services.onboarding_viagem import (
    OnboardingViagemService,
)
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


class OnboardingBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Primeiros Passos LTDA', nome_fantasia='Primeiros Passos',
            cnpj='21345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='21345678000272',
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
            email='passos@rota.local', nome='Passos', password='x' * 12,
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
        self.filial.focusnfe_token = 'token-de-teste'
        self.filial.save(update_fields=['focusnfe_token'])
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




class ProntidaoTests(OnboardingBase):
    """O que a conferência enxerga."""

    def _por_chave(self, filial=None):
        return {
            c['chave']: c
            for c in OnboardingViagemService.checagens(filial or self.filial)
        }

    def test_com_tudo_cadastrado_a_filial_esta_pronta(self):
        resumo = OnboardingViagemService.resumo(self.filial)

        self.assertTrue(resumo['pronto'])
        self.assertEqual(resumo['faltando'], [])

    def test_natureza_que_falta_e_apontada_com_a_mensagem_da_emissao(self):
        """
        A conferência chama o mesmo código que recusa a emissão: a pessoa lê
        aqui a frase que leria na doca, e procura um problema só.
        """
        self.natureza_retorno.ativo = False
        self.natureza_retorno.save(update_fields=['ativo'])

        checagem = self._por_chave()['natureza-retorno_venda_fora']

        self.assertFalse(checagem['pronto'])
        self.assertIn('Nenhuma natureza de operação cadastrada', checagem['detalhe'])

    def test_duas_naturezas_ativas_tambem_travam(self):
        """O CFOP sairia de qualquer uma delas."""
        segunda = NaturezaOperacao.objects.create(
            filial=self.filial, codigo='bonif2', descricao='Bonificação 2',
            especie=NaturezaOperacao.Especie.BONIFICACAO,
        )
        RegraNaturezaOperacao.objects.create(natureza=segunda, cfop='5910')

        checagem = self._por_chave()['natureza-bonificacao']

        self.assertFalse(checagem['pronto'])
        self.assertIn('mais de uma natureza ativa', checagem['detalhe'])

    def test_natureza_sem_regra_nao_esta_pronta(self):
        """
        Ela existe, o cadastro parece feito, e a emissão para do mesmo jeito
        — por falta de CFOP, que é o que a regra traz.
        """
        self.natureza_venda.regras.all().delete()

        checagem = self._por_chave()['natureza-venda']

        self.assertFalse(checagem['pronto'])
        self.assertIn('sem nenhuma regra', checagem['detalhe'])

    def test_sem_token_a_nota_ficaria_parada(self):
        self.filial.focusnfe_token = ''
        self.filial.save(update_fields=['focusnfe_token'])

        checagem = self._por_chave()['focus']

        self.assertFalse(checagem['pronto'])
        self.assertFalse(OnboardingViagemService.resumo(self.filial)['pronto'])

    def test_sem_forma_a_prazo_a_venda_da_rua_nunca_vira_cobranca(self):
        # A EMPRESA JA' NASCE COM FORMAS PADRAO -- cartao de credito entre
        # elas. Para cair no caso "nada a prazo" e' preciso desativar todas,
        # que e' exatamente o que alguem faria sem perceber a consequencia.
        FormaPagamento.objects.filter(
            empresa=self.filial.empresa, gera_parcelas=True,
        ).update(ativo=False)

        checagem = self._por_chave()['pagamento']

        self.assertFalse(checagem['pronto'])
        self.assertIn('recebida na entrega', checagem['detalhe'])

    def test_produto_sem_ncm_e_apontado(self):
        """A SEFAZ rejeita item sem NCM."""
        self.produto.ncm = ''
        self.produto.save(update_fields=['ncm'])

        checagem = self._por_chave()['produtos']

        self.assertFalse(checagem['pronto'])
        self.assertIn('sem NCM', checagem['detalhe'])

    def test_motorista_e_veiculo_sao_recomendados_e_nao_travam(self):
        """
        Sem cadastro a viagem aceita nome e placa digitados — o que se perde
        é o MDF-e vir preenchido.
        """
        checagem = self._por_chave()['transporte']

        self.assertEqual(checagem['peso'], 'recomendado')
        self.assertFalse(checagem['pronto'])
        self.assertTrue(OnboardingViagemService.resumo(self.filial)['pronto'])


class ConviteTests(OnboardingBase):
    """Quando o sistema chama a pessoa para esta tela."""

    def test_filial_sem_viagem_recebe_o_convite(self):
        resumo = OnboardingViagemService.resumo(self.filial)

        self.assertTrue(resumo['primeira_viagem'])
        self.assertTrue(resumo['mostrar_convite'])

    def test_filial_que_ja_roda_e_esta_pronta_nao_e_mais_cobrada(self):
        """Checklist depois que a operação anda vira ruído."""
        Viagem.objects.create(
            filial=self.filial, numero=1, motorista_nome='Seu Zé',
            veiculo_placa='ABC1D23', responsavel=self.usuario,
        )

        resumo = OnboardingViagemService.resumo(self.filial)

        self.assertFalse(resumo['primeira_viagem'])
        self.assertFalse(resumo['mostrar_convite'])

    def test_cadastro_faltando_traz_o_convite_de_volta(self):
        Viagem.objects.create(
            filial=self.filial, numero=1, motorista_nome='Seu Zé',
            veiculo_placa='ABC1D23', responsavel=self.usuario,
        )
        self.filial.focusnfe_token = ''
        self.filial.save(update_fields=['focusnfe_token'])

        self.assertTrue(OnboardingViagemService.resumo(self.filial)['mostrar_convite'])


class TelaTests(OnboardingBase):
    """A tela dos primeiros passos."""

    def _tela(self):
        return self.client.get(
            reverse('logistica:viagem-primeiros-passos'),
        ).content.decode()

    def test_a_tela_lista_o_que_falta_e_leva_ao_dono_do_cadastro(self):
        self.filial.focusnfe_token = ''
        self.filial.save(update_fields=['focusnfe_token'])

        html = self._tela()

        self.assertIn('Token da Focus NFe', html)
        self.assertIn('Abrir parâmetros fiscais', html)
        self.assertIn('/gestao/parametros/', html)

    def test_pronta_a_tela_convida_a_criar_a_viagem(self):
        html = self._tela()

        self.assertIn('O essencial está pronto', html)
        self.assertIn('Criar a primeira viagem', html)

    def test_a_lista_de_viagens_chama_quem_ainda_nao_rodou(self):
        html = self.client.get(
            reverse('logistica:viagem-list'),
        ).content.decode()

        self.assertIn('Primeira viagem desta filial', html)
        self.assertIn('Ver primeiros passos', html)

    def test_a_lista_para_de_cobrar_quem_ja_rodou(self):
        Viagem.objects.create(
            filial=self.filial, numero=1, motorista_nome='Seu Zé',
            veiculo_placa='ABC1D23', responsavel=self.usuario,
        )

        html = self.client.get(
            reverse('logistica:viagem-list'),
        ).content.decode()

        self.assertNotIn('Ver primeiros passos', html)
