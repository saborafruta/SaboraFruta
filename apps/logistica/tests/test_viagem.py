"""
O ciclo da viagem: montar, fechar, vender na rua, voltar e prestar contas.

O que este módulo tem de próprio não é a carga — é o que sai SEM comprador. A
mercadoria deixa o estabelecimento mas continua sendo da empresa, e a conta
remetido = vendido + bonificado + retornado + baixado precisa fechar.
"""
from decimal import Decimal

from django.test import TestCase

from apps.cadastros.models import Cliente, ClienteFilial
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import DadosInvalidosError
from apps.estoque.models import Estoque, MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.fiscal.models import NaturezaOperacao, RegraNaturezaOperacao
from apps.logistica.models import SaldoCarga, Viagem
from apps.logistica.services.viagem import ViagemService
from apps.produtos.models import Produto, ProdutoFilial
from apps.produtos.models.unidade import UnidadeMedida, UnidadeMedidaFilial


class ViagemBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Viagem LTDA', nome_fantasia='Viagem',
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
            email='via@carga.local', nome='Via', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Mercado Central',
            cpf_cnpj='12345678901', uf='RN',
        )
        ClienteFilial.objects.create(cliente=cls.cliente, filial=cls.filial)

        # As naturezas que este vertical usa, com as regras que a contabilidade
        # cadastraria.
        cls.venda = cls._natureza('venda', NaturezaOperacao.Especie.VENDA, '5102')
        cls.remessa = cls._natureza(
            'remessa_venda_fora', NaturezaOperacao.Especie.REMESSA_VENDA_FORA, '5904',
            exige_destinatario=False, gera_financeiro=False,
        )
        cls.bonificacao = cls._natureza(
            'bonificacao', NaturezaOperacao.Especie.BONIFICACAO, '5910',
            gera_financeiro=False,
        )

    @classmethod
    def _natureza(cls, codigo, especie, cfop, **extras):
        natureza = NaturezaOperacao.objects.create(
            filial=cls.filial, codigo=codigo, descricao=codigo.replace('_', ' ').title(),
            especie=especie, **extras,
        )
        RegraNaturezaOperacao.objects.create(natureza=natureza, cfop=cfop)
        return natureza

    def _produto(self, codigo='P1', saldo='100'):
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

    def _viagem(self, **dados):
        base = {'motorista_nome': 'Seu Zé', 'veiculo_placa': 'abc1d23'}
        base.update(dados)
        return ViagemService.criar(self.filial, base, usuario=self.usuario)

    def _saldo(self, produto):
        return Estoque.objects.get(produto=produto, filial=self.filial).quantidade_atual


class MontarAViagemTests(ViagemBase):

    def test_o_numero_e_gerado_e_nao_pedido(self):
        primeira = self._viagem()
        segunda = self._viagem()

        self.assertEqual(primeira.numero, 1)
        self.assertEqual(segunda.numero, 2)

    def test_a_placa_vai_em_maiuscula(self):
        viagem = self._viagem()

        self.assertEqual(viagem.veiculo_placa, 'ABC1D23')

    def test_a_carga_leva_as_tres_naturezas_juntas(self):
        """
        É o ponto do módulo: um caminhão só, três operações fiscalmente
        separadas.
        """
        viagem = self._viagem(vendedor=self.usuario)
        produto = self._produto()

        ViagemService.adicionar_item(viagem, {
            'natureza': self.venda, 'produto': produto, 'quantidade': '10',
            'cliente': self.cliente, 'valor_unitario': '10',
        })
        ViagemService.adicionar_item(viagem, {
            'natureza': self.remessa, 'produto': produto, 'quantidade': '20',
        })
        ViagemService.adicionar_item(viagem, {
            'natureza': self.bonificacao, 'produto': produto, 'quantidade': '5',
            'cliente': self.cliente,
        })

        especies = {i.natureza.especie for i in viagem.itens.all()}
        self.assertEqual(len(especies), 3)

    def test_operacao_que_exige_destinatario_nao_entra_sem_cliente(self):
        """
        Deixar passar produz nota sem destinatário na hora de transmitir,
        quando já não dá para voltar atrás.
        """
        viagem = self._viagem()
        produto = self._produto()

        with self.assertRaises(Exception):
            ViagemService.adicionar_item(viagem, {
                'natureza': self.bonificacao, 'produto': produto, 'quantidade': '5',
            })

    def test_a_remessa_entra_sem_cliente(self):
        """A mercadoria sai justamente porque ainda não tem comprador."""
        viagem = self._viagem(vendedor=self.usuario)
        produto = self._produto()

        item = ViagemService.adicionar_item(viagem, {
            'natureza': self.remessa, 'produto': produto, 'quantidade': '20',
        })

        self.assertIsNone(item.cliente_id)


