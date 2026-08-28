"""
Relatório de acerto de viagem.

É o documento que o escritório e quem viajou olham juntos no fim: o que saiu,
o que virou dinheiro, o que foi dado, o que voltou — e quais documentos
amparam cada uma dessas coisas.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.models import Cliente, ClienteFilial
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.fiscal.models import NaturezaOperacao, RegraNaturezaOperacao
from apps.logistica.models import VendaViagem, Viagem
from apps.logistica.services.relatorio_acerto import RelatorioAcertoService
from apps.logistica.services.remessa_nfe import RemessaVendaForaService
from apps.logistica.services.venda_viagem import VendaViagemService
from apps.logistica.services.viagem import ViagemService
from apps.produtos.models import Produto, ProdutoFilial
from apps.produtos.models.unidade import UnidadeMedida, UnidadeMedidaFilial

E = NaturezaOperacao.Especie


class RelatorioAcertoTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Acerto LTDA', nome_fantasia='Acerto',
            cnpj='63345678000191', segmento='polpa_frutas',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='63345678000272',
            uf='RN', cidade='Natal', is_matriz=True, endereco='Rua A',
            numero='100', bairro='Centro', cep='59000000',
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='CX', descricao='Caixa',
            tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='ace@viagem.local', nome='Ace', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Cliente A',
            cpf_cnpj='12345678901', uf='RN', cidade='Natal',
        )
        ClienteFilial.objects.create(cliente=cls.cliente, filial=cls.filial)

        cls.naturezas = {}
        for codigo, especie, cfop in (
            ('venda', E.VENDA, '5102'),
            ('remessa', E.REMESSA_VENDA_FORA, '5904'),
            ('bonificacao', E.BONIFICACAO, '5910'),
        ):
            natureza = NaturezaOperacao.objects.create(
                filial=cls.filial, codigo=codigo, descricao=codigo.title(),
                especie=especie, exige_destinatario=especie != E.REMESSA_VENDA_FORA,
            )
            RegraNaturezaOperacao.objects.create(natureza=natureza, cfop=cfop)
            cls.naturezas[especie] = natureza

    def setUp(self):
        self.client.force_login(self.usuario)
        self.produto = self._produto('CX1', '1000')
        self.viagem = Viagem.objects.create(
            filial=self.filial, numero=125, motorista_nome='João da Silva',
            motorista_documento='12345678901', veiculo_placa='ABC1234',
            veiculo_descricao='Volvo FH', vendedor=self.usuario,
            responsavel=self.usuario, rota='Zona Norte',
            previsao_retorno=timezone.localdate(),
            observacao='Viagem de teste.',
        )

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

    def _carregar(self, especie, quantidade, valor='10', cliente=None):
        return ViagemService.adicionar_item(self.viagem, {
            'natureza': self.naturezas[especie], 'produto': self.produto,
            'quantidade': quantidade, 'valor_unitario': valor,
            'cliente': cliente,
        })

    def _na_rua(self):
        ViagemService.fechar_carga(self.viagem, usuario=self.usuario)
        self.viagem.status = Viagem.Status.EM_VENDAS
        self.viagem.save(update_fields=['status'])

    def _entregar(self, quantidade, tipo=VendaViagem.Tipo.VENDA, valor='10', **extras):
        dados = {
            'produto': self.produto, 'quantidade': quantidade,
            'valor_unitario': valor, 'cliente': self.cliente, 'tipo': tipo,
        }
        if tipo == VendaViagem.Tipo.BONIFICACAO:
            dados['motivo'] = VendaViagem.Motivo.values[0]
        dados.update(extras)
        return VendaViagemService.registrar(self.viagem, dados, usuario=self.usuario)

    def _relatorio(self):
        return RelatorioAcertoService.relatorio(self.viagem)

    # ── Identificação ────────────────────────────────────────────────────

    def test_o_cabecalho_traz_a_viagem_inteira(self):
        identificacao = self._relatorio()['identificacao']

        self.assertEqual(identificacao['numero'], 125)
        self.assertEqual(identificacao['motorista'], 'João da Silva')
        self.assertEqual(identificacao['veiculo'], 'Volvo FH')
        self.assertEqual(identificacao['placa'], 'ABC1234')
        self.assertEqual(identificacao['rota'], 'Zona Norte')
        self.assertIn('Viagem de teste', identificacao['observacoes'])

    def test_previsao_e_retorno_aparecem_separados(self):
        """
        É assim que se vê se a viagem atrasou; guardar só um número apagaria
        essa informação.
        """
        identificacao = self._relatorio()['identificacao']

        self.assertIsNotNone(identificacao['previsao_retorno'])
        self.assertIsNone(identificacao['data_retorno'])

    # ── Quantidades ──────────────────────────────────────────────────────

    def test_as_quantidades_vem_do_quadro_de_acerto(self):
        self._carregar(E.VENDA, '150', cliente=self.cliente)
        self._carregar(E.REMESSA_VENDA_FORA, '200')
        self._carregar(E.BONIFICACAO, '10', cliente=self.cliente)

        quantidades = self._relatorio()['quantidades']

        self.assertEqual(quantidades['carregada'], Decimal('360.000'))
        self.assertEqual(quantidades['vendida_previa'], Decimal('150.000'))
        self.assertEqual(quantidades['bonificada'], Decimal('10.000'))

    def test_a_venda_na_rua_soma_com_a_ja_vendida(self):
        """
        Separar as duas na leitura é útil; somar só uma delas seria mentir
        sobre o que a viagem levou.
        """
        self._carregar(E.VENDA, '150', cliente=self.cliente)
        self._carregar(E.REMESSA_VENDA_FORA, '200')
        self._na_rua()
        self._entregar('50')

        quantidades = self._relatorio()['quantidades']

        self.assertEqual(quantidades['vendida_na_rua'], Decimal('50.000'))
        self.assertEqual(quantidades['vendida'], Decimal('200.000'))

    def test_o_retorno_aparece_na_conta(self):
        self._carregar(E.REMESSA_VENDA_FORA, '200')
        self._na_rua()
        self._entregar('50')
        ViagemService.registrar_retorno(
            self.viagem, self.produto, Decimal('150'), usuario=self.usuario,
        )

        quantidades = self._relatorio()['quantidades']

        self.assertEqual(quantidades['retornada'], Decimal('150.000'))
        self.assertEqual(quantidades['em_poder'], Decimal('0'))

    # ── Valores ──────────────────────────────────────────────────────────

    def test_o_valor_da_carga_e_a_soma_do_que_subiu(self):
        self._carregar(E.VENDA, '150', valor='10', cliente=self.cliente)
        self._carregar(E.REMESSA_VENDA_FORA, '200', valor='10')

        valores = self._relatorio()['valores']

        self.assertEqual(valores['carga'], Decimal('3500.00'))

    def test_o_valor_vendido_soma_a_carga_vendida_e_a_venda_na_rua(self):
        self._carregar(E.VENDA, '150', valor='10', cliente=self.cliente)
        self._carregar(E.REMESSA_VENDA_FORA, '200', valor='10')
        self._na_rua()
        self._entregar('50', valor='12')

        valores = self._relatorio()['valores']

        self.assertEqual(valores['vendido_previa'], Decimal('1500.00'))
        self.assertEqual(valores['vendido_na_rua'], Decimal('600.00'))
        self.assertEqual(valores['vendido'], Decimal('2100.00'))

    def test_o_valor_bonificado_soma_carga_e_rua(self):
        self._carregar(E.BONIFICACAO, '10', valor='10', cliente=self.cliente)
        self._carregar(E.REMESSA_VENDA_FORA, '200', valor='10')
        self._na_rua()
        self._entregar('5', tipo=VendaViagem.Tipo.BONIFICACAO, valor='10')

        valores = self._relatorio()['valores']

        self.assertEqual(valores['bonificado'], Decimal('150.00'))

    def test_o_retorno_e_avaliado_pelo_valor_de_saida(self):
        """
        A mercadoria voltou como saiu. Avaliá-la por preço de venda faria a
        viagem parecer ter perdido dinheiro só por ter voltado com carga.

        A REMESSA SAI POR 8 e o produto vale 10 na tabela, de propósito: com os
        dois iguais o teste passaria mesmo se o cálculo usasse o preço errado.
        """
        self._carregar(E.REMESSA_VENDA_FORA, '200', valor='8')
        self._na_rua()
        ViagemService.registrar_retorno(
            self.viagem, self.produto, Decimal('150'), usuario=self.usuario,
        )

        self.assertEqual(self.produto.preco_venda, Decimal('10.00'))
        self.assertEqual(self._relatorio()['valores']['retornado'], Decimal('1200.00'))

    def test_venda_cancelada_sai_do_valor(self):
        """
        Contá-la faria o acerto cobrar do vendedor um dinheiro que nunca
        entrou.
        """
        self._carregar(E.REMESSA_VENDA_FORA, '200', valor='10')
        self._na_rua()
        venda = self._entregar('50')
        VendaViagemService.cancelar(venda, motivo='Cliente desistiu')

        self.assertEqual(self._relatorio()['valores']['vendido_na_rua'], Decimal('0'))

    # ── Clientes atendidos ───────────────────────────────────────────────

    def test_conta_clientes_por_pessoa_e_nao_por_entrega(self):
        """
        O mesmo cliente pode ter comprado duas vezes na mesma rota, e dizer
        que a viagem atendeu dois é inflar o número que mede a rota.
        """
        self._carregar(E.REMESSA_VENDA_FORA, '200')
        self._na_rua()
        self._entregar('30')
        self._entregar('20')

        clientes = self._relatorio()['clientes']

        self.assertEqual(clientes['total'], 1)
        self.assertEqual(clientes['lista'][0]['vendas'], 2)

    def test_cliente_sem_cadastro_tambem_conta(self):
        self._carregar(E.REMESSA_VENDA_FORA, '200')
        self._na_rua()
        self._entregar('10', cliente=None, cliente_nome='Padaria do Zé',
                       cliente_documento='98765432100')

        clientes = self._relatorio()['clientes']

        self.assertEqual(clientes['total'], 1)
        self.assertIsNone(clientes['lista'][0]['cliente'])

    def test_o_cliente_da_carga_ja_vendida_tambem_foi_atendido(self):
        """Ele recebeu mercadoria nesta viagem, mesmo sem venda na rua."""
        self._carregar(E.VENDA, '150', cliente=self.cliente)

        clientes = self._relatorio()['clientes']

        self.assertEqual(clientes['total'], 1)
        self.assertEqual(clientes['lista'][0]['valor_vendido'], Decimal('1500.00'))

    # ── Documentos ───────────────────────────────────────────────────────

    def test_a_remessa_emitida_aparece_no_relatorio(self):
        self._carregar(E.REMESSA_VENDA_FORA, '200', valor='10')
        self._na_rua()
        nota = RemessaVendaForaService.emitir(self.viagem, usuario=self.usuario)

        documentos = self._relatorio()['documentos']

        self.assertEqual(documentos['remessa'], nota)
        self.assertEqual(documentos['total'], 1)

    def test_a_remessa_nao_emitida_aparece_como_pendencia(self):
        """
        Viagem com remessa não emitida é diferente de viagem que não precisava
        de remessa, e o acerto não pode passar em branco por cima disso.
        """
        self._carregar(E.REMESSA_VENDA_FORA, '200', valor='10')
        self._na_rua()

        documentos = self._relatorio()['documentos']

        self.assertIsNone(documentos['remessa'])
        self.assertTrue(
            any('remessa não emitida' in p for p in documentos['pendentes']),
            documentos['pendentes'],
        )

    def test_viagem_sem_venda_fora_nao_cobra_remessa(self):
        self._carregar(E.VENDA, '150', cliente=self.cliente)

        documentos = self._relatorio()['documentos']

        self.assertEqual(documentos['pendentes'], [])

    def test_as_vendas_da_rua_aparecem_uma_a_uma(self):
        self._carregar(E.REMESSA_VENDA_FORA, '200')
        self._na_rua()
        self._entregar('30')
        self._entregar('20')

        documentos = self._relatorio()['documentos']

        self.assertEqual(len(documentos['vendas']), 2)
        self.assertTrue(
            any('venda(s) na rua sem NF-e' in p for p in documentos['pendentes']),
        )

    def test_bonificacao_e_venda_ficam_em_listas_separadas(self):
        self._carregar(E.REMESSA_VENDA_FORA, '200')
        self._na_rua()
        self._entregar('30')
        self._entregar('5', tipo=VendaViagem.Tipo.BONIFICACAO)

        documentos = self._relatorio()['documentos']

        self.assertEqual(len(documentos['vendas']), 1)
        self.assertEqual(len(documentos['bonificacoes']), 1)

    # ── A tela ───────────────────────────────────────────────────────────

    def test_a_tela_traz_todos_os_campos_pedidos(self):
        self._carregar(E.VENDA, '150', cliente=self.cliente)
        self._carregar(E.REMESSA_VENDA_FORA, '200')
        self._carregar(E.BONIFICACAO, '10', cliente=self.cliente)

        html = self.client.get(
            reverse('logistica:viagem-relatorio-acerto', args=[self.viagem.pk]),
        ).content.decode()

        for rotulo in (
            'João da Silva', 'ABC1234', 'Volvo FH', 'Zona Norte',
            'Carga', 'Vendido', 'Bonificado', 'Retornado',
            'Clientes atendidos', 'NF-e de remessa', 'NF-e de venda',
            'NF-e de bonificação', 'NF-e de retorno', 'MDF-e', 'Observações',
        ):
            self.assertIn(rotulo, html, f'"{rotulo}" sumiu do relatório')

    def test_a_tela_diz_se_a_conta_fechou(self):
        self._carregar(E.REMESSA_VENDA_FORA, '200')
        self._na_rua()

        html = self.client.get(
            reverse('logistica:viagem-relatorio-acerto', args=[self.viagem.pk]),
        ).content.decode()

        self.assertIn('Ainda no caminhão', html)

    def test_relatorio_de_outra_filial_nao_abre(self):
        outra = Filial.objects.create(
            empresa=self.empresa, razao_social='Segunda',
            cnpj='31345678000677', uf='RN', cidade='Mossoro',
        )
        alheia = Viagem.objects.create(
            filial=outra, numero=1, motorista_nome='Zé',
        )

        resposta = self.client.get(
            reverse('logistica:viagem-relatorio-acerto', args=[alheia.pk]),
        )

        self.assertEqual(resposta.status_code, 404)

    def test_a_tela_nao_vaza_sintaxe_de_template(self):
        self._carregar(E.REMESSA_VENDA_FORA, '200')

        html = self.client.get(
            reverse('logistica:viagem-relatorio-acerto', args=[self.viagem.pk]),
        ).content.decode()

        for resto in ('{#', '#}', '{%', '%}'):
            self.assertNotIn(resto, html, 'vazou sintaxe de template no HTML')
