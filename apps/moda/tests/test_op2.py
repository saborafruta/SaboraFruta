from decimal import Decimal

from django.template.loader import get_template
from django.test import TestCase
from django.http import QueryDict
from django.urls import reverse

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.moda.models import Grade, ItemGrade, ItemPedidoProducao, PedidoProducao, ProdutoModa, Tamanho
from apps.moda.services.op2_estrutura import juntar_observacoes_item
from apps.moda.services.kanban_comercial import COLUNAS
from apps.moda.views_op2 import Op2CreateView, _sincronizar_status


class Op2Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        empresa = Empresa.objects.create(
            razao_social='OP 2 LTDA', nome_fantasia='OP 2', cnpj='53345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=empresa, razao_social='OP 2 LTDA', cnpj='53345678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.perfil = PerfilAcesso.objects.create(
            empresa=empresa, nome='Administrador OP 2', is_admin=True,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, tipo_pessoa='J', razao_social='Cliente OP 2', ativo=True,
        )

    def setUp(self):
        self.pedido = PedidoProducao.objects.create(
            filial=self.filial, cliente=self.cliente,
        )
        self.produto = ProdutoModa.objects.create(
            filial=self.filial, codigo='OP2001', nome='Camisa personalizada',
        )

    def _item(self, quantidade=10, status='orcamento', entregue=0):
        return ItemPedidoProducao.objects.create(
            pedido=self.pedido, produto=self.produto, quantidade=quantidade,
            valor_unitario=Decimal('50'), status_fluxo=status,
            quantidade_entregue=entregue,
        )

    def test_entrega_parcial_mantem_op_na_etapa_do_item_pendente(self):
        self._item(status=ItemPedidoProducao.StatusFluxo.ENTREGUE, entregue=10)
        pendente = self._item(status=ItemPedidoProducao.StatusFluxo.PRODUCAO)

        _sincronizar_status(self.pedido)
        self.pedido.refresh_from_db()

        self.assertTrue(self.pedido.entrega_parcial)
        self.assertEqual(self.pedido.status, PedidoProducao.Status.EM_PRODUCAO)
        pendente.status_fluxo = ItemPedidoProducao.StatusFluxo.PRONTO
        pendente.save(update_fields=['status_fluxo'])
        _sincronizar_status(self.pedido)
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.status, PedidoProducao.Status.PRONTO)

    def test_todos_os_produtos_entregues_encerram_op(self):
        self._item(status=ItemPedidoProducao.StatusFluxo.ENTREGUE, entregue=10)
        self._item(status=ItemPedidoProducao.StatusFluxo.ENTREGUE, entregue=10)

        _sincronizar_status(self.pedido)
        self.pedido.refresh_from_db()

        self.assertEqual(self.pedido.status, PedidoProducao.Status.ENTREGUE)
        self.assertFalse(self.pedido.entrega_parcial)

    def test_rotas_e_templates_da_versao_nova_sao_separados(self):
        self.assertEqual(reverse('moda:op2-create'), '/moda/comercial/op-2/novo/')
        self.assertIn('/op-2/', reverse('moda:op2-detail', args=[self.pedido.pk]))
        get_template('moda/op2_create.html')
        get_template('moda/op2_detail.html')

    def test_kanban_nao_oferece_coluna_aguardando_material(self):
        self.assertNotIn('material', [coluna.chave for coluna in COLUNAS])
        self.assertIn('Pronto para retirada', [coluna.label for coluna in COLUNAS])

    def test_op2_carrega_grade_cadastrada_do_modelo_de_producao(self):
        tamanho_p = Tamanho.objects.create(filial=self.filial, sigla='P', ordem=10)
        tamanho_m = Tamanho.objects.create(filial=self.filial, sigla='M', ordem=20)
        grade = Grade.objects.create(filial=self.filial, nome='Adulto')
        ItemGrade.objects.create(grade=grade, tamanho=tamanho_p, ordem=10)
        ItemGrade.objects.create(grade=grade, tamanho=tamanho_m, ordem=20)
        self.produto.grade = grade
        self.produto.save(update_fields=['grade'])
        item = self._item(quantidade=12)

        criadas = Op2CreateView._copiar_grade_do_modelo(item)

        self.assertEqual(criadas, 2)
        self.assertEqual(
            list(item.grade.order_by('tamanho__ordem').values_list('tamanho__sigla', 'quantidade')),
            [('P', 0), ('M', 0)],
        )
        item.refresh_from_db()
        self.assertEqual(item.quantidade, 12)

    def test_quantidades_da_nova_op_ignoram_zeros_ate_usuario_preencher(self):
        item = self._item(quantidade=7)
        tamanho_p = Tamanho.objects.create(filial=self.filial, sigla='P', ordem=10)
        tamanho_m = Tamanho.objects.create(filial=self.filial, sigla='M', ordem=20)
        post = QueryDict('', mutable=True)
        post.update({f'grade_{tamanho_p.pk}': '0', f'grade_{tamanho_m.pk}': '3'})
        request = type('Request', (), {'POST': post})()

        self.assertEqual(Op2CreateView._quantidades_grade(request, item), {(item.pk, tamanho_m.pk): 3})
        self.assertEqual(
            Op2CreateView._quantidades_grade(request, item, incluir_zeros=True),
            {(item.pk, tamanho_p.pk): 0, (item.pk, tamanho_m.pk): 3},
        )

    def test_nova_op_aceita_itens_indexados_com_mais_de_um_modelo(self):
        post = QueryDict('', mutable=True)
        post.update({
            'item_0_produto_id': str(self.produto.pk),
            'item_0_quantidade': '2',
            'item_1_produto_id': str(self.produto.pk),
            'item_1_quantidade': '3',
        })
        request = type('Request', (), {'POST': post})()

        self.assertEqual(Op2CreateView._indices_itens(request), [0, 1])
        self.assertEqual(Op2CreateView._dados_item(request, 0)['produto'], f'moda:{self.produto.pk}')
        self.assertEqual(Op2CreateView._dados_item(request, 1)['quantidade'], '3')

    def test_post_da_nova_op_sem_cliente_nao_estoura_500(self):
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

        resposta = self.client.post(reverse('moda:op2-create'), {'cliente': ''})

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Selecione um cliente')

    def test_busca_da_op_encontra_cliente_antigo_sem_vinculo_novo(self):
        cliente_antigo = Cliente.objects.create(
            filial=self.filial,
            tipo_pessoa='F',
            razao_social='Diego Macedo',
            celular='849944149438',
            ativo=True,
        )
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

        resposta = self.client.get(
            reverse('moda:cliente-buscar'),
            {'q': 'Macedo Diego'},
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, cliente_antigo.razao_social)
        self.assertEqual(resposta.json()['clientes'][0]['id'], cliente_antigo.pk)

    def _usuario(self):
        user, _ = Usuario.objects.get_or_create(
            email='op2@teste.local',
            defaults={
                'nome': 'Usuario OP 2',
                'empresa': self.filial.empresa,
                'filial': self.filial,
                'perfil': self.perfil,
            },
        )
        user.is_staff = True
        user.is_superuser = True
        user.save()
        return user

    def test_quantidades_indexadas_da_nova_op_somam_grade_do_item_correto(self):
        item = self._item(quantidade=7)
        tamanho_p = Tamanho.objects.create(filial=self.filial, sigla='PP', ordem=5)
        tamanho_m = Tamanho.objects.create(filial=self.filial, sigla='GG', ordem=30)
        post = QueryDict('', mutable=True)
        post.update({
            f'item_2_grade_{tamanho_p.pk}': '1',
            f'item_2_grade_{tamanho_m.pk}': '4',
        })
        request = type('Request', (), {'POST': post})()

        self.assertEqual(Op2CreateView._total_grade(request, 2), 5)
        self.assertEqual(
            Op2CreateView._quantidades_grade(request, item, prefixos=('item_2_grade_',)),
            {(item.pk, tamanho_p.pk): 1, (item.pk, tamanho_m.pk): 4},
        )

    def test_estrutura_da_planilha_entra_nas_observacoes_do_item(self):
        post = QueryDict('', mutable=True)
        post.update({
            'estrutura_tipo': 'camisa',
            'estrutura_malha': 'DRYTECH',
            'estrutura_gola': 'POLO',
            'estrutura_manga': 'CURTA',
        })

        resumo = juntar_observacoes_item('Observação livre', post)

        self.assertIn('Observação livre', resumo)
        self.assertIn('Estrutura da peça', resumo)
        self.assertIn('Malha: DRYTECH', resumo)
        self.assertIn('Gola: POLO', resumo)
        self.assertIn('Manga: CURTA', resumo)