class FecharACargaTests(ViagemBase):

    def _carga_de_remessa(self, quantidade='20', saldo='100'):
        viagem = self._viagem(vendedor=self.usuario)
        produto = self._produto(saldo=saldo)
        ViagemService.adicionar_item(viagem, {
            'natureza': self.remessa, 'produto': produto,
            'quantidade': quantidade, 'valor_unitario': '4',
        })
        return viagem, produto

    def test_fechar_baixa_o_estoque_da_filial(self):
        """
        A mercadoria deixa o estabelecimento de verdade, amparada pela remessa.
        """
        viagem, produto = self._carga_de_remessa()

        ViagemService.fechar_carga(viagem, usuario=self.usuario)

        self.assertEqual(self._saldo(produto), Decimal('80.000'))

    def test_fechar_abre_o_saldo_em_poder_da_viagem(self):
        """
        Sem este livro, durante a rota o sistema não sabe onde a mercadoria
        está, e o que volta entra como devolução solta.
        """
        viagem, produto = self._carga_de_remessa()

        ViagemService.fechar_carga(viagem, usuario=self.usuario)

        saldo = SaldoCarga.objects.get(viagem=viagem, produto=produto)
        self.assertEqual(saldo.quantidade_remetida, Decimal('20.000'))
        self.assertEqual(saldo.quantidade_em_poder, Decimal('20.000'))

    def test_venda_ja_fechada_nao_abre_saldo_de_carga(self):
        """Ela tem dono: sai vendida, não fica em poder de ninguém."""
        viagem = self._viagem()
        produto = self._produto()
        ViagemService.adicionar_item(viagem, {
            'natureza': self.venda, 'produto': produto, 'quantidade': '10',
            'cliente': self.cliente, 'valor_unitario': '10',
        })

        ViagemService.fechar_carga(viagem, usuario=self.usuario)

        self.assertFalse(SaldoCarga.objects.filter(viagem=viagem).exists())
        self.assertEqual(self._saldo(produto), Decimal('90.000'))

    def test_bonificacao_baixa_pelo_tipo_de_bonificacao(self):
        """
        Bonificação não é venda: o estoque precisa saber disso para o custo e
        para o relatório de saída sem receita.
        """
        viagem = self._viagem()
        produto = self._produto()
        ViagemService.adicionar_item(viagem, {
            'natureza': self.bonificacao, 'produto': produto,
            'quantidade': '5', 'cliente': self.cliente,
        })

        ViagemService.fechar_carga(viagem, usuario=self.usuario)

        movimentacao = MovimentacaoEstoque.objects.filter(
            produto=produto, documento_tipo='viagem',
        ).latest('id')
        self.assertEqual(
            movimentacao.tipo_operacao,
            MovimentacaoEstoque.TipoOperacao.BONIFICACAO,
        )

    def test_carga_vazia_nao_fecha(self):
        viagem = self._viagem()

        with self.assertRaises(DadosInvalidosError) as erro:
            ViagemService.fechar_carga(viagem, usuario=self.usuario)

        self.assertIn('vazia', str(erro.exception))

    def test_mercadoria_sem_comprador_exige_quem_responde_por_ela(self):
        """Sem vendedor, o saldo da rua não tem a quem prestar contas."""
        viagem = self._viagem()
        produto = self._produto()
        ViagemService.adicionar_item(viagem, {
            'natureza': self.remessa, 'produto': produto, 'quantidade': '20',
        })

        with self.assertRaises(DadosInvalidosError) as erro:
            ViagemService.fechar_carga(viagem, usuario=self.usuario)

        self.assertIn('responde', str(erro.exception))

    def test_sem_regra_fiscal_a_carga_nao_fecha(self):
        """
        Descobrir que falta CFOP com a nota meio emitida é o pior momento
        possível — a conferência puxa isso para antes da saída.
        """
        sem_regra = NaturezaOperacao.objects.create(
            filial=self.filial, codigo='sem_regra', descricao='Sem regra',
            especie=NaturezaOperacao.Especie.VENDA,
        )
        viagem = self._viagem()
        produto = self._produto()
        ViagemService.adicionar_item(viagem, {
            'natureza': sem_regra, 'produto': produto, 'quantidade': '1',
            'cliente': self.cliente,
        })

        with self.assertRaises(DadosInvalidosError) as erro:
            ViagemService.fechar_carga(viagem, usuario=self.usuario)

        self.assertIn('Sem regra fiscal', str(erro.exception))

    def test_a_conferencia_mostra_todas_as_pendencias_de_uma_vez(self):
        """
        Quem está com o caminhão encostado não pode descobrir as pendências uma
        por vez, a cada tentativa.
        """
        viagem = self._viagem(motorista_nome='', veiculo_placa='')
        produto = self._produto()
        ViagemService.adicionar_item(viagem, {
            'natureza': self.remessa, 'produto': produto, 'quantidade': '20',
        })

        problemas = ViagemService.conferir_antes_de_fechar(viagem)

        self.assertGreaterEqual(len(problemas), 2)

    def test_depois_de_sair_a_carga_nao_muda(self):
        """Alterar agora reescreveria o que o documento fiscal já declarou."""
        viagem, produto = self._carga_de_remessa()
        ViagemService.fechar_carga(viagem, usuario=self.usuario)

        with self.assertRaises(DadosInvalidosError) as erro:
            ViagemService.adicionar_item(viagem, {
                'natureza': self.remessa, 'produto': produto, 'quantidade': '1',
            })

        self.assertIn('já saiu', str(erro.exception))


