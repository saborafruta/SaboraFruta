"""
A NF-e de retorno do que não foi vendido.

O CICLO QUE FICAVA ABERTO. A remessa dizia que 300 caixas saíram; as vendas
justificavam parte; e o que voltava para a prateleira voltava por
movimentação interna, sem documento nenhum. A diferença entre a remessa e as
vendas é exatamente o que a fiscalização pede para explicar — e a explicação
é esta nota.

O QUE ESTES TESTES CERCAM:

  · UMA NOTA POR VIAGEM, e não por produto: o caminhão volta uma vez;

  · SÓ O QUE VOLTOU entra. Listar o que não voltou declararia entrada de
    mercadoria que não existe;

  · A NOTA É DE ENTRADA, com a empresa nos dois lados — é o sentido que a
    distingue da remessa;

  · NÃO SE EMITE COM SALDO EM ABERTO. Mercadoria ainda sem destino não foi
    contada, e a nota declararia entrada de algo que ninguém viu;

  · O VALOR É O DA SAÍDA. Valor diferente faria remessa e retorno não
    baterem, e a diferença apareceria como resultado inventado;

  · ELA NÃO MEXE NO ESTOQUE: a entrada já aconteceu na conferência física.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.cadastros.models import Cliente, ClienteFilial
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import DadosInvalidosError
from apps.estoque.models import Estoque, MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.financeiro.constants.enums import StatusDocumentoFiscal
from apps.fiscal.models import NaturezaOperacao, RegraNaturezaOperacao
from apps.logistica.models import Viagem
from apps.logistica.services.remessa_nfe import RemessaVendaForaService
from apps.logistica.services.retorno_nfe import RetornoVendaForaService
from apps.logistica.services.venda_viagem import VendaViagemService
from apps.logistica.services.viagem import ViagemService
from apps.produtos.models import Produto, ProdutoFilial
from apps.produtos.models.unidade import UnidadeMedida, UnidadeMedidaFilial

ZERO = Decimal('0')


class RetornoNFeBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Volta LTDA', nome_fantasia='Volta',
            cnpj='31345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='31345678000272',
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
            email='volta@rota.local', nome='Volta', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Mercado da Esquina',
            cpf_cnpj='12345678901', uf='RN', cidade='Natal',
        )
        ClienteFilial.objects.create(cliente=cls.cliente, filial=cls.filial)

        cls.remessa = NaturezaOperacao.objects.create(
            filial=cls.filial, codigo='remessa', descricao='Remessa venda fora',
            especie=NaturezaOperacao.Especie.REMESSA_VENDA_FORA,
            exige_destinatario=False,
        )
        RegraNaturezaOperacao.objects.create(natureza=cls.remessa, cfop='5904')

    def setUp(self):
        self.client.force_login(self.usuario)
        self.natureza_retorno = NaturezaOperacao.objects.create(
            filial=self.filial, codigo='retorno',
            descricao='Retorno de venda fora do estabelecimento',
            especie=NaturezaOperacao.Especie.RETORNO_VENDA_FORA,
            exige_destinatario=False,
        )
        RegraNaturezaOperacao.objects.create(
            natureza=self.natureza_retorno, cfop='1904',
            natureza_operacao_texto='Retorno de mercadoria nao vendida',
        )
        self.produto = self._produto('P1', '1000')
        self.viagem = self._viagem_na_rua()

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

    def _viagem_na_rua(self, quantidade='300', numero=1):
        viagem = Viagem.objects.create(
            filial=self.filial, numero=numero, motorista_nome='Seu Zé',
            veiculo_placa='ABC1D23', vendedor=self.usuario,
            responsavel=self.usuario,
        )
        ViagemService.adicionar_item(viagem, {
            'natureza': self.remessa, 'produto': self.produto,
            'quantidade': quantidade, 'valor_unitario': '10',
        })
        ViagemService.fechar_carga(viagem, usuario=self.usuario)
        viagem.status = Viagem.Status.EM_VENDAS
        viagem.save(update_fields=['status'])
        return viagem

    def _vender(self, quantidade='50'):
        return VendaViagemService.registrar(self.viagem, {
            'produto': self.produto, 'quantidade': quantidade,
            'valor_unitario': '10', 'cliente': self.cliente,
        }, usuario=self.usuario)

    def _retornar(self, quantidade='250'):
        return ViagemService.registrar_retorno(
            self.viagem, self.produto, Decimal(quantidade), usuario=self.usuario,
        )

    def _fechar_a_conta(self):
        """Vende 50 e devolve 250 — a carga de 300 fecha."""
        self._vender('50')
        self._retornar('250')

    def _saldo_estoque(self) -> Decimal:
        return Estoque.objects.get(
            produto=self.produto, filial=self.filial,
        ).quantidade_atual


class ItensTests(RetornoNFeBase):
    """O que entra na nota."""

    def test_so_o_que_voltou_entra(self):
        self._fechar_a_conta()

        itens = RetornoVendaForaService.itens_do_retorno(self.viagem)

        self.assertEqual(len(itens), 1)
        self.assertEqual(itens[0].quantidade_retornada, Decimal('250.000'))

    def test_viagem_sem_retorno_nao_tem_nota(self):
        """
        Listar o que não voltou declararia entrada de mercadoria que não
        existe.
        """
        self._vender('50')

        self.assertEqual(RetornoVendaForaService.itens_do_retorno(self.viagem), [])
        with self.assertRaises(DadosInvalidosError):
            RetornoVendaForaService.emitir(self.viagem, self.usuario)


class ConferenciaTests(RetornoNFeBase):
    """O que impede a emissão."""

    def test_nao_emite_com_saldo_ainda_na_rua(self):
        """
        Mercadoria sem destino não foi contada — a nota declararia entrada de
        algo que ninguém viu.
        """
        self._vender('50')
        self._retornar('100')  # sobram 150 em poder da viagem

        problemas = RetornoVendaForaService.conferir(self.viagem)

        self.assertTrue(any('conferência' in p for p in problemas))
        with self.assertRaises(DadosInvalidosError):
            RetornoVendaForaService.emitir(self.viagem, self.usuario)

    def test_sem_natureza_de_retorno_a_emissao_para(self):
        self.natureza_retorno.delete()
        self._fechar_a_conta()

        with self.assertRaises(DadosInvalidosError) as erro:
            RetornoVendaForaService.emitir(self.viagem, self.usuario)

        self.assertIn('natureza', str(erro.exception).lower())

    def test_duas_naturezas_ativas_sao_recusadas(self):
        outra = NaturezaOperacao.objects.create(
            filial=self.filial, codigo='retorno-2', descricao='Retorno 2',
            especie=NaturezaOperacao.Especie.RETORNO_VENDA_FORA,
            exige_destinatario=False,
        )
        RegraNaturezaOperacao.objects.create(natureza=outra, cfop='2904')
        self._fechar_a_conta()

        with self.assertRaises(DadosInvalidosError):
            RetornoVendaForaService.emitir(self.viagem, self.usuario)

    def test_a_recusa_nao_queima_numero(self):
        from apps.core.models.empresa import Filial

        self._vender('50')
        self._retornar('100')
        antes = Filial.objects.get(pk=self.filial.pk).proximo_numero_nfe

        with self.assertRaises(DadosInvalidosError):
            RetornoVendaForaService.emitir(self.viagem, self.usuario)

        depois = Filial.objects.get(pk=self.filial.pk).proximo_numero_nfe
        self.assertEqual(antes, depois)


class PayloadTests(RetornoNFeBase):
    """O que vai na nota."""

    def test_o_cfop_vem_da_regra_cadastrada(self):
        self._fechar_a_conta()

        payload = RetornoVendaForaService.construir_payload(self.viagem, 1, 1)

        self.assertEqual(payload['items'][0]['cfop'], '1904')

    def test_mudar_a_regra_muda_a_nota(self):
        RegraNaturezaOperacao.objects.filter(natureza=self.natureza_retorno).update(
            cfop='2904',
        )
        self._fechar_a_conta()

        payload = RetornoVendaForaService.construir_payload(self.viagem, 1, 1)

        self.assertEqual(payload['items'][0]['cfop'], '2904')

    def test_a_nota_e_de_entrada(self):
        """É o sentido que a distingue da remessa."""
        self._fechar_a_conta()

        payload = RetornoVendaForaService.construir_payload(self.viagem, 1, 1)

        self.assertEqual(payload['tipo_documento'], '0')

    def test_a_empresa_esta_nos_dois_lados(self):
        self._fechar_a_conta()

        payload = RetornoVendaForaService.construir_payload(self.viagem, 1, 1)

        self.assertEqual(
            payload['cnpj_destinatario'], payload['cnpj_emitente'],
        )

    def test_a_quantidade_e_a_que_voltou(self):
        self._fechar_a_conta()

        payload = RetornoVendaForaService.construir_payload(self.viagem, 1, 1)

        self.assertEqual(payload['items'][0]['quantidade_comercial'], 250.0)

    def test_o_valor_e_o_da_saida(self):
        """
        Valor diferente faria remessa e retorno não baterem, e a diferença
        apareceria como resultado inventado.
        """
        self._fechar_a_conta()

        payload = RetornoVendaForaService.construir_payload(self.viagem, 1, 1)

        self.assertEqual(payload['items'][0]['valor_unitario_comercial'], 10.0)
        self.assertEqual(payload['valor_total'], 2500.0)

    def test_a_nota_aponta_para_a_remessa(self):
        """
        Quem confere precisa ligar as duas sem procurar: é a diferença entre
        elas que a fiscalização pede para justificar.
        """
        remessa = RemessaVendaForaService.emitir(self.viagem, self.usuario)
        remessa.chave = '1' * 44
        remessa.save(update_fields=['chave'])
        self._fechar_a_conta()

        payload = RetornoVendaForaService.construir_payload(self.viagem, 2, 1)

        self.assertEqual(
            payload['notas_referenciadas'], [{'chave_nfe': '1' * 44}],
        )
        self.assertIn(
            f'Remessa {remessa.numero}/{remessa.serie}',
            payload['informacoes_adicionais_contribuinte'],
        )

    def test_sem_remessa_transmitida_a_nota_sai_do_mesmo_jeito(self):
        """
        A remessa pode ainda não ter chave (não transmitida). Travar o
        retorno por isso deixaria a mercadoria de volta sem documento por
        causa de um dado que chega depois.
        """
        self._fechar_a_conta()

        payload = RetornoVendaForaService.construir_payload(self.viagem, 1, 1)

        self.assertNotIn('notas_referenciadas', payload)


class EmissaoTests(RetornoNFeBase):
    """A nota gravada."""

    def test_a_nota_nasce_pendente_e_ligada_a_viagem(self):
        self._fechar_a_conta()

        documento = RetornoVendaForaService.emitir(self.viagem, self.usuario)

        self.assertEqual(documento.status, StatusDocumentoFiscal.PENDENTE)
        self.assertEqual(documento.origem_tipo, 'viagem_retorno')
        self.assertEqual(documento.origem_id, self.viagem.pk)
        self.assertEqual(documento.tipo_operacao, '0')
        self.assertEqual(documento.valor_total, Decimal('2500.00'))

    def test_uma_nota_por_viagem(self):
        """O caminhão volta uma vez."""
        self._fechar_a_conta()
        RetornoVendaForaService.emitir(self.viagem, self.usuario)

        with self.assertRaises(DadosInvalidosError):
            RetornoVendaForaService.emitir(self.viagem, self.usuario)

    def test_nota_cancelada_libera_nova_emissao(self):
        self._fechar_a_conta()
        primeira = RetornoVendaForaService.emitir(self.viagem, self.usuario)
        primeira.status = StatusDocumentoFiscal.CANCELADA
        primeira.save(update_fields=['status'])

        segunda = RetornoVendaForaService.emitir(self.viagem, self.usuario)

        self.assertNotEqual(primeira.pk, segunda.pk)

    def test_a_nota_nao_mexe_no_estoque(self):
        """A entrada já aconteceu na conferência física do retorno."""
        self._fechar_a_conta()
        antes = self._saldo_estoque()

        RetornoVendaForaService.emitir(self.viagem, self.usuario)

        self.assertEqual(self._saldo_estoque(), antes)


class TelaTests(RetornoNFeBase):
    """O botão vive na prestação de contas."""

    def _detalhe(self):
        return self.client.get(
            reverse('logistica:viagem-detail', args=[self.viagem.pk]),
        ).content.decode()

    def test_com_retorno_conferido_a_tela_oferece_a_nota(self):
        self._fechar_a_conta()

        html = self._detalhe()

        self.assertIn('Emitir NF-e de retorno', html)

    def test_com_saldo_em_aberto_a_tela_diz_o_que_falta(self):
        self._vender('50')
        self._retornar('100')

        html = self._detalhe()

        self.assertNotIn('Emitir NF-e de retorno', html)
        self.assertIn('conferência do retorno', html)

    def test_emitir_pela_tela(self):
        self._fechar_a_conta()

        self.client.post(
            reverse('logistica:viagem-emitir-retorno', args=[self.viagem.pk]),
        )

        self.assertIsNotNone(RetornoVendaForaService.nota_da_viagem(self.viagem))

    def test_emitida_a_tela_mostra_o_numero(self):
        self._fechar_a_conta()
        documento = RetornoVendaForaService.emitir(self.viagem, self.usuario)

        html = self._detalhe()

        self.assertIn(f'{documento.numero}/{documento.serie}', html)
