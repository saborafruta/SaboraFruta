"""
Vendas durante a viagem.

O SALDO DA CARGA É O LIMITE. Não é validação de formulário — é a regra que
impede a mesma mercadoria de ser vendida duas vezes e o saldo de fechar
negativo no retorno, quando já não há como saber o que aconteceu.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.cadastros.models import Cliente, ClienteFilial
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import DadosInvalidosError
from apps.estoque.models import Estoque, MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.fiscal.models import NaturezaOperacao, RegraNaturezaOperacao
from apps.logistica.models import SaldoCarga, VendaViagem, Viagem
from apps.logistica.services.venda_viagem import VendaViagemService
from apps.logistica.services.viagem import ViagemService
from apps.produtos.models import Produto, ProdutoFilial
from apps.produtos.models.unidade import UnidadeMedida, UnidadeMedidaFilial


class VendaDuranteAViagemTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Rua LTDA', nome_fantasia='Rua',
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
            email='rua@viagem.local', nome='Rua', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Mercado da Esquina',
            cpf_cnpj='12345678901', uf='RN', cidade='Natal',
            endereco='Rua B', numero='50', bairro='Centro',
        )
        ClienteFilial.objects.create(cliente=cls.cliente, filial=cls.filial)
        cls.natureza = NaturezaOperacao.objects.create(
            filial=cls.filial, codigo='remessa', descricao='Remessa',
            especie=NaturezaOperacao.Especie.REMESSA_VENDA_FORA,
            exige_destinatario=False,
        )
        RegraNaturezaOperacao.objects.create(natureza=cls.natureza, cfop='5904')

    def setUp(self):
        self.client.force_login(self.usuario)
        self.produto = self._produto('P1', '1000')
        self.viagem = self._viagem_na_rua('300')
        self.url = reverse('logistica:viagem-venda-create', args=[self.viagem.pk])

    def _produto(self, codigo, saldo):
        produto = Produto.objects.create(
            filial=self.filial, unidade_medida=self.unidade,
            descricao=f'Produto {codigo}', codigo=codigo, ncm='20079900',
            controla_lote=False, preco_venda=Decimal('10'), preco_custo=Decimal('4'),
        )
        ProdutoFilial.objects.create(produto=produto, filial=self.filial)
        MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk, filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal(saldo), usuario_id=self.usuario.pk,
            documento_tipo=MovimentacaoEstoque.DocumentoTipo.OUTRAS,
        )
        return produto

    def _viagem_na_rua(self, quantidade='300', numero=1):
        viagem = Viagem.objects.create(
            filial=self.filial, numero=numero, motorista_nome='Seu Zé',
            veiculo_placa='ABC1D23', vendedor=self.usuario,
        )
        ViagemService.adicionar_item(viagem, {
            'natureza': self.natureza, 'produto': self.produto,
            'quantidade': quantidade, 'valor_unitario': '10',
        })
        ViagemService.fechar_carga(viagem, usuario=self.usuario)
        viagem.status = Viagem.Status.EM_VENDAS
        viagem.save(update_fields=['status'])
        return viagem

    def _vender(self, quantidade, **extras):
        dados = {
            'produto': self.produto, 'quantidade': quantidade,
            'valor_unitario': '10', 'cliente': self.cliente,
        }
        dados.update(extras)
        return VendaViagemService.registrar(
            self.viagem, dados, usuario=self.usuario,
        )

    def _saldo(self):
        return SaldoCarga.objects.get(
            viagem=self.viagem, produto=self.produto,
        ).quantidade_em_poder

    # ── O exemplo da especificação ───────────────────────────────────────

    def test_a_carga_comeca_com_trezentos(self):
        self.assertEqual(self._saldo(), Decimal('300.000'))

    def test_a_primeira_venda_de_cinquenta_deixa_duzentos_e_cinquenta(self):
        self._vender('50')

        self.assertEqual(self._saldo(), Decimal('250.000'))

    def test_a_segunda_venda_de_oitenta_deixa_cento_e_setenta(self):
        self._vender('50')
        self._vender('80')

        self.assertEqual(self._saldo(), Decimal('170.000'))

    def test_o_saldo_e_conferido_a_cada_venda(self):
        """A verificação é automática, e não depende de a tela lembrar."""
        self._vender('50')

        disponivel = VendaViagemService.saldo_disponivel(self.viagem, self.produto)

        self.assertEqual(disponivel, Decimal('250.000'))

    # ── Nunca mais do que a carga ────────────────────────────────────────

    def test_nao_vende_mais_do_que_esta_no_caminhao(self):
        with self.assertRaises(DadosInvalidosError) as erro:
            self._vender('301')

        self.assertIn('Nunca se vende mais', str(erro.exception))
        self.assertEqual(self._saldo(), Decimal('300.000'))

    def test_nem_somando_varias_vendas(self):
        """
        É a regra que impede a mesma mercadoria de ser vendida duas vezes.
        """
        self._vender('200')

        with self.assertRaises(DadosInvalidosError):
            self._vender('150')

        self.assertEqual(self._saldo(), Decimal('100.000'))

    def test_produto_que_nao_viajou_nao_e_vendido(self):
        outro = self._produto('P2', '500')

        with self.assertRaises(DadosInvalidosError) as erro:
            self._vender('10', produto=outro)

        self.assertIn('não está nesta viagem', str(erro.exception))

    def test_a_venda_recusada_nao_deixa_registro(self):
        """
        Deixar a venda entrar e corrigir depois produziria saldo negativo, que
        no retorno é indistinguível de furto.
        """
        with self.assertRaises(DadosInvalidosError):
            self._vender('301')

        self.assertEqual(VendaViagem.objects.count(), 0)

    def test_quantidade_zero_nao_vende(self):
        with self.assertRaises(DadosInvalidosError):
            self._vender('0')

    # ── A venda não mexe no estoque da filial ────────────────────────────

    def test_a_venda_nao_baixa_o_estoque_da_filial(self):
        """
        Aquela mercadoria já saiu de lá quando a carga fechou. Baixar de novo
        contaria a mesma saída duas vezes.
        """
        antes = Estoque.objects.get(
            produto=self.produto, filial=self.filial,
        ).quantidade_atual

        self._vender('50')

        depois = Estoque.objects.get(
            produto=self.produto, filial=self.filial,
        ).quantidade_atual
        self.assertEqual(depois, antes)

    # ── O registro da venda ──────────────────────────────────────────────

    def test_a_venda_guarda_quem_comprou_e_por_quanto(self):
        """
        Sem isso o sistema sabe que 50 saíram do caminhão, mas não para quem
        nem por quanto -- e a prestação de contas vira a palavra do vendedor.
        """
        venda = self._vender('50')

        self.assertEqual(venda.cliente, self.cliente)
        self.assertEqual(venda.cliente_nome, 'Mercado da Esquina')
        self.assertEqual(venda.cliente_documento, '12345678901')
        self.assertEqual(venda.valor_total, Decimal('500.00'))

    def test_o_endereco_e_copiado_e_nao_apontado(self):
        """
        Cliente que muda de endereço depois não pode reescrever o histórico de
        uma entrega já feita.
        """
        venda = self._vender('50')

        self.assertEqual(venda.endereco['cidade'], 'Natal')
        self.assertEqual(venda.endereco['endereco'], 'Rua B')

    def test_venda_para_cliente_nao_cadastrado(self):
        """
        Venda de rua acontece com quem aparece, e exigir cadastro prévio
        pararia a venda na calçada.
        """
        venda = self._vender(
            '20', cliente=None, cliente_nome='Padaria do Zé',
            cliente_documento='123.456.789-01',
            endereco='Av. Principal, 200',
        )

        self.assertIsNone(venda.cliente_id)
        self.assertEqual(venda.cliente_nome, 'Padaria do Zé')
        self.assertEqual(venda.cliente_documento, '12345678901')
        self.assertEqual(venda.endereco['endereco'], 'Av. Principal, 200')

    def test_sem_dizer_para_quem_nao_vende(self):
        with self.assertRaises(DadosInvalidosError) as erro:
            self._vender('10', cliente=None, cliente_nome='')

        self.assertIn('para quem foi a venda', str(erro.exception))

    def test_a_venda_numera_dentro_da_viagem(self):
        primeira = self._vender('10')
        segunda = self._vender('10')

        self.assertEqual((primeira.numero, segunda.numero), (1, 2))

    def test_mais_de_um_produto_na_mesma_venda(self):
        """Um cliente costuma levar vários."""
        outro = self._produto('P2', '500')
        # A carga desta viagem ja' saiu, e o servico recusa mexer nela -- o que
        # esta' certo. Aqui o saldo do segundo produto e' criado direto, que e'
        # o estado em que ele estaria se tivesse subido no caminhao junto.
        SaldoCarga.objects.create(
            viagem=self.viagem, produto=outro, quantidade_remetida=Decimal('100'),
        )
        venda = self._vender('50')

        VendaViagemService.adicionar_item(venda, {
            'produto': outro, 'quantidade': '10', 'valor_unitario': '5',
        })

        venda.refresh_from_db()
        self.assertEqual(venda.itens.count(), 2)
        self.assertEqual(venda.valor_total, Decimal('550.00'))

    # ── Quando dá para vender ────────────────────────────────────────────

    def test_viagem_que_nao_saiu_nao_vende(self):
        parada = Viagem.objects.create(
            filial=self.filial, numero=9, motorista_nome='Zé',
            vendedor=self.usuario,
        )

        with self.assertRaises(DadosInvalidosError) as erro:
            VendaViagemService.registrar(parada, {
                'produto': self.produto, 'quantidade': '10',
                'cliente': self.cliente,
            }, usuario=self.usuario)

        self.assertIn('caminhão fora', str(erro.exception))

    def test_viagem_finalizada_nao_vende(self):
        self.viagem.status = Viagem.Status.FINALIZADA
        self.viagem.save(update_fields=['status'])

        with self.assertRaises(DadosInvalidosError):
            self._vender('10')

    # ── Cancelar devolve ao saldo ────────────────────────────────────────

    def test_cancelar_devolve_a_mercadoria_ao_saldo(self):
        """
        Cancelar sem devolver deixaria a viagem com menos mercadoria no papel
        do que no caminhão, e o retorno acusaria uma sobra que ninguém explica.
        """
        venda = self._vender('50')

        VendaViagemService.cancelar(venda, motivo='Cliente desistiu')

        self.assertEqual(self._saldo(), Decimal('300.000'))
        venda.refresh_from_db()
        self.assertEqual(venda.status, VendaViagem.Status.CANCELADA)
        self.assertIn('desistiu', venda.observacao)

    def test_venda_cancelada_nao_cancela_de_novo(self):
        venda = self._vender('50')
        VendaViagemService.cancelar(venda)

        with self.assertRaises(DadosInvalidosError):
            VendaViagemService.cancelar(venda)

    # ── O que a tela oferece ─────────────────────────────────────────────

    def test_so_o_que_esta_no_caminhao_e_oferecido(self):
        """
        Vender de um produto que não viajou não é engano de digitação, é
        impossibilidade.
        """
        self._produto('P2', '500')

        disponivel = VendaViagemService.disponivel_para_venda(self.viagem)

        self.assertEqual(len(disponivel), 1)
        self.assertEqual(disponivel[0]['produto'], self.produto)
        self.assertEqual(disponivel[0]['disponivel'], Decimal('300.000'))

    def test_produto_esgotado_sai_da_lista(self):
        self._vender('300')

        self.assertEqual(VendaViagemService.disponivel_para_venda(self.viagem), [])

    def test_o_resumo_soma_as_vendas_e_o_que_resta(self):
        self._vender('50')
        self._vender('80')

        resumo = VendaViagemService.resumo(self.viagem)

        self.assertEqual(resumo['quantidade'], 2)
        self.assertEqual(resumo['valor'], Decimal('1300.00'))
        self.assertEqual(resumo['disponivel'], Decimal('170.000'))

    def test_venda_cancelada_sai_do_resumo(self):
        venda = self._vender('50')
        VendaViagemService.cancelar(venda)

        self.assertEqual(VendaViagemService.resumo(self.viagem)['quantidade'], 0)