class PrestacaoDeContasTests(ViagemBase):

    def setUp(self):
        self.viagem = self._viagem(vendedor=self.usuario)
        self.produto = self._produto(saldo='100')
        ViagemService.adicionar_item(self.viagem, {
            'natureza': self.remessa, 'produto': self.produto,
            'quantidade': '20', 'valor_unitario': '4',
        })
        ViagemService.fechar_carga(self.viagem, usuario=self.usuario)

    def test_a_venda_na_rua_baixa_do_saldo_e_nao_do_estoque(self):
        """
        Essa mercadoria já saiu da filial quando a carga fechou. Baixar de novo
        contaria a mesma saída duas vezes.
        """
        antes = self._saldo(self.produto)

        ViagemService.registrar_saida_do_saldo(
            self.viagem, self.produto, Decimal('12'), 'quantidade_vendida',
        )

        saldo = SaldoCarga.objects.get(viagem=self.viagem, produto=self.produto)
        self.assertEqual(saldo.quantidade_vendida, Decimal('12.000'))
        self.assertEqual(saldo.quantidade_em_poder, Decimal('8.000'))
        self.assertEqual(self._saldo(self.produto), antes)

    def test_o_retorno_devolve_a_mercadoria_ao_estoque(self):
        antes = self._saldo(self.produto)

        ViagemService.registrar_retorno(
            self.viagem, self.produto, Decimal('8'), usuario=self.usuario,
        )

        self.assertEqual(self._saldo(self.produto), antes + Decimal('8'))

    def test_nao_da_para_baixar_mais_do_que_saiu(self):
        """
        Saldo negativo em poder de terceiro é mercadoria inventada — e o
        documento de remessa diz exatamente quanto saiu.
        """
        with self.assertRaises(DadosInvalidosError) as erro:
            ViagemService.registrar_saida_do_saldo(
                self.viagem, self.produto, Decimal('50'), 'quantidade_vendida',
            )

        self.assertIn('só há', str(erro.exception))

    def test_produto_que_nao_saiu_na_viagem_nao_tem_saldo(self):
        outro = self._produto('P2')

        with self.assertRaises(DadosInvalidosError) as erro:
            ViagemService.registrar_saida_do_saldo(
                self.viagem, outro, Decimal('1'), 'quantidade_vendida',
            )

        self.assertIn('não saiu nesta viagem', str(erro.exception))

    def test_a_viagem_nao_encerra_com_saldo_em_aberto(self):
        """
        Encerrar com sobra é perder o rastro de mercadoria da empresa que está
        na rua — exatamente o que a fiscalização pede para ver.
        """
        ViagemService.registrar_saida_do_saldo(
            self.viagem, self.produto, Decimal('12'), 'quantidade_vendida',
        )

        with self.assertRaises(DadosInvalidosError) as erro:
            ViagemService.encerrar(self.viagem)

        self.assertIn('8', str(erro.exception))

    def test_com_a_conta_fechada_a_viagem_encerra(self):
        ViagemService.registrar_saida_do_saldo(
            self.viagem, self.produto, Decimal('12'), 'quantidade_vendida',
        )
        ViagemService.registrar_saida_do_saldo(
            self.viagem, self.produto, Decimal('3'), 'quantidade_bonificada',
        )
        ViagemService.registrar_retorno(
            self.viagem, self.produto, Decimal('4'), usuario=self.usuario,
        )
        ViagemService.registrar_saida_do_saldo(
            self.viagem, self.produto, Decimal('1'), 'quantidade_baixada',
        )

        ViagemService.encerrar(self.viagem)

        self.viagem.refresh_from_db()
        self.assertEqual(self.viagem.status, Viagem.Status.ENCERRADA)

    def test_a_conciliacao_mostra_a_conta_linha_a_linha(self):
        ViagemService.registrar_saida_do_saldo(
            self.viagem, self.produto, Decimal('12'), 'quantidade_vendida',
        )

        linha = ViagemService.conciliacao(self.viagem)[0]

        self.assertEqual(linha['remetida'], Decimal('20.000'))
        self.assertEqual(linha['vendida'], Decimal('12.000'))
        self.assertEqual(linha['em_poder'], Decimal('8.000'))
        self.assertFalse(linha['fechado'])
