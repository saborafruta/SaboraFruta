"""
O vínculo entre a venda da rua e a NF-e de remessa.

A CADEIA: NF-e de remessa → viagem → produto (e lote) → venda → NF-e da
venda. Cada elo já existia guardado em algum lugar; o que faltava era a
LEITURA — e um vínculo que só se monta abrindo quatro telas não serve para o
que ele existe: responder, com a fiscalização na mesa, de qual remessa saiu
a mercadoria de uma venda específica.

O QUE ESTES TESTES CERCAM:

  · O VÍNCULO É GRAVADO NA HORA DA VENDA, não descoberto depois. Cancelada e
    reemitida a remessa, a busca apontaria a nota NOVA para mercadoria que
    saiu sob a ANTIGA — e o vínculo mudaria sozinho no dia em que ele mais
    importa;

  · UMA LINHA POR ITEM, porque é o item que tem produto e lote, e é por
    produto e lote que a remessa amparou a mercadoria;

  · O SALDO É O DE AGORA. Congelá-lo na venda daria um número que envelhece
    em silêncio;

  · VÍNCULO VAZIO APARECE COMO VAZIO. Venda registrada antes de a remessa
    existir fica sem vínculo — apontar para a nota que existir depois seria
    inventar um amparo que não havia.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.cadastros.models import Cliente, ClienteFilial
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.financeiro.constants.enums import StatusDocumentoFiscal
from apps.fiscal.models import NaturezaOperacao, RegraNaturezaOperacao
from apps.logistica.models import VendaViagem, Viagem
from apps.logistica.services.remessa_nfe import RemessaVendaForaService
from apps.logistica.services.venda_fora_nfe import VendaForaNFeService
from apps.logistica.services.venda_viagem import VendaViagemService
from apps.logistica.services.viagem import ViagemService
from apps.logistica.services.vinculo_remessa import VinculoRemessaService
from apps.produtos.models import Produto, ProdutoFilial
from apps.produtos.models.unidade import UnidadeMedida, UnidadeMedidaFilial

ZERO = Decimal('0')
T = VendaViagem.Tipo


class VinculoBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Elo LTDA', nome_fantasia='Elo',
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
            email='elo@rota.local', nome='Elo', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Mercado da Esquina',
            cpf_cnpj='12345678901', uf='RN', cidade='Natal',
        )
        ClienteFilial.objects.create(cliente=cls.cliente, filial=cls.filial)
        cls.remessa_natureza = NaturezaOperacao.objects.create(
            filial=cls.filial, codigo='remessa', descricao='Remessa venda fora',
            especie=NaturezaOperacao.Especie.REMESSA_VENDA_FORA,
            exige_destinatario=False,
        )
        RegraNaturezaOperacao.objects.create(
            natureza=cls.remessa_natureza, cfop='5904',
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        self.venda_natureza = NaturezaOperacao.objects.create(
            filial=self.filial, codigo='venda-fora', descricao='Venda fora',
            especie=NaturezaOperacao.Especie.VENDA_FORA,
        )
        RegraNaturezaOperacao.objects.create(
            natureza=self.venda_natureza, cfop='5103',
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
            'natureza': self.remessa_natureza, 'produto': self.produto,
            'quantidade': quantidade, 'valor_unitario': '10',
        })
        ViagemService.fechar_carga(viagem, usuario=self.usuario)
        viagem.status = Viagem.Status.EM_VENDAS
        viagem.save(update_fields=['status'])
        return viagem

    def _emitir_remessa(self, chave='1' * 44):
        documento = RemessaVendaForaService.emitir(self.viagem, self.usuario)
        if chave:
            documento.chave = chave
            documento.save(update_fields=['chave'])
        return documento

    def _vender(self, quantidade='50', tipo=T.VENDA):
        return VendaViagemService.registrar(self.viagem, {
            'tipo': tipo, 'produto': self.produto, 'quantidade': quantidade,
            'valor_unitario': '10', 'cliente': self.cliente,
        }, usuario=self.usuario)

    def _linha(self):
        return VinculoRemessaService.linhas(self.viagem)[0]


class GravacaoTests(VinculoBase):
    """O vínculo nasce com a venda."""

    def test_a_venda_guarda_a_remessa_que_a_amparou(self):
        remessa = self._emitir_remessa()

        venda = self._vender('50')

        self.assertEqual(venda.itens.first().remessa, remessa)

    def test_venda_antes_da_remessa_fica_sem_vinculo(self):
        """
        O caminhão sai de madrugada e a nota sai às 8h. Recusar a venda por
        isso pararia a rua por causa de um documento que chega depois.
        """
        venda = self._vender('50')

        self.assertIsNone(venda.itens.first().remessa)
        self.assertTrue(self._linha()['sem_vinculo'])

    def test_remessa_reemitida_nao_reescreve_o_vinculo_antigo(self):
        """
        Se a busca fosse feita na hora da consulta, a nota NOVA apareceria
        amparando mercadoria que saiu sob a ANTIGA.
        """
        primeira = self._emitir_remessa()
        venda = self._vender('50')

        primeira.status = StatusDocumentoFiscal.CANCELADA
        primeira.save(update_fields=['status'])
        segunda = RemessaVendaForaService.emitir(self.viagem, self.usuario)

        self.assertEqual(venda.itens.first().remessa, primeira)
        self.assertNotEqual(venda.itens.first().remessa, segunda)

    def test_o_item_acrescentado_depois_tambem_guarda(self):
        remessa = self._emitir_remessa()
        venda = self._vender('50')

        item = VendaViagemService.adicionar_item(venda, {
            'produto': self.produto, 'quantidade': '10', 'valor_unitario': '10',
        })

        self.assertEqual(item.remessa, remessa)


class LeituraTests(VinculoBase):
    """A cadeia lida de uma vez."""

    def test_a_linha_traz_chave_numero_serie_e_data(self):
        remessa = self._emitir_remessa()
        self._vender('50')

        linha = self._linha()

        self.assertEqual(linha['chave'], '1' * 44)
        self.assertEqual(linha['numero'], remessa.numero)
        self.assertEqual(linha['serie'], remessa.serie)
        self.assertEqual(linha['data'], remessa.data_emissao)

    def test_a_linha_traz_produto_lote_e_as_quantidades(self):
        self._emitir_remessa()
        self._vender('50')

        linha = self._linha()

        self.assertEqual(linha['produto'], self.produto)
        self.assertEqual(linha['remetida'], Decimal('300.000'))
        self.assertEqual(linha['vendida'], Decimal('50.000'))
        self.assertEqual(linha['saldo'], Decimal('250.000'))

    def test_o_saldo_e_o_de_agora(self):
        """
        Congelá-lo na venda daria um número que envelhece em silêncio, e
        "quanto ainda tem" passaria a ser respondido com o saldo de ontem.
        """
        self._emitir_remessa()
        self._vender('50')
        self._vender('30')

        primeira, segunda = VinculoRemessaService.linhas(self.viagem)

        self.assertEqual(primeira['vendida'], Decimal('50.000'))
        self.assertEqual(segunda['vendida'], Decimal('30.000'))
        # As duas leem o mesmo saldo, que e' o atual da carga.
        self.assertEqual(primeira['saldo'], Decimal('220.000'))
        self.assertEqual(segunda['saldo'], Decimal('220.000'))

    def test_uma_linha_por_item(self):
        """
        É o item que tem produto e lote — e é por eles que a remessa amparou.

        A carga do segundo produto entra ANTES de o caminhão sair: depois
        que a carga fecha, mexer nela é reescrever o que o documento já
        disse, e o serviço recusa (com razão).
        """
        outro = self._produto('P2', '500')
        viagem = Viagem.objects.create(
            filial=self.filial, numero=2, motorista_nome='Seu Zé',
            veiculo_placa='ABC1D23', vendedor=self.usuario,
            responsavel=self.usuario,
        )
        for produto, quantidade in ((self.produto, '300'), (outro, '100')):
            ViagemService.adicionar_item(viagem, {
                'natureza': self.remessa_natureza, 'produto': produto,
                'quantidade': quantidade, 'valor_unitario': '5',
            })
        ViagemService.fechar_carga(viagem, usuario=self.usuario)
        viagem.status = Viagem.Status.EM_VENDAS
        viagem.save(update_fields=['status'])

        venda = VendaViagemService.registrar(viagem, {
            'produto': self.produto, 'quantidade': '50',
            'valor_unitario': '10', 'cliente': self.cliente,
        }, usuario=self.usuario)
        VendaViagemService.adicionar_item(venda, {
            'produto': outro, 'quantidade': '10', 'valor_unitario': '5',
        })

        linhas = VinculoRemessaService.linhas(viagem)

        self.assertEqual(len(linhas), 2)
        self.assertEqual(
            {l['produto'] for l in linhas}, {self.produto, outro},
        )

    def test_a_nota_da_venda_fecha_a_cadeia(self):
        self._emitir_remessa()
        venda = self._vender('50')

        nota = VendaForaNFeService.emitir(venda, self.usuario)

        self.assertEqual(self._linha()['nota_venda'], nota)

    def test_venda_sem_nota_aparece_como_pendente(self):
        self._emitir_remessa()
        self._vender('50')

        linha = self._linha()

        self.assertIsNone(linha['nota_venda'])
        self.assertEqual(
            VinculoRemessaService.resumo([linha])['sem_nota_venda'], 1,
        )

    def test_remessa_sem_chave_e_marcada(self):
        """A chave é o que a fiscalização procura primeiro."""
        self._emitir_remessa(chave='')
        self._vender('50')

        linha = self._linha()

        self.assertTrue(linha['sem_chave'])
        self.assertFalse(linha['sem_vinculo'])

    def test_venda_cancelada_fica_de_fora(self):
        self._emitir_remessa()
        venda = self._vender('50')
        VendaViagemService.cancelar(venda, 'desistiu')

        self.assertEqual(VinculoRemessaService.linhas(self.viagem), [])
        self.assertEqual(
            len(VinculoRemessaService.linhas(self.viagem, incluir_canceladas=True)),
            1,
        )

    def test_a_bonificacao_entra_marcada(self):
        self._emitir_remessa()
        self._vender('20', tipo=T.BONIFICACAO)

        self.assertTrue(self._linha()['bonificacao'])


class CaminhoInversoTests(VinculoBase):
    """Partindo da nota de remessa."""

    def test_a_remessa_diz_o_que_ela_amparou(self):
        """
        É a pergunta da fiscalização quando ela chega pela NOTA: "esta
        remessa de 300 caixas virou o quê?".
        """
        remessa = self._emitir_remessa()
        venda = self._vender('50')

        linhas = VinculoRemessaService.por_remessa(remessa)

        self.assertEqual(len(linhas), 1)
        self.assertEqual(linhas[0]['venda'], venda)
        self.assertEqual(linhas[0]['vendida'], Decimal('50.000'))


class TelaTests(VinculoBase):
    """A cadeia na tela da viagem."""

    def _detalhe(self):
        return self.client.get(
            reverse('logistica:viagem-detail', args=[self.viagem.pk]),
        ).content.decode()

    def test_a_tabela_mostra_a_cadeia(self):
        self._emitir_remessa()
        self._vender('50')

        html = self._detalhe()

        self.assertIn('Vínculo entre venda e remessa', html)
        self.assertIn('1' * 44, html)
        self.assertIn('Mercado da Esquina', html)

    def test_a_tela_avisa_a_entrega_sem_remessa(self):
        self._vender('50')

        html = self._detalhe()

        self.assertIn('sem remessa vinculada', html)
