"""
Onde cada caixa esteve: estoque → remessa → viagem → venda/bonificação/retorno.

O EXEMPLO DA ESPECIFICAÇÃO: 300 unidades saem, a remessa nº 500 as ampara,
a viagem nº 125 as leva, 180 são vendidas, 10 bonificadas e 110 voltam.

O QUE ESTES TESTES CERCAM:

  · A ORDEM É A DOS FATOS, e não a das tabelas: estoque, remessa, viagem e
    só então venda, bonificação e retorno. Ler fora dessa ordem é o que faz
    alguém concluir que a mercadoria voltou antes de sair;

  · UMA CADEIA POR VIAGEM. Um produto que viajou cinco vezes tem cinco
    cadeias — somá-las daria um total sem significado, porque "quanto
    voltou?" só existe dentro de uma viagem;

  · ETAPA SEM REGISTRO É DITA, e não escondida: remessa sem NF-e aparece
    como remessa sem NF-e, porque é a etapa que falta que alguém precisa
    ver;

  · NADA É GRAVADO AQUI. A cadeia é leitura do razão, da carga e das vendas
    — um histórico guardado à parte seria uma segunda verdade sobre a mesma
    caixa.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.cadastros.models import Cliente, ClienteFilial
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import DadosInvalidosError
from apps.estoque.models import MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.fiscal.models import NaturezaOperacao, RegraNaturezaOperacao
from apps.logistica.models import ItemCarga, VendaViagem, Viagem
from apps.logistica.services.rastreabilidade import RastreabilidadeService
from apps.logistica.services.remessa_nfe import RemessaVendaForaService
from apps.logistica.services.retorno_nfe import RetornoVendaForaService
from apps.logistica.services.venda_viagem import VendaViagemService
from apps.logistica.services.viagem import ViagemService
from apps.produtos.models import Produto, ProdutoFilial
from apps.produtos.models.unidade import UnidadeMedida, UnidadeMedidaFilial

ZERO = Decimal('0')
T = VendaViagem.Tipo


class RastreioBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Rastro LTDA', nome_fantasia='Rastro',
            cnpj='51345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='51345678000272',
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
            email='rastro@rota.local', nome='Rastro', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Mercado da Esquina',
            cpf_cnpj='12345678901', uf='RN', cidade='Natal',
        )
        ClienteFilial.objects.create(cliente=cls.cliente, filial=cls.filial)

        cls.venda = NaturezaOperacao.objects.create(
            filial=cls.filial, codigo='venda', descricao='Venda',
            especie=NaturezaOperacao.Especie.VENDA,
        )
        RegraNaturezaOperacao.objects.create(natureza=cls.venda, cfop='5102')
        cls.remessa = NaturezaOperacao.objects.create(
            filial=cls.filial, codigo='remessa', descricao='Remessa',
            especie=NaturezaOperacao.Especie.REMESSA_VENDA_FORA,
            exige_destinatario=False,
        )
        RegraNaturezaOperacao.objects.create(natureza=cls.remessa, cfop='5904')
        cls.bonificacao = NaturezaOperacao.objects.create(
            filial=cls.filial, codigo='bonif', descricao='Bonificação',
            especie=NaturezaOperacao.Especie.BONIFICACAO,
        )
        RegraNaturezaOperacao.objects.create(natureza=cls.bonificacao, cfop='5910')

    def setUp(self):
        self.client.force_login(self.usuario)
        self.produto = self._produto('P1', '5000')

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

    def _viagem(self, venda='0', remessa='0', bonificacao='0'):
        viagem = Viagem.objects.create(
            filial=self.filial, numero=Viagem.objects.count() + 1,
            motorista_nome='Seu Zé', veiculo_placa='ABC1D23',
            vendedor=self.usuario, responsavel=self.usuario,
        )
        for natureza, quantidade, cliente in (
            (self.venda, venda, self.cliente),
            (self.remessa, remessa, None),
            (self.bonificacao, bonificacao, self.cliente),
        ):
            if Decimal(quantidade) <= ZERO:
                continue
            ViagemService.adicionar_item(viagem, {
                'natureza': natureza, 'produto': self.produto,
                'cliente': cliente, 'quantidade': quantidade,
                'valor_unitario': '10',
            })
        ViagemService.fechar_carga(viagem, usuario=self.usuario)
        viagem.status = Viagem.Status.EM_VENDAS
        viagem.save(update_fields=['status'])
        return viagem

    def _entregar_na_rua(self, viagem, quantidade, tipo=T.VENDA):
        dados = {
            'tipo': tipo, 'produto': self.produto, 'quantidade': quantidade,
            'valor_unitario': '10', 'cliente': self.cliente,
        }
        if tipo == T.BONIFICACAO:
            dados['motivo'] = VendaViagem.Motivo.BRINDE
        return VendaViagemService.registrar(viagem, dados, usuario=self.usuario)

    def _viagem_do_exemplo(self):
        """A cadeia da especificação: 300 → remessa → viagem → 180/10/110."""
        viagem = self._viagem(remessa='300')
        RemessaVendaForaService.emitir(viagem, self.usuario)
        self._entregar_na_rua(viagem, '180')
        self._entregar_na_rua(viagem, '10', tipo=T.BONIFICACAO)
        ViagemService.registrar_retorno(
            viagem, self.produto, Decimal('110'), usuario=self.usuario,
        )
        return viagem

    def _cadeia(self, viagem):
        return RastreabilidadeService.cadeia(viagem, self.produto)

    def _tela(self, produto=None):
        return self.client.get(
            reverse('logistica:rastreabilidade'),
            {'produto': (produto or self.produto).pk},
        ).content.decode()




class CadeiaTests(RastreioBase):
    """A linha inteira, na ordem dos fatos."""

    def test_o_exemplo_da_especificacao(self):
        viagem = self._viagem_do_exemplo()

        cadeia = self._cadeia(viagem)

        self.assertEqual(cadeia['carga'], Decimal('300'))
        self.assertEqual(cadeia['remetido'], Decimal('300'))
        self.assertEqual(cadeia['vendido'], Decimal('180'))
        self.assertEqual(cadeia['bonificado_na_rua'], Decimal('10'))
        self.assertEqual(cadeia['retornado'], Decimal('110'))
        self.assertEqual(cadeia['em_poder'], ZERO)

    def test_as_etapas_vem_na_ordem_dos_fatos(self):
        """
        O estoque baixa, a remessa ampara, a viagem sai — e só então há
        venda, bonificação e retorno.
        """
        viagem = self._viagem_do_exemplo()

        etapas = [e['etapa'] for e in self._cadeia(viagem)['eventos']]

        self.assertEqual(etapas[:3], ['estoque', 'remessa', 'viagem'])
        self.assertEqual(etapas[-1], 'retorno')
        self.assertIn('venda', etapas)
        self.assertIn('bonificacao', etapas)

    def test_a_remessa_entra_com_numero_e_serie(self):
        viagem = self._viagem_do_exemplo()
        remessa = RemessaVendaForaService.nota_da_viagem(viagem)

        evento = [
            e for e in self._cadeia(viagem)['eventos'] if e['etapa'] == 'remessa'
        ][0]

        self.assertIn(str(remessa.numero), evento['titulo'])
        self.assertEqual(evento['quantidade'], Decimal('300'))

    def test_remessa_sem_nfe_aparece_como_remessa_sem_nfe(self):
        """A etapa que falta é exatamente a que alguém precisa ver."""
        viagem = self._viagem(remessa='300')

        evento = [
            e for e in self._cadeia(viagem)['eventos'] if e['etapa'] == 'remessa'
        ][0]

        self.assertIn('sem NF-e', evento['titulo'])

    def test_a_baixa_de_estoque_traz_o_saldo_antes_e_depois(self):
        """
        É o movimento que prova a saída — e é ele que a conferência de
        estoque vai encontrar.
        """
        viagem = self._viagem(remessa='300')

        evento = [
            e for e in self._cadeia(viagem)['eventos'] if e['etapa'] == 'estoque'
        ][0]

        self.assertEqual(evento['quantidade'], Decimal('300'))
        self.assertIn('5000', evento['detalhe'])
        self.assertIn('4700', evento['detalhe'])

    def test_o_retorno_traz_a_volta_ao_estoque_e_a_nota(self):
        viagem = self._viagem_do_exemplo()
        natureza = NaturezaOperacao.objects.create(
            filial=self.filial, codigo='retorno', descricao='Retorno',
            especie=NaturezaOperacao.Especie.RETORNO_VENDA_FORA,
            exige_destinatario=False,
        )
        RegraNaturezaOperacao.objects.create(natureza=natureza, cfop='1904')
        nota = RetornoVendaForaService.emitir(viagem, self.usuario)

        retornos = [
            e for e in self._cadeia(viagem)['eventos'] if e['etapa'] == 'retorno'
        ]

        self.assertEqual(retornos[0]['quantidade'], Decimal('110'))
        self.assertIn(str(nota.numero), retornos[-1]['titulo'])

    def test_a_bonificacao_da_carga_tambem_entra_na_cadeia(self):
        """
        Ela sai endereçada da doca, e não na rua: fora da cadeia, a cortesia
        sumiria entre a baixa de estoque e o retorno.
        """
        viagem = self._viagem(bonificacao='20')

        eventos = [
            e for e in self._cadeia(viagem)['eventos']
            if e['etapa'] == 'bonificacao'
        ]

        self.assertEqual(len(eventos), 1)
        self.assertEqual(eventos[0]['quantidade'], Decimal('20'))
        self.assertIn('Mercado da Esquina', eventos[0]['titulo'])

    def test_o_que_ficou_na_rua_e_dito(self):
        viagem = self._viagem(remessa='300')
        self._entregar_na_rua(viagem, '50')

        eventos = [
            e for e in self._cadeia(viagem)['eventos'] if e['etapa'] == 'pendente'
        ]

        self.assertEqual(eventos[0]['quantidade'], Decimal('250'))

    def test_uma_cadeia_por_viagem(self):
        """
        Somar as viagens daria um total sem significado: "quanto voltou?" só
        existe dentro de uma viagem.
        """
        self._viagem(remessa='300')
        self._viagem(remessa='100')

        cadeias = RastreabilidadeService.do_produto(self.produto, self.filial)

        self.assertEqual(len(cadeias), 2)
        self.assertEqual(
            [c['carga'] for c in cadeias], [Decimal('100'), Decimal('300')],
        )

    def test_produto_que_nunca_viajou_nao_entra_na_escolha(self):
        """
        Escolher um produto sem viagem devolveria uma tela vazia que parece
        falha do sistema, e não ausência de viagem.
        """
        self._viagem(remessa='300')
        outro = self._produto('P2', '100')

        rastreaveis = RastreabilidadeService.produtos_rastreaveis(self.filial)

        self.assertIn(self.produto, rastreaveis)
        self.assertNotIn(outro, rastreaveis)

    def test_a_cadeia_nao_grava_nada(self):
        """
        Um histórico guardado à parte seria uma segunda verdade sobre a mesma
        caixa.
        """
        viagem = self._viagem_do_exemplo()
        antes = MovimentacaoEstoque.objects.count()

        RastreabilidadeService.cadeia(viagem, self.produto)

        self.assertEqual(MovimentacaoEstoque.objects.count(), antes)


class TelaTests(RastreioBase):
    """O que a pessoa vê."""

    def test_a_tela_mostra_a_cadeia_do_produto(self):
        viagem = self._viagem_do_exemplo()

        html = self._tela()

        self.assertIn('Rastreabilidade', html)
        self.assertIn(f'{viagem.numero:06d}', html)
        for numero in ('300', '180', '110'):
            self.assertIn(numero, html)

    def test_sem_produto_a_tela_pede_a_escolha(self):
        self._viagem_do_exemplo()

        html = self.client.get(
            reverse('logistica:rastreabilidade'),
        ).content.decode()

        self.assertIn('Escolha um produto', html)

    def test_produto_de_outra_filial_nao_rastreia(self):
        """
        A tela filtra, mas quem decide é o servidor: pedir o produto pelo id
        não pode atravessar a filial.
        """
        self._viagem_do_exemplo()
        outra = Filial.objects.create(
            empresa=self.empresa, razao_social='Filial 2',
            cnpj='51345678000353', uf='RN', cidade='Mossoró',
        )
        alheio = Produto.objects.create(
            filial=outra, unidade_medida=self.unidade, descricao='Alheio',
            codigo='X9', ncm='20079900', controla_lote=False,
        )

        html = self.client.get(
            reverse('logistica:rastreabilidade'), {'produto': alheio.pk},
        ).content.decode()

        self.assertIn('Escolha um produto', html)
