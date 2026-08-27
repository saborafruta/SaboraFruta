"""
Os três botões que compõem a carga.

TRÊS BOTÕES, TRÊS FORMULÁRIOS — e não um só com um seletor de natureza. Cada
operação pergunta coisas diferentes: venda tem comprador, venda fora sai
justamente porque ainda não tem, e bonificação tem destinatário mas não cobra
dele. Um formulário genérico obrigaria quem monta a carga a escolher CFOP no
meio do carregamento.
"""
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from django.urls import reverse

from apps.cadastros.models import Cliente, ClienteFilial
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.fiscal.models import NaturezaOperacao, RegraNaturezaOperacao
from apps.logistica.forms_carga import PERFIS, ItemCargaForm
from apps.logistica.models import ItemCarga, Viagem
from apps.produtos.models import Produto, ProdutoFilial
from apps.produtos.models.unidade import UnidadeMedida, UnidadeMedidaFilial
from apps.vendas.models.pedido import PedidoVenda

VENDA = NaturezaOperacao.Especie.VENDA
VENDA_FORA = NaturezaOperacao.Especie.REMESSA_VENDA_FORA
BONIFICACAO = NaturezaOperacao.Especie.BONIFICACAO


class ComporACargaTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Carga LTDA', nome_fantasia='Carga',
            cnpj='63345678000191', segmento='polpa_frutas',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='63345678000272',
            uf='RN', cidade='Natal', is_matriz=True,
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
            email='carga@viagem.local', nome='Carga', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Cliente A',
            cpf_cnpj='12345678901', uf='RN',
        )
        ClienteFilial.objects.create(cliente=cls.cliente, filial=cls.filial)
        cls.produto = Produto.objects.create(
            filial=cls.filial, unidade_medida=cls.unidade,
            descricao='Caixa de polpa', codigo='CX1', ncm='20079900',
        )
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.filial)

        for codigo, especie, cfop in (
            ('venda', VENDA, '5102'),
            ('remessa', VENDA_FORA, '5904'),
            ('bonificacao', BONIFICACAO, '5910'),
        ):
            natureza = NaturezaOperacao.objects.create(
                filial=cls.filial, codigo=codigo, descricao=codigo.title(),
                especie=especie,
                exige_destinatario=especie != VENDA_FORA,
            )
            RegraNaturezaOperacao.objects.create(natureza=natureza, cfop=cfop)

    def setUp(self):
        self.client.force_login(self.usuario)
        self.viagem = Viagem.objects.create(
            filial=self.filial, numero=1, motorista_nome='Seu Zé',
            veiculo_placa='ABC1D23', vendedor=self.usuario,
        )
        self.url_detalhe = reverse('logistica:viagem-detail', args=[self.viagem.pk])

    def _incluir(self, especie, **campos):
        base = {'produto': self.produto.pk, 'quantidade': '10', 'valor_unitario': '5'}
        base.update(campos)
        return self.client.post(
            reverse('logistica:viagem-item-create', args=[self.viagem.pk, especie]),
            base, follow=True,
        )

    def _avisos(self, resposta):
        return [str(m) for m in resposta.context['messages']]

    # ── Os três botões ───────────────────────────────────────────────────

    def test_a_tela_oferece_os_tres_botoes(self):
        html = self.client.get(self.url_detalhe).content.decode()

        self.assertIn('Adicionar venda', html)
        self.assertIn('Adicionar venda fora do estabelecimento', html)
        self.assertIn('Adicionar bonificação', html)

    def test_cada_botao_posta_para_a_sua_natureza(self):
        html = self.client.get(self.url_detalhe).content.decode()

        for especie in (VENDA, VENDA_FORA, BONIFICACAO):
            self.assertIn(
                reverse('logistica:viagem-item-create', args=[self.viagem.pk, especie]),
                html,
            )

    def test_a_opcao_vazia_diz_o_que_fazer(self):
        """"---------" não informa nada a quem está montando a carga."""
        html = self.client.get(self.url_detalhe).content.decode()

        self.assertNotIn('---------', html)
        self.assertIn('— escolher produto —', html)

    def test_a_tela_nao_vaza_sintaxe_de_template(self):
        html = self.client.get(self.url_detalhe).content.decode()

        for resto in ('{#', '#}', '{%', '%}'):
            self.assertNotIn(resto, html, 'vazou sintaxe de template no HTML')

    # ── Cada formulário pergunta o que a operação precisa ────────────────

    def test_venda_exige_destinatario(self):
        form = ItemCargaForm(viagem=self.viagem, especie=VENDA)

        self.assertTrue(form.fields['cliente'].required)

    def test_venda_fora_nao_pede_destinatario(self):
        """Ela sai justamente porque ainda não tem comprador."""
        form = ItemCargaForm(viagem=self.viagem, especie=VENDA_FORA)

        self.assertFalse(form.fields['cliente'].required)

    def test_bonificacao_exige_destinatario(self):
        form = ItemCargaForm(viagem=self.viagem, especie=BONIFICACAO)

        self.assertTrue(form.fields['cliente'].required)

    def test_so_a_venda_oferece_vinculo_com_pedido(self):
        html = self.client.get(self.url_detalhe).content.decode()

        self.assertEqual(html.count('name="pedido_venda"'), 1)

    def test_com_uma_natureza_so_o_campo_nao_e_perguntado(self):
        """Escolher entre uma opção só é um clique que não decide nada."""
        form = ItemCargaForm(viagem=self.viagem, especie=VENDA)

        self.assertEqual(form.fields['natureza'].widget.__class__.__name__, 'HiddenInput')
        self.assertIsNotNone(form.natureza_unica)

    def test_com_duas_naturezas_a_escolha_aparece(self):
        """Aí a escolha é real: são CFOPs diferentes."""
        outra = NaturezaOperacao.objects.create(
            filial=self.filial, codigo='venda_st', descricao='Venda com ST',
            especie=VENDA,
        )
        RegraNaturezaOperacao.objects.create(natureza=outra, cfop='5405')

        form = ItemCargaForm(viagem=self.viagem, especie=VENDA)

        self.assertIsNone(form.natureza_unica)
        self.assertNotEqual(
            form.fields['natureza'].widget.__class__.__name__, 'HiddenInput',
        )

    # ── O que entra ──────────────────────────────────────────────────────

    def test_adicionar_venda_com_cliente(self):
        self._incluir(VENDA, cliente=self.cliente.pk)

        item = ItemCarga.objects.get()
        self.assertEqual(item.natureza.especie, VENDA)
        self.assertEqual(item.cliente, self.cliente)
        self.assertEqual(item.quantidade, Decimal('10.000'))

    def test_adicionar_venda_fora_sem_cliente(self):
        self._incluir(VENDA_FORA)

        item = ItemCarga.objects.get()
        self.assertEqual(item.natureza.especie, VENDA_FORA)
        self.assertIsNone(item.cliente_id)

    def test_adicionar_bonificacao_com_destinatario(self):
        self._incluir(BONIFICACAO, cliente=self.cliente.pk)

        item = ItemCarga.objects.get()
        self.assertEqual(item.natureza.especie, BONIFICACAO)
        self.assertEqual(item.cliente, self.cliente)

    def test_as_tres_convivem_na_mesma_carga(self):
        """É o ponto do módulo: um caminhão, três operações separadas."""
        self._incluir(VENDA, cliente=self.cliente.pk, quantidade='150')
        self._incluir(VENDA_FORA, quantidade='200')
        self._incluir(BONIFICACAO, cliente=self.cliente.pk, quantidade='10')

        por_especie = {
            item.natureza.especie: item.quantidade
            for item in ItemCarga.objects.filter(viagem=self.viagem)
        }
        self.assertEqual(por_especie, {
            VENDA: Decimal('150.000'),
            VENDA_FORA: Decimal('200.000'),
            BONIFICACAO: Decimal('10.000'),
        })

    # ── O que não entra ──────────────────────────────────────────────────

    def test_venda_sem_cliente_nao_entra(self):
        resposta = self._incluir(VENDA)

        self.assertEqual(ItemCarga.objects.count(), 0)
        self.assertTrue(any('Destinatário' in a for a in self._avisos(resposta)))

    def test_venda_fora_com_cliente_e_recusada(self):
        """
        Preencher um faria a nota de remessa sair contra alguém que não comprou
        nada.
        """
        resposta = self._incluir(VENDA_FORA, cliente=self.cliente.pk)

        self.assertEqual(ItemCarga.objects.count(), 0)
        self.assertTrue(any('sem comprador' in a for a in self._avisos(resposta)))

    def test_quantidade_zero_nao_entra(self):
        resposta = self._incluir(VENDA, cliente=self.cliente.pk, quantidade='0')

        self.assertEqual(ItemCarga.objects.count(), 0)
        self.assertTrue(any('maior que zero' in a for a in self._avisos(resposta)))

    def test_o_erro_diz_qual_campo_faltou(self):
        """
        "Revise os dados" manda a pessoa procurar sozinha no formulário que ela
        nem tem mais na tela.
        """
        resposta = self._incluir(VENDA, cliente=self.cliente.pk, produto='')

        avisos = self._avisos(resposta)
        self.assertTrue(any('Produto' in a for a in avisos), avisos)

    def test_pedido_de_outro_cliente_e_recusado(self):
        """Senão a carga promete a um a mercadoria que outro comprou."""
        outro = Cliente.objects.create(
            filial=self.filial, razao_social='Cliente B',
            cpf_cnpj='12345678902', uf='RN',
        )
        ClienteFilial.objects.create(cliente=outro, filial=self.filial)
        pedido = PedidoVenda.objects.create(
            filial=self.filial, numero_pedido='PV1', cliente=outro,
            usuario=self.usuario, data_emissao=timezone.now(),
        )

        resposta = self._incluir(
            VENDA, cliente=self.cliente.pk, pedido_venda=pedido.pk,
        )

        self.assertEqual(ItemCarga.objects.count(), 0)
        self.assertTrue(any('Cliente B' in a for a in self._avisos(resposta)))

    def test_operacao_desconhecida_nao_entra(self):
        resposta = self.client.post(
            reverse('logistica:viagem-item-create', args=[self.viagem.pk, 'roubo']),
            {'produto': self.produto.pk, 'quantidade': '1'}, follow=True,
        )

        self.assertEqual(ItemCarga.objects.count(), 0)
        self.assertTrue(any('desconhecida' in a for a in self._avisos(resposta)))

    # ── Remover ──────────────────────────────────────────────────────────

    def test_item_sai_da_carga_enquanto_ela_pode_mudar(self):
        self._incluir(VENDA, cliente=self.cliente.pk)
        item = ItemCarga.objects.get()

        self.client.post(
            reverse('logistica:viagem-item-delete', args=[self.viagem.pk, item.pk]),
            follow=True,
        )

        self.assertEqual(ItemCarga.objects.count(), 0)

    def test_depois_que_a_carga_saiu_os_botoes_somem(self):
        """
        Alterar agora reescreveria o que o documento fiscal já declarou -- e um
        botão que só produz erro é pior que botão nenhum.
        """
        self.viagem.status = Viagem.Status.EM_TRANSITO
        self.viagem.save(update_fields=['status'])

        html = self.client.get(self.url_detalhe).content.decode()

        self.assertNotIn('Incluir na carga', html)

    def test_depois_que_a_carga_saiu_o_item_nao_entra_nem_por_url(self):
        self.viagem.status = Viagem.Status.EM_TRANSITO
        self.viagem.save(update_fields=['status'])

        resposta = self._incluir(VENDA, cliente=self.cliente.pk)

        self.assertEqual(ItemCarga.objects.count(), 0)
        self.assertTrue(any('já saiu' in a for a in self._avisos(resposta)))

    # ── Escopo ───────────────────────────────────────────────────────────

    def test_viagem_de_outra_filial_nao_recebe_carga(self):
        outra = Filial.objects.create(
            empresa=self.empresa, razao_social='Segunda',
            cnpj='31345678000677', uf='RN', cidade='Mossoro',
        )
        alheia = Viagem.objects.create(filial=outra, numero=1, motorista_nome='Zé')

        resposta = self.client.post(
            reverse('logistica:viagem-item-create', args=[alheia.pk, VENDA]),
            {'produto': self.produto.pk, 'quantidade': '1', 'cliente': self.cliente.pk},
        )

        self.assertEqual(resposta.status_code, 404)
        self.assertEqual(ItemCarga.objects.count(), 0)

    def test_o_perfil_de_cada_botao_esta_declarado(self):
        """A tela lê daqui o que perguntar; espécie sem perfil vira tela vazia."""
        for especie in (VENDA, VENDA_FORA, BONIFICACAO):
            self.assertIn(especie, PERFIS)
            self.assertIn('titulo', PERFIS[especie])
            self.assertIn('ajuda', PERFIS[especie])
