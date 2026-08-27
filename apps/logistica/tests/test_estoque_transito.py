"""
Estoque em trânsito — venda fora do estabelecimento.

O exemplo da especificação, como teste: 1.000 no estoque, remessa de 300.
Depois da saída, 700 físicos e 300 em trânsito — e as 300 NÃO estão
disponíveis para venda no estabelecimento.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import EstoqueInsuficienteError
from apps.estoque.models import Estoque, MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.fiscal.models import NaturezaOperacao, RegraNaturezaOperacao
from apps.logistica.models import Viagem
from apps.logistica.services.estoque_transito import EstoqueEmTransitoService
from apps.logistica.services.viagem import ViagemService
from apps.produtos.models import Produto, ProdutoFilial
from apps.produtos.models.unidade import UnidadeMedida, UnidadeMedidaFilial


class EstoqueEmTransitoTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Transito LTDA', nome_fantasia='Transito',
            cnpj='63345678000191', segmento='polpa_frutas',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='63345678000272',
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
            email='tra@estoque.local', nome='Tra', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.natureza = NaturezaOperacao.objects.create(
            filial=cls.filial, codigo='remessa', descricao='Remessa',
            especie=NaturezaOperacao.Especie.REMESSA_VENDA_FORA,
            exige_destinatario=False,
        )
        RegraNaturezaOperacao.objects.create(natureza=cls.natureza, cfop='5904')

    def setUp(self):
        self.client.force_login(self.usuario)
        self.produto = self._produto('P1', '1000')

    def _produto(self, codigo, saldo):
        produto = Produto.objects.create(
            filial=self.filial, unidade_medida=self.unidade,
            descricao=f'Produto {codigo}', codigo=codigo, ncm='20079900',
            controla_lote=False, preco_custo=Decimal('4'), preco_venda=Decimal('10'),
        )
        ProdutoFilial.objects.create(produto=produto, filial=self.filial)
        MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk, filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal(saldo), usuario_id=self.usuario.pk,
            documento_tipo=MovimentacaoEstoque.DocumentoTipo.OUTRAS,
        )
        return produto

    def _viagem_com_remessa(self, quantidade='300', numero=1, produto=None):
        viagem = Viagem.objects.create(
            filial=self.filial, numero=numero, motorista_nome='Seu Zé',
            veiculo_placa='ABC1D23', vendedor=self.usuario,
        )
        ViagemService.adicionar_item(viagem, {
            'natureza': self.natureza, 'produto': produto or self.produto,
            'quantidade': quantidade, 'valor_unitario': '10',
        })
        ViagemService.fechar_carga(viagem, usuario=self.usuario)
        return viagem

    def _fisico(self, produto=None):
        return Estoque.objects.get(
            produto=produto or self.produto, filial=self.filial,
        ).quantidade_atual

    # ── O exemplo da especificação ───────────────────────────────────────

    def test_antes_da_saida_o_estoque_e_de_mil(self):
        self.assertEqual(self._fisico(), Decimal('1000.000'))

    def test_depois_da_saida_ficam_setecentos_no_fisico(self):
        self._viagem_com_remessa('300')

        self.assertEqual(self._fisico(), Decimal('700.000'))

    def test_depois_da_saida_trezentos_ficam_em_transito(self):
        self._viagem_com_remessa('300')

        self.assertEqual(
            EstoqueEmTransitoService.do_produto(self.filial, self.produto),
            Decimal('300.000'),
        )

    def test_a_soma_continua_mil(self):
        """É a soma que o inventário precisa fechar."""
        self._viagem_com_remessa('300')

        linha = EstoqueEmTransitoService.por_produto(self.filial)[0]

        self.assertEqual(linha['fisico'], Decimal('700.000'))
        self.assertEqual(linha['em_transito'], Decimal('300.000'))
        self.assertEqual(linha['total'], Decimal('1000.000'))

    # ── O ponto central: não fica disponível para venda ──────────────────

    def test_a_mercadoria_em_transito_sai_do_disponivel(self):
        """
        Quem vende consulta `quantidade_disponivel`, e o que saiu já não está
        lá. É o que impede a mesma mercadoria de ser vendida duas vezes.
        """
        self._viagem_com_remessa('300')

        estoque = Estoque.objects.get(produto=self.produto, filial=self.filial)
        self.assertEqual(estoque.quantidade_disponivel, Decimal('700.000'))

    def test_nao_da_para_vender_o_que_esta_na_rua(self):
        """
        Tentar vender 800 depois de 300 saírem é vender mercadoria que não está
        lá.

        O TESTE PASSA PELO CAMINHO DE VENDA, e não pelo mutador de estoque:
        `registrar_movimentacao` é a via de baixo nível e não confere saldo
        para produto sem lote -- quem confere é a saída FEFO, que é por onde a
        venda passa. Bater na porta errada aqui daria a impressão de que o
        sistema deixa vender o que está na rua.
        """
        self._viagem_com_remessa('300')

        with self.assertRaises(EstoqueInsuficienteError):
            MovimentacaoService.registrar_saida_fefo(
                produto_id=self.produto.pk, filial_id=self.filial.pk,
                quantidade=Decimal('800'), usuario_id=self.usuario.pk,
                tipo_operacao=MovimentacaoEstoque.TipoOperacao.SAIDA,
                documento_tipo=MovimentacaoEstoque.DocumentoTipo.OUTRAS,
            )

    def test_o_contrato_de_venda_ve_so_o_que_sobrou(self):
        """
        `saldo_disponivel` é o que o PDV consulta antes de deixar vender -- e
        ele já desconta o que saiu por remessa.
        """
        from apps.pdv.services.produto_vendavel_service import ProdutoVendavelService

        self._viagem_com_remessa('300')

        contrato = ProdutoVendavelService.consultar(
            produto=self.produto, filial=self.filial, quantidade=Decimal('1'),
        )

        self.assertEqual(contrato['saldo_disponivel'], Decimal('700.000'))

    def test_o_que_sobrou_no_estabelecimento_continua_vendavel(self):
        self._viagem_com_remessa('300')

        MovimentacaoService.registrar_movimentacao(
            produto_id=self.produto.pk, filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.SAIDA,
            quantidade=Decimal('700'), usuario_id=self.usuario.pk,
            documento_tipo=MovimentacaoEstoque.DocumentoTipo.OUTRAS,
        )

        self.assertEqual(self._fisico(), Decimal('0.000'))

    # ── O trânsito acompanha o que acontece na rua ───────────────────────

    def test_vender_na_rua_diminui_o_transito(self):
        viagem = self._viagem_com_remessa('300')

        ViagemService.registrar_saida_do_saldo(
            viagem, self.produto, Decimal('120'), 'quantidade_vendida',
        )

        self.assertEqual(
            EstoqueEmTransitoService.do_produto(self.filial, self.produto),
            Decimal('180.000'),
        )

    def test_o_retorno_tira_do_transito_e_devolve_ao_fisico(self):
        viagem = self._viagem_com_remessa('300')

        ViagemService.registrar_retorno(
            viagem, self.produto, Decimal('300'), usuario=self.usuario,
        )

        self.assertEqual(self._fisico(), Decimal('1000.000'))
        self.assertEqual(
            EstoqueEmTransitoService.do_produto(self.filial, self.produto), Decimal('0'),
        )

    def test_viagem_finalizada_some_do_transito(self):
        """
        Ela já prestou contas.

        O ESTADO E' CONSTRUIDO A MAO de proposito: `encerrar` recusa viagem com
        saldo aberto, entao pelo caminho normal isto nunca acontece. Mas dado
        pode chegar assim por migracao ou correcao manual, e o filtro de status
        e' o que impede uma viagem encerrada de continuar contando como
        mercadoria na rua.
        """
        from apps.logistica.models import SaldoCarga

        viagem = self._viagem_com_remessa('300')
        viagem.status = Viagem.Status.FINALIZADA
        viagem.save(update_fields=['status'])

        self.assertEqual(
            SaldoCarga.objects.get(viagem=viagem).quantidade_em_poder,
            Decimal('300.000'),
            'a premissa mudou: o saldo deixou de ficar aberto',
        )
        self.assertEqual(EstoqueEmTransitoService.total(self.filial), Decimal('0'))

    def test_viagem_cancelada_tambem_some_do_transito(self):
        from apps.logistica.models import SaldoCarga

        viagem = self._viagem_com_remessa('300')
        viagem.status = Viagem.Status.CANCELADA
        viagem.save(update_fields=['status'])

        self.assertEqual(
            SaldoCarga.objects.get(viagem=viagem).quantidade_em_poder,
            Decimal('300.000'),
        )
        self.assertEqual(EstoqueEmTransitoService.total(self.filial), Decimal('0'))

    def test_viagem_ainda_em_planejamento_nao_conta(self):
        """Nada saiu: a carga só existe no papel."""
        viagem = Viagem.objects.create(
            filial=self.filial, numero=9, motorista_nome='Zé', vendedor=self.usuario,
        )
        ViagemService.adicionar_item(viagem, {
            'natureza': self.natureza, 'produto': self.produto,
            'quantidade': '50', 'valor_unitario': '10',
        })

        self.assertEqual(EstoqueEmTransitoService.total(self.filial), Decimal('0'))

    # ── Somado entre viagens ─────────────────────────────────────────────

    def test_o_mesmo_produto_em_dois_caminhoes_soma(self):
        """
        Quem olha o estoque quer saber quanto da empresa está fora, não quanto
        está em cada carga.
        """
        self._viagem_com_remessa('300', numero=1)
        self._viagem_com_remessa('200', numero=2)

        linha = EstoqueEmTransitoService.por_produto(self.filial)[0]

        self.assertEqual(linha['em_transito'], Decimal('500.000'))
        self.assertEqual(len(linha['viagens']), 2)

    def test_o_resumo_conta_produtos_e_viagens(self):
        outro = self._produto('P2', '500')
        self._viagem_com_remessa('300', numero=1)
        self._viagem_com_remessa('100', numero=2, produto=outro)

        resumo = EstoqueEmTransitoService.resumo(self.filial)

        self.assertEqual(resumo['produtos'], 2)
        self.assertEqual(resumo['unidades'], Decimal('400.000'))
        self.assertEqual(resumo['viagens'], 2)

    def test_transito_de_outra_filial_nao_aparece(self):
        outra = Filial.objects.create(
            empresa=self.empresa, razao_social='Segunda',
            cnpj='31345678000677', uf='RN', cidade='Mossoro',
        )
        alheia = Viagem.objects.create(
            filial=outra, numero=1, motorista_nome='Zé', vendedor=self.usuario,
        )
        from apps.logistica.models import SaldoCarga
        SaldoCarga.objects.create(
            viagem=alheia, produto=self.produto,
            quantidade_remetida=Decimal('999'),
        )
        alheia.status = Viagem.Status.EM_TRANSITO
        alheia.save(update_fields=['status'])

        self.assertEqual(EstoqueEmTransitoService.total(self.filial), Decimal('0'))

    # ── A tela ───────────────────────────────────────────────────────────

    def test_a_tela_mostra_o_fisico_e_o_transito_lado_a_lado(self):
        self._viagem_com_remessa('300')

        html = self.client.get(reverse('logistica:estoque-transito')).content.decode()

        self.assertIn('Estoque físico', html)
        self.assertIn('Em trânsito', html)
        self.assertIn('Total da empresa', html)
        self.assertIn('700', html)
        self.assertIn('300', html)

    def test_a_tela_diz_que_a_mercadoria_nao_esta_disponivel_aqui(self):
        html = self.client.get(reverse('logistica:estoque-transito')).content.decode()

        self.assertIn('não está disponível para venda', html)

    def test_a_tela_aponta_para_a_viagem(self):
        viagem = self._viagem_com_remessa('300')

        html = self.client.get(reverse('logistica:estoque-transito')).content.decode()

        self.assertIn(reverse('logistica:viagem-detail', args=[viagem.pk]), html)

    def test_a_tela_nao_vaza_sintaxe_de_template(self):
        self._viagem_com_remessa('300')

        html = self.client.get(reverse('logistica:estoque-transito')).content.decode()

        for resto in ('{#', '#}', '{%', '%}'):
            self.assertNotIn(resto, html, 'vazou sintaxe de template no HTML')
