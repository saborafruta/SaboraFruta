"""
Bonificação entregue durante a viagem.

O CAMPO ÓRFÃO. `SaldoCarga.quantidade_bonificada` existia e a conciliação já
o somava — mas não havia como preenchê-lo: dava para sair com mercadoria de
bonificação na carga e não dava para registrar a bonificação feita na rua.

O QUE ESTES TESTES CERCAM:

  · BONIFICAÇÃO É A MESMA ENTREGA COM OUTRA NATUREZA: mesmo cliente, mesmo
    saldo, mesmos itens, mesma tela. Muda que ninguém paga e que o CFOP é
    outro. Um modelo próprio ao lado daria duas listas de entregas feitas na
    rua, e elas acabariam discordando sobre o que saiu do caminhão;

  · CADA UMA BAIXA NA SUA COLUNA. Somar bonificação em "vendido" faria a
    viagem parecer ter faturado o que foi dado — e a conciliação existe
    justamente para separar os dois;

  · O SALDO É O MESMO LIMITE. Não se bonifica o que não está no caminhão;

  · CANCELAR DEVOLVE NA COLUNA DE ONDE SAIU, senão sobra venda negativa e
    bonificação fantasma na mesma linha;

  · A NOTA SAI PELA NATUREZA DA ESPÉCIE `bonificacao`, e sem meio de
    pagamento: declarar um diria à SEFAZ que houve recebimento.
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
from apps.logistica.models import SaldoCarga, VendaViagem, Viagem
from apps.logistica.services.venda_fora_nfe import VendaForaNFeService
from apps.logistica.services.venda_viagem import VendaViagemService
from apps.logistica.services.viagem import ViagemService
from apps.produtos.models import Produto, ProdutoFilial
from apps.produtos.models.unidade import UnidadeMedida, UnidadeMedidaFilial

ZERO = Decimal('0')
T = VendaViagem.Tipo


class BonificacaoBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Brinde LTDA', nome_fantasia='Brinde',
            cnpj='41345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='41345678000272',
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
            email='rua@brinde.local', nome='Rua', password='x' * 12,
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
        self.natureza_bonificacao = NaturezaOperacao.objects.create(
            filial=self.filial, codigo='bonif', descricao='Bonificação',
            especie=NaturezaOperacao.Especie.BONIFICACAO,
        )
        RegraNaturezaOperacao.objects.create(
            natureza=self.natureza_bonificacao, cfop='5910',
            natureza_operacao_texto='Bonificacao',
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

    def _entregar(self, quantidade='20', tipo=T.BONIFICACAO, motivo=None):
        """
        Uma entrega na rua. Bonificação leva motivo por padrão porque ele
        passou a ser obrigatório — é ele que explica a mercadoria que saiu
        sem cobrança.
        """
        return VendaViagemService.registrar(self.viagem, {
            'tipo': tipo, 'produto': self.produto, 'quantidade': quantidade,
            'valor_unitario': '10', 'cliente': self.cliente,
            'motivo': motivo or (
                VendaViagem.Motivo.COMERCIAL if tipo == T.BONIFICACAO else ''
            ),
        }, usuario=self.usuario)

    def _saldo(self) -> SaldoCarga:
        return SaldoCarga.objects.get(viagem=self.viagem, produto=self.produto)


class RegistroTests(BonificacaoBase):
    """Registrar a bonificação contra o saldo."""

    def test_a_bonificacao_baixa_na_coluna_dela(self):
        """
        Somar bonificação em "vendido" faria a viagem parecer ter faturado o
        que foi dado.
        """
        self._entregar('20')

        saldo = self._saldo()
        self.assertEqual(saldo.quantidade_bonificada, Decimal('20.000'))
        self.assertEqual(saldo.quantidade_vendida, ZERO)
        self.assertEqual(saldo.quantidade_em_poder, Decimal('280.000'))

    def test_a_venda_continua_baixando_na_dela(self):
        self._entregar('50', tipo=T.VENDA)

        saldo = self._saldo()
        self.assertEqual(saldo.quantidade_vendida, Decimal('50.000'))
        self.assertEqual(saldo.quantidade_bonificada, ZERO)

    def test_as_duas_consomem_o_mesmo_saldo(self):
        """
        É a mesma mercadoria no mesmo caminhão: bonificar 20 tira 20 do que
        ainda dá para vender.
        """
        self._entregar('50', tipo=T.VENDA)
        self._entregar('20')

        self.assertEqual(self._saldo().quantidade_em_poder, Decimal('230.000'))

    def test_nao_se_bonifica_mais_do_que_esta_no_caminhao(self):
        with self.assertRaises(DadosInvalidosError):
            self._entregar('400')

    def test_tipo_desconhecido_e_recusado(self):
        with self.assertRaises(DadosInvalidosError):
            self._entregar('10', tipo='brinde')

    def test_sem_tipo_e_venda(self):
        """
        O padrão preserva o que essas entregas sempre foram — mudar isso
        reescreveria a conciliação de viagens já encerradas.
        """
        venda = VendaViagemService.registrar(self.viagem, {
            'produto': self.produto, 'quantidade': '10',
            'valor_unitario': '10', 'cliente': self.cliente,
        }, usuario=self.usuario)

        self.assertEqual(venda.tipo, T.VENDA)
        self.assertEqual(self._saldo().quantidade_vendida, Decimal('10.000'))

    def test_o_item_adicionado_depois_segue_o_tipo_da_entrega(self):
        bonificacao = self._entregar('20')

        VendaViagemService.adicionar_item(bonificacao, {
            'produto': self.produto, 'quantidade': '5', 'valor_unitario': '10',
        })

        saldo = self._saldo()
        self.assertEqual(saldo.quantidade_bonificada, Decimal('25.000'))
        self.assertEqual(saldo.quantidade_vendida, ZERO)


class CancelamentoTests(BonificacaoBase):
    """Voltar atrás."""

    def test_cancelar_devolve_na_coluna_de_onde_saiu(self):
        """
        Descontar de "vendido" criaria venda negativa e bonificação fantasma
        na mesma linha.
        """
        bonificacao = self._entregar('20')

        VendaViagemService.cancelar(bonificacao, 'cliente recusou')

        saldo = self._saldo()
        self.assertEqual(saldo.quantidade_bonificada, ZERO)
        self.assertEqual(saldo.quantidade_vendida, ZERO)
        self.assertEqual(saldo.quantidade_em_poder, Decimal('300.000'))


class NotaTests(BonificacaoBase):
    """A NF-e da bonificação."""

    def test_o_cfop_vem_da_natureza_de_bonificacao(self):
        bonificacao = self._entregar('20')

        payload = VendaForaNFeService.construir_payload(bonificacao, 1, 1)

        self.assertEqual(payload['items'][0]['cfop'], '5910')

    def test_a_bonificacao_nao_declara_meio_de_pagamento(self):
        """Declarar um diria à SEFAZ que houve recebimento."""
        bonificacao = self._entregar('20')

        payload = VendaForaNFeService.construir_payload(bonificacao, 1, 1)

        self.assertEqual(
            payload['formas_pagamento'],
            [{'forma_pagamento': '90', 'valor_pagamento': 0.0}],
        )

    def test_a_nota_diz_que_e_bonificacao(self):
        bonificacao = self._entregar('20')

        payload = VendaForaNFeService.construir_payload(bonificacao, 1, 1)

        self.assertIn(
            'Bonificacao', payload['informacoes_adicionais_contribuinte'],
        )

    def test_o_valor_vai_na_nota(self):
        """
        Mercadoria entregue sem valor declarado não existe para a SEFAZ —
        mesmo sem cobrança, a nota precisa dizer quanto vale o que saiu.
        """
        bonificacao = self._entregar('20')

        payload = VendaForaNFeService.construir_payload(bonificacao, 1, 1)

        self.assertEqual(payload['valor_total'], 200.0)

    def test_sem_natureza_de_bonificacao_a_emissao_para(self):
        self.natureza_bonificacao.delete()
        bonificacao = self._entregar('20')

        with self.assertRaises(DadosInvalidosError) as erro:
            VendaForaNFeService.emitir(bonificacao, self.usuario)

        self.assertIn('bonificação', str(erro.exception))

    def test_a_natureza_de_venda_nao_serve_para_bonificacao(self):
        """
        Usar a natureza de venda "porque parece igual" daria uma nota com
        CFOP de venda para mercadoria que ninguém pagou.
        """
        venda_fora = NaturezaOperacao.objects.create(
            filial=self.filial, codigo='venda-fora', descricao='Venda fora',
            especie=NaturezaOperacao.Especie.VENDA_FORA,
        )
        RegraNaturezaOperacao.objects.create(natureza=venda_fora, cfop='5103')
        bonificacao = self._entregar('20')

        payload = VendaForaNFeService.construir_payload(bonificacao, 1, 1)

        self.assertEqual(payload['items'][0]['cfop'], '5910')

    def test_a_nota_da_bonificacao_e_gravada_e_amarrada(self):
        bonificacao = self._entregar('20')

        documento = VendaForaNFeService.emitir(bonificacao, self.usuario)

        bonificacao.refresh_from_db()
        self.assertEqual(bonificacao.documento_fiscal, documento)
        self.assertIn('Bonificação', documento.destinatario_snapshot['observacao'])


class TelaTests(BonificacaoBase):
    """Os dois botões da rua."""

    def _detalhe(self):
        return self.client.get(
            reverse('logistica:viagem-detail', args=[self.viagem.pk]),
        ).content.decode()

    def test_a_viagem_na_rua_oferece_venda_e_bonificacao(self):
        """
        A tela de registrar entrega existia e NADA levava até ela — quem
        estava na rota só chegava lá digitando o endereço.
        """
        html = self._detalhe()

        self.assertIn('Registrar venda', html)
        self.assertIn('Registrar bonificação', html)

    def test_viagem_em_planejamento_nao_oferece(self):
        """Antes de sair não há saldo para entregar."""
        self.viagem.status = Viagem.Status.RASCUNHO
        self.viagem.save(update_fields=['status'])

        html = self._detalhe()

        self.assertNotIn('Registrar bonificação', html)

    def test_o_formulario_de_bonificacao_nao_pede_pagamento(self):
        resposta = self.client.get(
            reverse('logistica:viagem-venda-create', args=[self.viagem.pk]),
            {'tipo': 'bonificacao'},
        )

        html = resposta.content.decode()
        self.assertIn('Nova bonificação', html)
        # O CAMPO, e não a palavra: "Pagamento" aparece no menu lateral, e
        # procurar por ela testaria o menu em vez do formulário.
        self.assertNotIn('name="forma_pagamento"', html)
        self.assertNotIn('name="condicao_pagamento"', html)
        self.assertIn('Motivo da bonificação', html)

    def test_registrar_bonificacao_pela_tela(self):
        self.client.post(
            reverse('logistica:viagem-venda-create', args=[self.viagem.pk]),
            {
                'tipo': 'bonificacao', 'produto': self.produto.pk,
                'cliente': self.cliente.pk, 'quantidade': '20',
                'valor_unitario': '10', 'motivo': 'brinde',
            },
        )

        self.assertEqual(self._saldo().quantidade_bonificada, Decimal('20.000'))

    def test_a_entrega_aparece_marcada_como_bonificacao(self):
        self._entregar('20')

        self.assertIn('bonificação', self._detalhe())


class MotivoTests(BonificacaoBase):
    """
    Por que a mercadoria saiu sem cobrança.

    LISTA FECHADA, E NÃO TEXTO LIVRE: "por que demos 20 caixas?" é a pergunta
    que a auditoria faz e que o comercial precisa responder por cliente e por
    período — e isso não se faz agrupando frases digitadas à mão.
    """

    def _com_motivo(self, motivo, quantidade='20', **extras):
        dados = {
            'tipo': T.BONIFICACAO, 'produto': self.produto,
            'quantidade': quantidade, 'valor_unitario': '10',
            'cliente': self.cliente, 'motivo': motivo,
        }
        dados.update(extras)
        return VendaViagemService.registrar(
            self.viagem, dados, usuario=self.usuario,
        )

    def test_a_bonificacao_guarda_o_motivo(self):
        bonificacao = self._com_motivo(VendaViagem.Motivo.CAMPANHA)

        self.assertEqual(bonificacao.motivo, VendaViagem.Motivo.CAMPANHA)

    def test_bonificacao_sem_motivo_e_recusada(self):
        """Uma lista de cortesias sem explicação é o mesmo que não ter lista."""
        with self.assertRaises(DadosInvalidosError) as erro:
            self._com_motivo('')

        self.assertIn('motivo', str(erro.exception).lower())

    def test_motivo_desconhecido_e_recusado(self):
        with self.assertRaises(DadosInvalidosError):
            self._com_motivo('porque sim')

    def test_os_sete_motivos_da_especificacao_existem(self):
        self.assertEqual(
            [v for v, _ in VendaViagem.Motivo.choices],
            [
                'comercial', 'brinde', 'campanha', 'acao',
                'relacionamento', 'compensacao', 'outro',
            ],
        )

    def test_venda_nao_guarda_motivo(self):
        """
        Guardar um aqui faria o relatório de bonificações contar venda como
        cortesia.
        """
        venda = VendaViagemService.registrar(self.viagem, {
            'tipo': T.VENDA, 'produto': self.produto, 'quantidade': '10',
            'valor_unitario': '10', 'cliente': self.cliente,
            'motivo': VendaViagem.Motivo.BRINDE,
        }, usuario=self.usuario)

        self.assertEqual(venda.motivo, '')

    def test_o_pedido_relacionado_e_gravado_quando_existe(self):
        """
        "Esta cortesia foi por causa de quê?" não tem resposta no sistema sem
        o vínculo.
        """
        from django.utils import timezone

        from apps.vendas.models.pedido import PedidoVenda

        pedido = PedidoVenda.objects.create(
            filial=self.filial, numero_pedido='PV9', cliente=self.cliente,
            usuario=self.usuario, status=PedidoVenda.Status.CONFIRMADO,
            data_emissao=timezone.now(),
        )

        bonificacao = self._com_motivo(
            VendaViagem.Motivo.COMPENSACAO, pedido_venda=pedido,
        )

        self.assertEqual(bonificacao.pedido_venda, pedido)

    def test_o_pedido_e_opcional(self):
        bonificacao = self._com_motivo(VendaViagem.Motivo.BRINDE)

        self.assertIsNone(bonificacao.pedido_venda)


class FormularioTests(BonificacaoBase):
    """O formulário com o que a especificação pede."""

    def _abrir(self):
        return self.client.get(
            reverse('logistica:viagem-venda-create', args=[self.viagem.pk]),
            {'tipo': 'bonificacao'},
        ).content.decode()

    def test_o_formulario_oferece_os_sete_motivos(self):
        html = self._abrir()

        for rotulo in (
            'Bonificação comercial', 'Brinde', 'Campanha promocional',
            'Ação comercial', 'Relacionamento', 'Compensação', 'Outro',
        ):
            self.assertIn(rotulo, html, f'o motivo {rotulo} sumiu')

    def test_o_formulario_oferece_o_pedido_relacionado(self):
        html = self._abrir()

        self.assertIn('name="pedido_venda"', html)
        self.assertIn('Pedido relacionado', html)

    def test_o_formulario_mostra_o_tratamento_fiscal_parametrizado(self):
        """
        O CFOP não é digitado — vem da parametrização — mas quem entrega
        precisa VER qual vai sair antes de a mercadoria trocar de mão.
        """
        html = self._abrir()

        self.assertIn('CFOP', html)
        self.assertIn('tratamento-fiscal', html)

    def test_sem_natureza_cadastrada_o_formulario_avisa_e_nao_quebra(self):
        """
        A tela do vendedor não pode recusar abrir porque falta cadastro
        fiscal: quem recusa é a emissão da nota, mais tarde e com a mensagem
        certa.
        """
        self.natureza_bonificacao.delete()

        resposta = self.client.get(
            reverse('logistica:viagem-venda-create', args=[self.viagem.pk]),
            {'tipo': 'bonificacao'},
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertIn('Sem natureza de operação', resposta.content.decode())

    def test_registrar_com_motivo_pela_tela(self):
        self.client.post(
            reverse('logistica:viagem-venda-create', args=[self.viagem.pk]),
            {
                'tipo': 'bonificacao', 'produto': self.produto.pk,
                'cliente': self.cliente.pk, 'quantidade': '20',
                'valor_unitario': '10', 'motivo': 'campanha',
            },
        )

        bonificacao = VendaViagem.objects.get(viagem=self.viagem)
        self.assertEqual(bonificacao.motivo, 'campanha')

    def test_sem_motivo_a_tela_devolve_o_aviso_e_nao_registra(self):
        resposta = self.client.post(
            reverse('logistica:viagem-venda-create', args=[self.viagem.pk]),
            {
                'tipo': 'bonificacao', 'produto': self.produto.pk,
                'cliente': self.cliente.pk, 'quantidade': '20',
                'valor_unitario': '10', 'motivo': '',
            },
            follow=True,
        )

        self.assertIn('motivo', resposta.content.decode().lower())
        self.assertFalse(VendaViagem.objects.filter(viagem=self.viagem).exists())

    def test_a_viagem_mostra_o_motivo_da_bonificacao(self):
        VendaViagemService.registrar(self.viagem, {
            'tipo': T.BONIFICACAO, 'produto': self.produto, 'quantidade': '20',
            'valor_unitario': '10', 'cliente': self.cliente,
            'motivo': VendaViagem.Motivo.RELACIONAMENTO,
        }, usuario=self.usuario)

        html = self.client.get(
            reverse('logistica:viagem-detail', args=[self.viagem.pk]),
        ).content.decode()

        self.assertIn('Relacionamento', html)
