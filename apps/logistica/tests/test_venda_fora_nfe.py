"""
A NF-e da venda feita na rua.

O ELO QUE FALTAVA. A remessa amparava a mercadoria no caminhão e a venda já
baixava o saldo da viagem — mas o cliente que comprava no meio da rota ficava
SEM DOCUMENTO. É esta nota que fecha a operação.

O QUE ESTES TESTES CERCAM:

  · NADA DE FISCAL É DECIDIDO NO CÓDIGO. CFOP, CST/CSOSN e alíquotas saem da
    natureza cadastrada para a espécie `venda_fora`. Trocar a regra na
    tabela muda a nota, sem tocar em uma linha de código;

  · A NATUREZA É ÚNICA E ATIVA. Duas naturezas da mesma espécie fariam o
    CFOP depender de qual delas o código pegasse primeiro;

  · CONFERE ANTES DE RESERVAR NÚMERO. Número reservado e não usado vira
    buraco na numeração, que a SEFAZ cobra depois com inutilização;

  · SEM CPF OU CNPJ NÃO HÁ NF-e. Na rua o vendedor anota "padaria da
    esquina" e segue — a nota precisa recusar antes, não na transmissão;

  · A NOTA NÃO MEXE NO ESTOQUE. A mercadoria saiu na remessa e o saldo da
    viagem já baixou na venda: baixar aqui seria a terceira saída da mesma
    caixa;

  · O LOTE VAI NA NOTA — é ele que o recall persegue.
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
from apps.financeiro.models.fiscal import DocumentoFiscal
from apps.fiscal.models import NaturezaOperacao, RegraNaturezaOperacao
from apps.logistica.models import VendaViagem, Viagem
from apps.logistica.services.venda_fora_nfe import VendaForaNFeService
from apps.logistica.services.venda_viagem import VendaViagemService
from apps.logistica.services.viagem import ViagemService
from apps.produtos.models import Produto, ProdutoFilial
from apps.produtos.models.unidade import UnidadeMedida, UnidadeMedidaFilial


class VendaForaNFeBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Rota LTDA', nome_fantasia='Rota',
            cnpj='21345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='21345678000272',
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
            email='rua@rota.local', nome='Rua', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Mercado da Esquina',
            cpf_cnpj='12345678901', uf='RN', cidade='Natal',
            endereco='Rua B', numero='50', bairro='Centro', cep='59000000',
        )
        ClienteFilial.objects.create(cliente=cls.cliente, filial=cls.filial)

        cls.remessa = NaturezaOperacao.objects.create(
            filial=cls.filial, codigo='remessa', descricao='Remessa venda fora',
            especie=NaturezaOperacao.Especie.REMESSA_VENDA_FORA,
            exige_destinatario=False,
        )
        RegraNaturezaOperacao.objects.create(natureza=cls.remessa, cfop='5904')

    def setUp(self):
        self.venda_fora = NaturezaOperacao.objects.create(
            filial=self.filial, codigo='venda-fora',
            descricao='Venda fora do estabelecimento',
            especie=NaturezaOperacao.Especie.VENDA_FORA,
        )
        RegraNaturezaOperacao.objects.create(
            natureza=self.venda_fora, cfop='5103',
            natureza_operacao_texto='Venda de producao do estabelecimento',
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

    def _vender(self, quantidade='50', **extras):
        dados = {
            'produto': self.produto, 'quantidade': quantidade,
            'valor_unitario': '10', 'cliente': self.cliente,
        }
        dados.update(extras)
        return VendaViagemService.registrar(
            self.viagem, dados, usuario=self.usuario,
        )

    def _saldo_estoque(self) -> Decimal:
        return Estoque.objects.get(
            produto=self.produto, filial=self.filial,
        ).quantidade_atual


class NaturezaTests(VendaForaNFeBase):
    """De onde vem a regra fiscal."""

    def test_a_natureza_da_especie_e_encontrada(self):
        self.assertEqual(
            VendaForaNFeService.natureza(self.filial), self.venda_fora,
        )

    def test_sem_natureza_cadastrada_a_emissao_para_e_diz_o_que_falta(self):
        """
        Escolher uma natureza de venda comum "porque parece igual" daria uma
        nota com CFOP errado, e o erro só apareceria na apuração.
        """
        self.venda_fora.delete()
        venda = self._vender()

        with self.assertRaises(DadosInvalidosError) as erro:
            VendaForaNFeService.emitir(venda, self.usuario)

        self.assertIn('natureza', str(erro.exception).lower())

    def test_duas_naturezas_ativas_sao_recusadas(self):
        """O CFOP passaria a depender de qual delas o código pegasse antes."""
        outra = NaturezaOperacao.objects.create(
            filial=self.filial, codigo='venda-fora-2',
            descricao='Venda fora 2',
            especie=NaturezaOperacao.Especie.VENDA_FORA,
        )
        RegraNaturezaOperacao.objects.create(natureza=outra, cfop='5104')
        venda = self._vender()

        with self.assertRaises(DadosInvalidosError):
            VendaForaNFeService.emitir(venda, self.usuario)

    def test_natureza_inativa_nao_vale(self):
        self.venda_fora.ativo = False
        self.venda_fora.save(update_fields=['ativo'])
        venda = self._vender()

        with self.assertRaises(DadosInvalidosError):
            VendaForaNFeService.emitir(venda, self.usuario)


class PayloadTests(VendaForaNFeBase):
    """O que vai na nota."""

    def test_o_cfop_vem_da_regra_cadastrada(self):
        venda = self._vender()

        payload = VendaForaNFeService.construir_payload(venda, 1, 1)

        self.assertEqual(payload['items'][0]['cfop'], '5103')

    def test_mudar_a_regra_muda_a_nota_sem_tocar_no_codigo(self):
        """É a exigência central: nada de fiscal fixado em código."""
        RegraNaturezaOperacao.objects.filter(natureza=self.venda_fora).update(
            cfop='6103',
        )
        venda = self._vender()

        payload = VendaForaNFeService.construir_payload(venda, 1, 1)

        self.assertEqual(payload['items'][0]['cfop'], '6103')

    def test_o_comprador_vai_como_destinatario(self):
        venda = self._vender()

        payload = VendaForaNFeService.construir_payload(venda, 1, 1)

        self.assertEqual(payload['cpf_destinatario'], '12345678901')
        self.assertEqual(payload['nome_destinatario'], 'Mercado da Esquina')
        self.assertEqual(payload['municipio_destinatario'], 'Natal')

    def test_a_venda_e_presencial_fora_do_estabelecimento(self):
        """A SEFAZ cruza presença com CFOP — o código 5 não é estilo."""
        venda = self._vender()

        payload = VendaForaNFeService.construir_payload(venda, 1, 1)

        self.assertEqual(payload['presenca_comprador'], '5')

    def test_o_total_sai_dos_itens(self):
        venda = self._vender('50')

        payload = VendaForaNFeService.construir_payload(venda, 1, 1)

        self.assertEqual(payload['valor_total'], 500.0)

    def test_a_nota_diz_de_qual_viagem_veio(self):
        """
        Na barreira, quem lê precisa entender que a mercadoria saiu antes,
        com outra nota, na mesma viagem.
        """
        venda = self._vender()

        payload = VendaForaNFeService.construir_payload(venda, 1, 1)

        self.assertIn('Viagem 000001', payload['informacoes_adicionais_contribuinte'])


class ConferenciaTests(VendaForaNFeBase):
    """O que impede a emissão."""

    def test_sem_documento_do_comprador_nao_ha_nota(self):
        venda = self._vender()
        venda.cliente = None
        venda.cliente_documento = ''
        venda.save(update_fields=['cliente', 'cliente_documento'])

        problemas = VendaForaNFeService.conferir(venda)

        self.assertTrue(any('CPF' in p for p in problemas))

    def test_venda_cancelada_nao_emite(self):
        venda = self._vender()
        VendaViagemService.cancelar(venda, 'cliente desistiu')

        with self.assertRaises(DadosInvalidosError):
            VendaForaNFeService.emitir(venda, self.usuario)

    def test_a_recusa_nao_queima_numero(self):
        """
        Número reservado e não usado vira buraco na numeração — mais
        trabalho do que recusar agora.
        """
        from apps.core.models.empresa import Filial

        venda = self._vender()
        venda.cliente_documento = ''
        venda.save(update_fields=['cliente_documento'])
        antes = Filial.objects.get(pk=self.filial.pk).proximo_numero_nfe

        with self.assertRaises(DadosInvalidosError):
            VendaForaNFeService.emitir(venda, self.usuario)

        depois = Filial.objects.get(pk=self.filial.pk).proximo_numero_nfe
        self.assertEqual(antes, depois)


class EmissaoTests(VendaForaNFeBase):
    """A nota gravada."""

    def test_a_nota_nasce_pendente_e_amarrada_a_venda(self):
        venda = self._vender('50')

        documento = VendaForaNFeService.emitir(venda, self.usuario)

        venda.refresh_from_db()
        self.assertEqual(venda.documento_fiscal, documento)
        self.assertEqual(documento.status, StatusDocumentoFiscal.PENDENTE)
        self.assertEqual(documento.origem_tipo, 'viagem_venda_fora')
        self.assertEqual(documento.origem_id, venda.pk)
        self.assertEqual(documento.valor_total, Decimal('500.00'))

    def test_o_cliente_fica_congelado_na_nota(self):
        """A nota precisa continuar explicável depois que o cadastro mudar."""
        venda = self._vender()
        documento = VendaForaNFeService.emitir(venda, self.usuario)

        self.cliente.razao_social = 'Outro nome'
        self.cliente.save(update_fields=['razao_social'])
        documento.refresh_from_db()

        self.assertEqual(documento.destinatario_snapshot['nome'], 'Mercado da Esquina')

    def test_nao_emite_duas_notas_para_a_mesma_venda(self):
        venda = self._vender()
        VendaForaNFeService.emitir(venda, self.usuario)

        with self.assertRaises(DadosInvalidosError):
            VendaForaNFeService.emitir(venda, self.usuario)

    def test_nota_cancelada_libera_nova_emissao(self):
        venda = self._vender()
        primeira = VendaForaNFeService.emitir(venda, self.usuario)
        primeira.status = StatusDocumentoFiscal.CANCELADA
        primeira.save(update_fields=['status'])

        segunda = VendaForaNFeService.emitir(venda, self.usuario)

        self.assertNotEqual(primeira.pk, segunda.pk)

    def test_a_nota_nao_mexe_no_estoque(self):
        """
        A mercadoria saiu na remessa e o saldo da viagem baixou na venda:
        baixar aqui seria a terceira saída da mesma caixa.
        """
        venda = self._vender('50')
        antes = self._saldo_estoque()

        VendaForaNFeService.emitir(venda, self.usuario)

        self.assertEqual(self._saldo_estoque(), antes)

    def test_o_numero_avanca_uma_vez_por_nota(self):
        from apps.core.models.empresa import Filial

        antes = Filial.objects.get(pk=self.filial.pk).proximo_numero_nfe
        VendaForaNFeService.emitir(self._vender('10'), self.usuario)
        VendaForaNFeService.emitir(self._vender('10'), self.usuario)

        depois = Filial.objects.get(pk=self.filial.pk).proximo_numero_nfe
        self.assertEqual(depois - antes, 2)
        self.assertEqual(
            DocumentoFiscal.objects.filter(origem_tipo='viagem_venda_fora').count(), 2,
        )


class TelaTests(VendaForaNFeBase):
    """A nota é emitida de onde a venda aconteceu."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.usuario)
        self.url = reverse('logistica:viagem-detail', args=[self.viagem.pk])

    def test_a_venda_da_rua_aparece_na_viagem(self):
        """
        O contexto já trazia as vendas e a tela não as mostrava: quem
        registrou uma venda não tinha como revê-la.
        """
        venda = self._vender('50')

        html = self.client.get(self.url).content.decode()

        self.assertIn('Entregas na rota', html)
        self.assertIn('Mercado da Esquina', html)
        self.assertIn(f'#{venda.numero}', html)

    def test_a_venda_sem_nota_oferece_o_botao(self):
        self._vender('50')

        html = self.client.get(self.url).content.decode()

        self.assertIn('Emitir NF-e', html)

    def test_emitir_pela_tela(self):
        venda = self._vender('50')

        self.client.post(
            reverse('logistica:viagem-venda-nfe', args=[self.viagem.pk, venda.pk]),
        )

        venda.refresh_from_db()
        self.assertIsNotNone(venda.documento_fiscal)

    def test_emitida_a_tela_mostra_o_numero_no_lugar_do_botao(self):
        venda = self._vender('50')
        documento = VendaForaNFeService.emitir(venda, self.usuario)

        html = self.client.get(self.url).content.decode()

        self.assertIn(f'{documento.numero}/{documento.serie}', html)

    def test_a_recusa_volta_como_mensagem_e_nao_como_erro(self):
        venda = self._vender('50')
        venda.cliente = None
        venda.cliente_documento = ''
        venda.save(update_fields=['cliente', 'cliente_documento'])

        resposta = self.client.post(
            reverse('logistica:viagem-venda-nfe', args=[self.viagem.pk, venda.pk]),
            follow=True,
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertIn('CPF', resposta.content.decode())
