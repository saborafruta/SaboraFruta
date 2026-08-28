"""
O MDF-e único da viagem: um caminhão, várias naturezas, um manifesto.

A mesma viagem leva mercadoria vendida, mercadoria sem comprador e cortesia
— cada uma com sua nota, seu CFOP e seu destinatário. Fisicamente é uma
carga só, e é isso que o manifesto descreve.

O QUE ESTES TESTES CERCAM:

  · O MANIFESTO NÃO MUDA A NATUREZA DE NADA. Venda continua venda, remessa
    continua remessa, bonificação continua bonificação — vinculá-las ao
    mesmo MDF-e não as transforma numa operação só;

  · A CONSOLIDAÇÃO É ESCOLHIDA, e não automática: "quando permitido pela
    legislação" depende de UF, regime e da contabilidade, não de código;

  · O QUE IMPEDE APARECE ESCRITO. Nota sem chave, não autorizada ou já
    vinculada a outro manifesto continua na lista com o motivo — sumir com
    a linha faria a pessoa procurar a nota que ela sabe que existe;

  · DESVINCULAR EXISTE porque errar é normal, e para depois de autorizado,
    quando mudar o que o manifesto ampara exige a SEFAZ.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.models import Cliente, ClienteFilial
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import DadosInvalidosError
from apps.estoque.models import MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.financeiro.constants.enums import (
    StatusDocumentoFiscal, TipoDocumentoFiscal,
)
from apps.financeiro.models.fiscal import DocumentoFiscal
from apps.fiscal.models import NaturezaOperacao, RegraNaturezaOperacao
from apps.logistica.models import DocumentoMDFe, MDFe, VendaViagem, Viagem
from apps.logistica.services.mdfe_viagem import MDFeViagemService
from apps.logistica.services.venda_fora_nfe import VendaForaNFeService
from apps.logistica.services.venda_viagem import VendaViagemService
from apps.logistica.services.viagem import ViagemService
from apps.produtos.models import Produto, ProdutoFilial
from apps.produtos.models.unidade import UnidadeMedida, UnidadeMedidaFilial

ZERO = Decimal('0')
T = VendaViagem.Tipo


class MDFeViagemBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Manifesto LTDA', nome_fantasia='Manifesto',
            cnpj='91345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='91345678000272',
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
            email='doca@manifesto.local', nome='Doca', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Cliente A',
            cpf_cnpj='12345678901', uf='RN', cidade='Natal',
        )
        ClienteFilial.objects.create(cliente=cls.cliente, filial=cls.filial)

        cls.remessa = NaturezaOperacao.objects.create(
            filial=cls.filial, codigo='remessa', descricao='Remessa',
            especie=NaturezaOperacao.Especie.REMESSA_VENDA_FORA,
            exige_destinatario=False,
        )
        RegraNaturezaOperacao.objects.create(natureza=cls.remessa, cfop='5904')
        cls.venda_fora = NaturezaOperacao.objects.create(
            filial=cls.filial, codigo='venda-fora', descricao='Venda fora',
            especie=NaturezaOperacao.Especie.VENDA_FORA,
        )
        RegraNaturezaOperacao.objects.create(natureza=cls.venda_fora, cfop='5103')
        cls.bonificacao = NaturezaOperacao.objects.create(
            filial=cls.filial, codigo='bonif', descricao='Bonificação',
            especie=NaturezaOperacao.Especie.BONIFICACAO,
        )
        RegraNaturezaOperacao.objects.create(natureza=cls.bonificacao, cfop='5910')

    def setUp(self):
        self.client.force_login(self.usuario)
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

    def _viagem_na_rua(self):
        viagem = Viagem.objects.create(
            filial=self.filial, numero=Viagem.objects.count() + 1,
            motorista_nome='Seu Zé', veiculo_placa='ABC1D23',
            vendedor=self.usuario, responsavel=self.usuario,
        )
        ViagemService.adicionar_item(viagem, {
            'natureza': self.remessa, 'produto': self.produto,
            'quantidade': '300', 'valor_unitario': '10',
        })
        ViagemService.fechar_carga(viagem, usuario=self.usuario)
        viagem.status = Viagem.Status.EM_VENDAS
        viagem.save(update_fields=['status'])
        return viagem

    def _entrega(self, tipo=T.VENDA, quantidade='50'):
        dados = {
            'tipo': tipo, 'produto': self.produto, 'quantidade': quantidade,
            'valor_unitario': '10', 'cliente': self.cliente,
        }
        if tipo == T.BONIFICACAO:
            dados['motivo'] = VendaViagem.Motivo.BRINDE
        return VendaViagemService.registrar(
            self.viagem, dados, usuario=self.usuario,
        )

    def _autorizar(self, documento, chave=None):
        """Uma nota só entra no manifesto depois de autorizada e com chave."""
        documento.chave = chave or (str(documento.pk).zfill(2) + '1' * 42)
        documento.status = StatusDocumentoFiscal.AUTORIZADA
        documento.save(update_fields=['chave', 'status'])
        return documento

    def _nota_de_venda(self, autorizar=True):
        venda = self._entrega(T.VENDA)
        documento = VendaForaNFeService.emitir(venda, self.usuario)
        return self._autorizar(documento) if autorizar else documento

    def _nota_de_bonificacao(self):
        bonificacao = self._entrega(T.BONIFICACAO, '20')
        return self._autorizar(
            VendaForaNFeService.emitir(bonificacao, self.usuario),
        )

    def _nota_de_remessa(self):
        from apps.logistica.services.remessa_nfe import RemessaVendaForaService

        return self._autorizar(
            RemessaVendaForaService.emitir(self.viagem, self.usuario),
        )

    def _mdfe(self, status=MDFe.Status.RASCUNHO):
        return MDFe.objects.create(
            filial=self.filial, numero=MDFe.objects.count() + 1, serie='1',
            viagem=self.viagem, status=status, responsavel=self.usuario,
            data_emissao=timezone.localdate(),
        )


class ListagemTests(MDFeViagemBase):
    """A tabela da especificação."""

    def test_as_tres_naturezas_aparecem_na_mesma_lista(self):
        """
        Um caminhão, várias naturezas: é isso que o manifesto descreve.
        """
        self._nota_de_remessa()
        self._nota_de_venda()
        self._nota_de_bonificacao()

        linhas = MDFeViagemService.documentos(self.viagem)

        self.assertEqual(
            sorted(l['tipo'] for l in linhas),
            ['Bonificação', 'Remessa', 'Venda'],
        )

    def test_cada_linha_traz_documento_destinatario_e_valor(self):
        documento = self._nota_de_venda()

        linha = next(
            l for l in MDFeViagemService.documentos(self.viagem)
            if l['documento'] == documento
        )

        self.assertEqual(linha['tipo'], 'Venda')
        self.assertEqual(linha['destinatario'], 'Cliente A')
        self.assertEqual(linha['valor'], Decimal('500.00'))
        self.assertEqual(linha['chave'], documento.chave)

    def test_a_remessa_tem_a_propria_empresa_como_destinataria(self):
        """É o que caracteriza a operação: não há comprador."""
        self._nota_de_remessa()

        linha = next(
            l for l in MDFeViagemService.documentos(self.viagem)
            if l['tipo'] == 'Remessa'
        )

        self.assertEqual(linha['destinatario'], self.filial.razao_social)

    def test_documento_de_outra_viagem_nao_entra(self):
        self._nota_de_venda()
        outra = Viagem.objects.create(
            filial=self.filial, numero=99, motorista_nome='Outro',
            veiculo_placa='XYZ9K88', responsavel=self.usuario,
        )

        self.assertEqual(MDFeViagemService.documentos(outra), [])

    def test_o_resumo_soma_o_valor_da_carga(self):
        self._nota_de_venda()
        self._nota_de_bonificacao()

        resumo = MDFeViagemService.resumo(
            MDFeViagemService.documentos(self.viagem),
        )

        self.assertEqual(resumo['documentos'], 2)
        self.assertEqual(resumo['valor'], Decimal('700.00'))


class ImpedimentoTests(MDFeViagemBase):
    """O que impede aparece escrito, e a linha continua na lista."""

    def test_nota_sem_chave_diz_que_nao_foi_transmitida(self):
        self._nota_de_venda(autorizar=False)

        linha = MDFeViagemService.documentos(self.viagem)[0]

        self.assertIn('não foi transmitida', linha['impedimento'])

    def test_nota_nao_autorizada_e_impedida(self):
        documento = self._nota_de_venda(autorizar=False)
        documento.chave = '9' * 44
        documento.save(update_fields=['chave'])

        linha = MDFeViagemService.documentos(self.viagem)[0]

        self.assertIn('não está autorizada', linha['impedimento'])

    def test_nota_cancelada_continua_na_lista_com_o_motivo(self):
        """Sumir com a linha faria a pessoa procurar a nota que ela sabe que existe."""
        documento = self._nota_de_venda()
        documento.status = StatusDocumentoFiscal.CANCELADA
        documento.save(update_fields=['status'])

        linhas = MDFeViagemService.documentos(self.viagem)

        self.assertEqual(len(linhas), 1)
        self.assertIn('cancelada', linhas[0]['impedimento'].lower())

    def test_nota_em_outro_manifesto_e_impedida(self):
        documento = self._nota_de_venda()
        outro = MDFe.objects.create(
            filial=self.filial, numero=77, serie='1',
            status=MDFe.Status.RASCUNHO, responsavel=self.usuario,
            data_emissao=timezone.localdate(),
        )
        DocumentoMDFe.objects.create(
            mdfe=outro, documento_fiscal=documento,
            tipo_documento=DocumentoMDFe.TipoDocumento.NFE,
            chave_acesso=documento.chave,
        )
        self._mdfe()

        linha = MDFeViagemService.documentos(self.viagem)[0]

        self.assertIn('outro MDF-e', linha['impedimento'])


class ConsolidacaoTests(MDFeViagemBase):
    """Vincular e desvincular."""

    def test_vincular_poe_as_notas_no_manifesto(self):
        remessa = self._nota_de_remessa()
        venda = self._nota_de_venda()
        mdfe = self._mdfe()

        quantos = MDFeViagemService.vincular(
            self.viagem, [remessa.pk, venda.pk], self.usuario,
        )

        self.assertEqual(quantos, 2)
        self.assertEqual(
            DocumentoMDFe.objects.filter(mdfe=mdfe).count(), 2,
        )

    def test_a_natureza_de_cada_nota_continua_a_mesma(self):
        """
        O manifesto é documento de transporte: consolidar não transforma
        venda, remessa e bonificação numa operação só.
        """
        remessa = self._nota_de_remessa()
        bonificacao = self._nota_de_bonificacao()
        self._mdfe()

        MDFeViagemService.vincular(
            self.viagem, [remessa.pk, bonificacao.pk], self.usuario,
        )

        remessa.refresh_from_db()
        bonificacao.refresh_from_db()
        self.assertEqual(remessa.origem_tipo, 'viagem_remessa')
        self.assertEqual(bonificacao.origem_tipo, 'viagem_bonificacao')

    def test_sem_manifesto_a_vinculacao_para_e_diz_o_que_falta(self):
        venda = self._nota_de_venda()

        with self.assertRaises(DadosInvalidosError) as erro:
            MDFeViagemService.vincular(self.viagem, [venda.pk], self.usuario)

        self.assertIn('MDF-e', str(erro.exception))

    def test_nota_impedida_nao_e_vinculada_nem_por_id(self):
        """
        A tela filtra, o serviço decide: id colado à mão poria no manifesto
        uma nota que a SEFAZ não conhece.
        """
        documento = self._nota_de_venda(autorizar=False)
        self._mdfe()

        with self.assertRaises(DadosInvalidosError):
            MDFeViagemService.vincular(
                self.viagem, [documento.pk], self.usuario,
            )

    def test_desvincular_tira_a_nota(self):
        """Marcar a nota errada na pressa da doca é comum."""
        venda = self._nota_de_venda()
        mdfe = self._mdfe()
        MDFeViagemService.vincular(self.viagem, [venda.pk], self.usuario)

        tirou = MDFeViagemService.desvincular(self.viagem, venda.pk)

        self.assertTrue(tirou)
        self.assertEqual(DocumentoMDFe.objects.filter(mdfe=mdfe).count(), 0)

    def test_manifesto_autorizado_nao_muda_mais(self):
        """Mudar o que ele ampara depois disso exige cancelá-lo na SEFAZ."""
        venda = self._nota_de_venda()
        mdfe = self._mdfe()
        MDFeViagemService.vincular(self.viagem, [venda.pk], self.usuario)
        mdfe.status = MDFe.Status.AUTORIZADO
        mdfe.save(update_fields=['status'])

        with self.assertRaises(DadosInvalidosError):
            MDFeViagemService.desvincular(self.viagem, venda.pk)

    def test_manifesto_cancelado_nao_e_o_da_viagem(self):
        mdfe = self._mdfe()
        mdfe.status = MDFe.Status.CANCELADO
        mdfe.save(update_fields=['status'])

        self.assertIsNone(MDFeViagemService.mdfe_da_viagem(self.viagem))


class TelaTests(MDFeViagemBase):
    """A tela MDF-e da viagem."""

    def _abrir(self):
        return self.client.get(
            reverse('logistica:viagem-mdfe', args=[self.viagem.pk]),
        ).content.decode()

    def test_a_tela_mostra_a_tabela_da_especificacao(self):
        self._nota_de_remessa()
        self._nota_de_venda()
        self._nota_de_bonificacao()

        html = self._abrir()

        for coluna in ('Tipo', 'Documento', 'Cliente / destinatário', 'Valor'):
            self.assertIn(coluna, html, f'a coluna {coluna} sumiu')
        for tipo in ('Remessa', 'Venda', 'Bonificação'):
            self.assertIn(tipo, html)

    def test_sem_manifesto_a_tela_oferece_criar(self):
        self._nota_de_venda()

        html = self._abrir()

        self.assertIn('Criar MDF-e desta viagem', html)

    def test_com_manifesto_a_tela_oferece_vincular(self):
        self._nota_de_venda()
        self._mdfe()

        html = self._abrir()

        self.assertIn('Vincular ao MDF-e', html)

    def test_vincular_pela_tela(self):
        venda = self._nota_de_venda()
        mdfe = self._mdfe()

        self.client.post(
            reverse('logistica:viagem-mdfe', args=[self.viagem.pk]),
            {'acao': 'vincular', 'documentos': [venda.pk]},
        )

        self.assertEqual(DocumentoMDFe.objects.filter(mdfe=mdfe).count(), 1)

    def test_desvincular_pela_tela(self):
        venda = self._nota_de_venda()
        mdfe = self._mdfe()
        MDFeViagemService.vincular(self.viagem, [venda.pk], self.usuario)

        self.client.post(
            reverse('logistica:viagem-mdfe', args=[self.viagem.pk]),
            {'acao': 'desvincular', 'documento': venda.pk},
        )

        self.assertEqual(DocumentoMDFe.objects.filter(mdfe=mdfe).count(), 0)

    def test_a_viagem_leva_ate_a_tela(self):
        html = self.client.get(
            reverse('logistica:viagem-detail', args=[self.viagem.pk]),
        ).content.decode()

        self.assertIn('MDF-e da viagem', html)
