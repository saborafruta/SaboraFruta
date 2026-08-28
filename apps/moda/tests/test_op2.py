import json
import re
from decimal import Decimal
from io import BytesIO
from zipfile import ZipFile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.template.loader import get_template
from django.test import TestCase
from django.http import QueryDict
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.moda.models import (
    ArquivoPedido, Grade, ItemGrade, ItemGradePedido, ItemPedidoProducao,
    OpcaoEstruturaOP2, OrdemProducao, PedidoProducao, Personalizacao,
    PersonalizacaoIndividual, ProdutoModa, Tamanho, VisualItemPedido,
)
from apps.moda.forms_cliente import ClienteRapidoForm
from apps.moda.services.op2_estrutura import (
    OP2_ESTRUTURA_OPCOES, juntar_observacoes_item, opcoes_estrutura_filial,
)
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

    def test_nova_op_embute_clientes_como_fallback_da_busca(self):
        cliente_antigo = Cliente.objects.create(
            filial=self.filial,
            tipo_pessoa='F',
            razao_social='Diego Macedo',
            ativo=True,
        )
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

        resposta = self.client.get(reverse('moda:op2-create'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'op2-clientes')
        self.assertContains(resposta, cliente_antigo.razao_social)

    def test_nova_op_renderiza_mapa_de_tamanhos_como_json_valido(self):
        tamanho = Tamanho.objects.create(
            filial=self.filial,
            sigla="G'Especial",
            ordem=10,
        )
        tamanho.refresh_from_db()
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

        resposta = self.client.get(reverse('moda:op2-create'))
        html = resposta.content.decode()
        bloco = re.search(
            r'<script id="op2-tamanhos-labels" type="application/json">(.*?)</script>',
            html,
            re.DOTALL,
        )

        self.assertIsNotNone(bloco)
        self.assertEqual(json.loads(bloco.group(1)), {str(tamanho.pk): tamanho.sigla})
        self.assertContains(
            resposta,
            "tamanhoLabels:JSON.parse(document.getElementById('op2-tamanhos-labels').textContent)",
        )

    def test_cadastro_rapido_da_op_exibe_dados_essenciais_com_um_nome(self):
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

        resposta = self.client.get(reverse('moda:op2-create'))

        self.assertEqual(
            [nome for nome in ClienteRapidoForm().fields if nome in {
                'razao_social', 'nome_fantasia',
            }],
            ['razao_social'],
        )
        for nome in (
            'tipo_pessoa', 'razao_social', 'cpf_cnpj', 'inscricao_estadual',
            'contribuinte_icms', 'contato_nome', 'celular', 'telefone',
            'email', 'cidade', 'uf',
        ):
            self.assertContains(resposta, f'name="{nome}"')
        self.assertNotContains(resposta, 'name="nome_fantasia"')

    def test_cadastro_rapido_salva_cliente_sem_nome_fantasia(self):
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

        resposta = self.client.post(reverse('moda:cliente-criar-json'), {
            'tipo_pessoa': 'F',
            'razao_social': 'Maria da Silva',
            'cpf_cnpj': '12345678901',
            'contato_nome': 'Maria',
            'celular': '84999990000',
            'email': 'maria@example.com',
            'cidade': 'Natal',
            'uf': 'RN',
        })

        self.assertEqual(resposta.status_code, 200)
        cliente = Cliente.objects.get(
            filial=self.filial,
            razao_social='Maria da Silva',
        )
        self.assertEqual(cliente.nome_fantasia, '')
        self.assertEqual(cliente.celular, '84999990000')

    def test_grade_da_nova_op_tem_controles_grandes_e_digitacao_explicita(self):
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

        resposta = self.client.get(reverse('moda:op2-create'))

        self.assertContains(resposta, 'class="op2-size-grid"')
        self.assertContains(resposta, 'op2-qty-input')
        self.assertContains(resposta, 'definirDraftGrade')
        self.assertContains(resposta, 'quantidadeDraftGrade')
        self.assertContains(resposta, '.op2-qty-btn')
        self.assertContains(resposta, 'op2NovaMelhorada()')

    def test_cada_grade_da_nova_op_tem_quantidades_independentes(self):
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

        resposta = self.client.get(reverse('moda:op2-create'))

        self.assertContains(resposta, 'gradePorGrade')
        self.assertContains(resposta, 'gradesSelecionadas()')
        self.assertContains(resposta, 'quantidadeDraftGrade(grade.id,tamanhoId)')
        self.assertContains(resposta, 'Cada grade selecionada possui suas próprias quantidades')

    def test_quantidade_total_da_nova_op_acompanha_soma_das_grades(self):
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

        resposta = self.client.get(reverse('moda:op2-create'))

        self.assertContains(resposta, ':readonly="draft.grades.length>0"')
        self.assertContains(
            resposta,
            'if(this.draft.grades.length)this.draft.quantidade=total;',
        )

    def test_quantidade_total_ao_editar_op_acompanha_soma_das_grades(self):
        self._item(quantidade=5)
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

        resposta = self.client.get(reverse('moda:op2-detail', args=[self.pedido.pk]))

        self.assertContains(resposta, 'this.sincronizarQuantidadeModal()')
        self.assertContains(
            resposta,
            'if(this.draft.grades.length)this.draft.quantidade=this.totalGrades()',
        )

    def test_editor_da_op_permite_selecionar_varias_grades(self):
        self._item(quantidade=5)
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

        resposta = self.client.get(reverse('moda:op2-detail', args=[self.pedido.pk]))

        self.assertContains(
            resposta,
            'this.draft.grades=[...this.draft.grades,id]',
        )
        self.assertNotContains(
            resposta,
            "if(this.modoItem==='editar'){this.draft.grades=[id]",
        )

    def test_grade_e_personalizacao_ficam_dentro_de_cada_produto(self):
        item = self._item(quantidade=5)
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

        resposta = self.client.get(reverse('moda:op2-detail', args=[self.pedido.pk]))

        self.assertContains(resposta, 'op2-item-personalizacao')
        self.assertContains(resposta, f'name="item" value="{item.pk}"')
        self.assertContains(resposta, 'Adicionar nomes / números')
        self.assertContains(resposta, '+ Outro nome / número')
        self.assertContains(resposta, 'Salvar todos')
        self.assertNotContains(resposta, '4. Grade e personalização individual')

    def test_financeiro_inicia_com_total_da_op_e_permite_edicao(self):
        item = self._item(quantidade=5)
        item.valor_unitario = Decimal('12.50')
        item.save(update_fields=['valor_unitario'])
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

        resposta = self.client.get(reverse('moda:op2-detail', args=[self.pedido.pk]))

        self.assertContains(resposta, 'id="op2-valor-total"')
        self.assertContains(
            resposta,
            "Number(JSON.parse(document.getElementById('op2-valor-total').textContent)||0)",
        )
        self.assertContains(resposta, 'O valor atual da OP já vem preenchido e pode ser editado.')
        self.assertContains(resposta, 'name="valor_total"')

    def test_op_mostra_extrato_e_atalho_para_quitar_titulo(self):
        from datetime import date

        from apps.financeiro.constants.enums import StatusContaReceber
        from apps.financeiro.models import ContaReceber, PagamentoContaReceber

        conta = ContaReceber.objects.create(
            filial=self.filial, cliente=self.cliente,
            documento_tipo='pedido_moda', documento_id=self.pedido.pk,
            documento_numero=str(self.pedido.numero), parcela=1, total_parcelas=1,
            valor_original=Decimal('500.00'), valor_final=Decimal('500.00'),
            valor_pago=Decimal('200.00'), valor_saldo=Decimal('300.00'),
            data_emissao=date.today(), data_vencimento=date.today(),
            status=StatusContaReceber.PAGO_PARCIAL,
        )
        PagamentoContaReceber.objects.create(
            filial=self.filial, conta_receber=conta,
            data_pagamento=date.today(), valor_pago=Decimal('200.00'),
        )
        self.pedido.financeiro_gerado_em = timezone.now()
        self.pedido.save(update_fields=['financeiro_gerado_em'])
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

        resposta = self.client.get(reverse('moda:op2-detail', args=[self.pedido.pk]))

        self.assertContains(resposta, 'Extrato de pagamentos')
        self.assertContains(resposta, 'data-payment-status="parcial"')
        self.assertContains(resposta, 'Pagamento parcial')
        self.assertContains(resposta, '@click="extratoFinanceiro=true"')
        self.assertContains(resposta, 'x-show="extratoFinanceiro"')
        self.assertNotContains(resposta, 'data-pane="financeiro"')
        self.assertContains(resposta, 'R$ 200,00')
        self.assertContains(resposta, 'R$ 300,00')
        self.assertContains(resposta, reverse('financeiro:receber_detail', args=[conta.pk]))
        self.assertContains(resposta, reverse('financeiro:receber_baixar', args=[conta.pk]))
        self.assertContains(resposta, 'Quitar saldo')
        self.assertContains(
            self.client.get(reverse('moda:comercial')),
            'data-payment-status="parcial"',
        )

    def test_op_mostra_tag_de_pagamento_pendente(self):
        from datetime import date

        from apps.financeiro.constants.enums import StatusContaReceber
        from apps.financeiro.models import ContaReceber

        ContaReceber.objects.create(
            filial=self.filial, cliente=self.cliente,
            documento_tipo='pedido_moda', documento_id=self.pedido.pk,
            documento_numero=str(self.pedido.numero), parcela=1, total_parcelas=1,
            valor_original=Decimal('500.00'), valor_final=Decimal('500.00'),
            valor_pago=Decimal('0.00'), valor_saldo=Decimal('500.00'),
            data_emissao=date.today(), data_vencimento=date.today(),
            status=StatusContaReceber.ABERTO,
        )
        self.pedido.financeiro_gerado_em = timezone.now()
        self.pedido.save(update_fields=['financeiro_gerado_em'])
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

        resposta = self.client.get(reverse('moda:op2-detail', args=[self.pedido.pk]))

        self.assertContains(resposta, 'data-payment-status="pendente"')
        self.assertContains(resposta, 'Pagamento pendente')
        self.assertContains(
            self.client.get(reverse('moda:comercial')),
            'data-payment-status="pendente"',
        )

    def test_op_mostra_tag_de_pagamento_pago(self):
        from datetime import date

        from apps.financeiro.constants.enums import StatusContaReceber
        from apps.financeiro.models import ContaReceber

        ContaReceber.objects.create(
            filial=self.filial, cliente=self.cliente,
            documento_tipo='pedido_moda', documento_id=self.pedido.pk,
            documento_numero=str(self.pedido.numero), parcela=1, total_parcelas=1,
            valor_original=Decimal('500.00'), valor_final=Decimal('500.00'),
            valor_pago=Decimal('500.00'), valor_saldo=Decimal('0.00'),
            data_emissao=date.today(), data_vencimento=date.today(),
            status=StatusContaReceber.PAGO,
        )
        self.pedido.financeiro_gerado_em = timezone.now()
        self.pedido.save(update_fields=['financeiro_gerado_em'])
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

        resposta = self.client.get(reverse('moda:op2-detail', args=[self.pedido.pk]))

        self.assertContains(resposta, 'data-payment-status="pago"')
        self.assertContains(resposta, '>Pago</span>')
        self.assertContains(
            self.client.get(reverse('moda:comercial')),
            'data-payment-status="pago"',
        )

    def test_tag_pagamento_ignora_titulos_cancelados_e_soma_parcelas(self):
        from datetime import date

        from apps.financeiro.constants.enums import StatusContaReceber
        from apps.financeiro.models import ContaReceber
        from apps.moda.services.financeiro import FinanceiroPedidoService

        for parcela, (pago, saldo, status) in enumerate([
            ('100', '0', StatusContaReceber.PAGO),
            ('0', '100', StatusContaReceber.ABERTO),
            ('0', '100', StatusContaReceber.CANCELADO),
        ], start=1):
            ContaReceber.objects.create(
                filial=self.filial, cliente=self.cliente,
                documento_tipo='pedido_moda', documento_id=self.pedido.pk,
                parcela=parcela, total_parcelas=3,
                valor_original=Decimal('100'), valor_final=Decimal('100'),
                valor_pago=Decimal(pago), valor_saldo=Decimal(saldo),
                data_emissao=date.today(), data_vencimento=date.today(),
                status=status,
            )
        with self.assertNumQueries(1):
            situacoes = FinanceiroPedidoService.situacoes_dos_pedidos(
                [self.pedido], filial=self.filial,
            )
        self.assertEqual(situacoes[self.pedido.pk]['chave'], 'parcial')

        ContaReceber.objects.filter(
            documento_tipo='pedido_moda', documento_id=self.pedido.pk, parcela=2,
        ).update(valor_pago=Decimal('100'), valor_saldo=Decimal('0'),
                 status=StatusContaReceber.PAGO)
        situacoes = FinanceiroPedidoService.situacoes_dos_pedidos(
            [self.pedido], filial=self.filial,
        )
        self.assertEqual(situacoes[self.pedido.pk]['chave'], 'pago')

    def test_nova_op_oferece_multiplas_formas_de_pagamento_previsto(self):
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

        resposta = self.client.get(reverse('moda:op2-create'))

        for rotulo in (
            'Dinheiro', 'Boleto', 'PIX', 'Cartão de débito',
            'Crédito parcelado', 'Crédito à vista',
        ):
            self.assertContains(resposta, rotulo)
        self.assertContains(resposta, '+ Outra forma')
        self.assertContains(resposta, 'não gera lançamentos no financeiro')

    def test_nova_op_salva_divisao_do_pagamento_previsto(self):
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

        resposta = self.client.post(reverse('moda:op2-create'), {
            'cliente': str(self.cliente.pk),
            'item_0_produto_id': str(self.produto.pk),
            'item_0_quantidade': '2',
            'item_0_valor_unitario': '50',
            'pagamento_0_forma': 'pix',
            'pagamento_0_valor': '40.00',
            'pagamento_1_forma': 'credito_parcelado',
            'pagamento_1_valor': '60.00',
        })

        criado = PedidoProducao.objects.exclude(pk=self.pedido.pk).get()
        self.assertRedirects(resposta, reverse('moda:op2-detail', args=[criado.pk]))
        self.assertEqual(criado.previsao_pagamento, [
            {'forma': 'pix', 'valor': '40.00'},
            {'forma': 'credito_parcelado', 'valor': '60.00'},
        ])

    def test_pagamento_previsto_precisa_fechar_o_total_do_orcamento(self):
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

        resposta = self.client.post(reverse('moda:op2-create'), {
            'cliente': str(self.cliente.pk),
            'item_0_produto_id': str(self.produto.pk),
            'item_0_quantidade': '2',
            'item_0_valor_unitario': '50',
            'pagamento_0_forma': 'pix',
            'pagamento_0_valor': '80.00',
        })

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'a soma das formas deve ser igual ao total')
        self.assertFalse(PedidoProducao.objects.exclude(pk=self.pedido.pk).exists())

    def test_clique_no_modelo_atualiza_draft_sem_chamada_indireta(self):
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

        resposta = self.client.get(reverse('moda:op2-create'))

        self.assertContains(resposta, 'this.draft.produto_id=String(id)')
        self.assertContains(resposta, 'this.draft.nome=nome')
        self.assertNotContains(resposta, 'const escolherProduto=estado.escolherProduto.bind')

    def test_nova_op_mostra_previa_de_anexos_e_imagens_por_produto(self):
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

        resposta = self.client.get(reverse('moda:op2-create'))

        self.assertContains(resposta, 'previewAnexos($event)')
        self.assertContains(resposta, 'anexosPreview')
        self.assertContains(resposta, '+ Adicionar imagens')
        self.assertNotContains(resposta, '+ Frente')
        self.assertNotContains(resposta, '+ Costas')

    def test_nova_op_salva_anexo_e_mockup_e_exibe_na_op(self):
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()
        imagem = b'\x89PNG\r\n\x1a\nconteudo-de-teste'

        resposta = self.client.post(reverse('moda:op2-create'), {
            'cliente': str(self.cliente.pk),
            'item_0_produto_id': str(self.produto.pk),
            'item_0_quantidade': '2',
            'item_0_valor_unitario': '10',
            'arquivo': SimpleUploadedFile('arte.png', imagem, content_type='image/png'),
            'mockup_frente_camisa': SimpleUploadedFile(
                'frente.png', imagem, content_type='image/png',
            ),
        })

        criado = PedidoProducao.objects.exclude(pk=self.pedido.pk).get()
        self.assertRedirects(resposta, reverse('moda:op2-detail', args=[criado.pk]))
        self.assertEqual(ArquivoPedido.objects.filter(pedido=criado).count(), 2)
        visual = VisualItemPedido.objects.get(item__pedido=criado)
        detalhe = self.client.get(reverse('moda:op2-detail', args=[criado.pk]))
        self.assertContains(detalhe, visual.url_imagem)

        for arquivo in ArquivoPedido.objects.filter(pedido=criado):
            arquivo.arquivo.delete(save=False)
        visual.imagem.delete(save=False)

    def test_nova_op_pode_salvar_e_abrir_orcamento_pdf(self):
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

        resposta = self.client.post(reverse('moda:op2-create'), {
            'cliente': str(self.cliente.pk),
            'item_0_produto_id': str(self.produto.pk),
            'item_0_quantidade': '2',
            'item_0_valor_unitario': '25.50',
            'destino': 'pdf',
        })

        criado = PedidoProducao.objects.exclude(pk=self.pedido.pk).get()
        url_pdf = reverse('moda:pedido-orcamento-pdf', args=[criado.pk])
        self.assertRedirects(resposta, url_pdf, fetch_redirect_response=False)

        pdf = self.client.get(url_pdf)
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf['Content-Type'], 'application/pdf')
        self.assertTrue(pdf.content.startswith(b'%PDF-'))

    def test_salvar_e_enviar_persiste_op_e_abre_whatsapp_na_nova_guia(self):
        self.cliente.telefone = '84999990000'
        self.cliente.save(update_fields=['telefone'])
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

        resposta = self.client.post(reverse('moda:op2-create'), {
            'cliente': str(self.cliente.pk),
            'item_0_produto_id': str(self.produto.pk),
            'item_0_quantidade': '2',
            'item_0_valor_unitario': '25.50',
            'destino': 'enviar',
        })

        criado = PedidoProducao.objects.exclude(pk=self.pedido.pk).get()
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'https://wa.me/5584999990000')
        self.assertContains(resposta, reverse('moda:op2-detail', args=[criado.pk]))
        self.assertContains(resposta, 'window.opener.location.assign')
        self.assertEqual(criado.itens.count(), 1)

    def test_detalhe_da_op_exibe_produto_compacto_e_total_no_cabecalho(self):
        self._item(quantidade=4)
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

        resposta = self.client.get(reverse('moda:op2-detail', args=[self.pedido.pk]))

        self.assertContains(resposta, 'class="op2-order-total"')
        self.assertContains(resposta, 'Total do pedido')
        self.assertContains(resposta, 'class="op2-item-tools"')
        self.assertContains(resposta, 'Detalhes técnicos')
        self.assertContains(resposta, 'x-show="gradeEdit"')
        self.assertContains(resposta, 'personalizar:false,pessoas:[')
        self.assertContains(resposta, 'op2-detail-itens')
        self.assertContains(resposta, 'op2WorkspaceCompleto()')
        self.assertContains(resposta, "abrirEditarProduto('")
        self.assertContains(resposta, 'abrirNovoProduto()')
        self.assertContains(resposta, 'rel="noopener"')
        self.assertContains(resposta, 'op2-order-header')
        self.assertContains(resposta, '<h2 class="font-bold">Fotos e mockups</h2>')
        self.assertContains(resposta, 'op2-aside .op2-gallery-grid')
        self.assertContains(resposta, 'Todos os produtos acompanham o status geral da OP.')
        self.assertNotContains(resposta, 'Quantidade entregue')
        self.assertNotContains(resposta, 'Atualizar entrega')
        self.assertNotContains(resposta, 'name="acao" value="item_fluxo"')

    def test_status_entregue_confirma_entrega_da_op_inteira(self):
        primeiro = self._item(quantidade=4, entregue=0)
        segundo = self._item(quantidade=3, entregue=1)
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

        resposta = self.client.post(reverse('moda:op2-action', args=[self.pedido.pk]), {
            'acao': 'status',
            'status': PedidoProducao.Status.ENTREGUE,
        })

        self.assertRedirects(resposta, reverse('moda:op2-detail', args=[self.pedido.pk]))
        primeiro.refresh_from_db()
        segundo.refresh_from_db()
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.status, PedidoProducao.Status.ENTREGUE)
        self.assertEqual(primeiro.quantidade_entregue, primeiro.quantidade)
        self.assertEqual(segundo.quantidade_entregue, segundo.quantidade)

    def test_nova_op_exibe_acoes_solicitadas(self):
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

        resposta = self.client.get(reverse('moda:op2-create'))

        self.assertContains(resposta, '>Salvar</button>')
        self.assertContains(resposta, 'Salvar e enviar para cliente')
        self.assertContains(resposta, 'value="enviar" formtarget="_blank"')
        self.assertContains(resposta, 'Cancelar OP')
        self.assertNotContains(resposta, 'Salvar e abrir orçamento PDF')
        self.assertContains(resposta, 'body.tema-claro .app-topbar')
        self.assertNotContains(resposta, 'body.tema-claro header {')
        self.assertContains(resposta, '<div x-cloak class="grid sm:grid-cols-2')

    def test_modelo_carrega_tipo_de_impressao_no_editor(self):
        self.produto.tipo_impressao = ProdutoModa.TipoImpressao.SILK
        self.produto.save(update_fields=['tipo_impressao'])
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

        resposta = self.client.get(reverse('moda:op2-detail', args=[self.pedido.pk]))

        self.assertContains(resposta, '"tipo_impressao": "silk"')
        self.assertContains(resposta, 'name="tipo_impressao"')

    def test_mockup_permite_varias_imagens_na_mesma_posicao_e_download_zip(self):
        item = self._item(quantidade=2)
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()
        imagem = b'\x89PNG\r\n\x1a\nconteudo-de-teste'

        for indice in range(2):
            resposta = self.client.post(reverse('moda:op2-action', args=[self.pedido.pk]), {
                'acao': 'visual_item',
                'item_id': str(item.pk),
                'posicao': 'frente_camisa',
                'imagem': SimpleUploadedFile(
                    f'frente-{indice}.png', imagem, content_type='image/png',
                ),
            })
            self.assertRedirects(
                resposta, reverse('moda:op2-detail', args=[self.pedido.pk]),
            )

        self.assertEqual(VisualItemPedido.objects.filter(item=item).count(), 2)
        pacote = self.client.get(reverse('moda:op2-anexos-zip', args=[self.pedido.pk]))
        self.assertEqual(pacote.status_code, 200)
        self.assertEqual(pacote['Content-Type'], 'application/zip')
        with ZipFile(BytesIO(pacote.content)) as zip_file:
            self.assertEqual(len(zip_file.namelist()), 2)

        for visual in VisualItemPedido.objects.filter(item=item):
            visual.imagem.delete(save=False)

    def test_personalizacao_risca_e_bloqueia_tamanho_esgotado(self):
        tamanho = Tamanho.objects.create(filial=self.filial, sigla='M', ordem=10)
        item = self._item(quantidade=1)
        ItemGradePedido.objects.create(item=item, tamanho=tamanho, quantidade=1)
        PersonalizacaoIndividual.objects.create(
            pedido=self.pedido, item=item, tamanho=tamanho, nome='SILVA',
        )
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

        resposta = self.client.get(reverse('moda:op2-detail', args=[self.pedido.pk]))

        self.assertContains(resposta, '.op2-vaga.is-esgotada')
        self.assertContains(resposta, '"restam": 0')
        self.assertContains(
            resposta,
            'String(pessoa.tamanhoId)!==String(vaga.id)',
        )

    def test_op_existente_salva_varios_nomes_em_um_unico_envio(self):
        tamanho = Tamanho.objects.create(filial=self.filial, sigla='M', ordem=10)
        item = self._item(quantidade=2)
        ItemGradePedido.objects.create(item=item, tamanho=tamanho, quantidade=2)
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

        resposta = self.client.post(reverse('moda:op2-action', args=[self.pedido.pk]), {
            'acao': 'individuais',
            'item': str(item.pk),
            'individual_0_tamanho': str(tamanho.pk),
            'individual_0_nome': 'ANA',
            'individual_0_numero': '7',
            'individual_1_tamanho': str(tamanho.pk),
            'individual_1_nome': 'BIA',
            'individual_1_numero': '10',
        })

        self.assertRedirects(resposta, reverse('moda:op2-detail', args=[self.pedido.pk]))
        self.assertEqual(
            list(item.individuais.order_by('ordem').values_list('nome', 'numero')),
            [('ANA', '7'), ('BIA', '10')],
        )

    def test_op_existente_atualiza_pagamento_previsto_sem_gerar_financeiro(self):
        self._item(quantidade=2)
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

        resposta = self.client.post(reverse('moda:op2-action', args=[self.pedido.pk]), {
            'acao': 'previsao_pagamento',
            'pagamento_0_forma': 'dinheiro',
            'pagamento_0_valor': '25.00',
            'pagamento_1_forma': 'pix',
            'pagamento_1_valor': '75.00',
        })

        self.assertRedirects(resposta, reverse('moda:op2-detail', args=[self.pedido.pk]))
        self.pedido.refresh_from_db()
        self.assertEqual(len(self.pedido.previsao_pagamento), 2)
        self.assertIsNone(self.pedido.financeiro_gerado_em)

    def test_editor_completo_atualiza_estrutura_impressao_e_grade(self):
        tamanho_p = Tamanho.objects.create(filial=self.filial, sigla='P', ordem=10)
        tamanho_m = Tamanho.objects.create(filial=self.filial, sigla='M', ordem=20)
        grade = Grade.objects.create(filial=self.filial, nome='Adulto')
        ItemGrade.objects.create(grade=grade, tamanho=tamanho_p, ordem=10)
        ItemGrade.objects.create(grade=grade, tamanho=tamanho_m, ordem=20)
        self.produto.grade = grade
        self.produto.save(update_fields=['grade'])
        item = self._item(quantidade=1)
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

        resposta = self.client.post(reverse('moda:op2-action', args=[self.pedido.pk]), {
            'acao': 'editar_item',
            'item_id': str(item.pk),
            'produto_id': str(self.produto.pk),
            'grades': str(grade.pk),
            f'grade_{grade.pk}_{tamanho_p.pk}': '2',
            f'grade_{grade.pk}_{tamanho_m.pk}': '3',
            'valor_unitario': '59.90',
            'referencia': 'REF-EDITADA',
            'acabamento': 'Barra reforçada',
            'estrutura_tipo': 'camisa',
            'estrutura_malha': 'DRYTECH',
            'estrutura_gola': 'POLO',
            'arte_tipo': 'arte',
            'arte_tecnica': 'silk',
            'arte_local': 'Peito',
            'arte_observacoes': 'Duas cores',
            'item_observacoes': 'Observação livre',
        })

        self.assertRedirects(resposta, reverse('moda:op2-detail', args=[self.pedido.pk]))
        item.refresh_from_db()
        self.assertEqual(item.quantidade, 5)
        self.assertEqual(item.grade_tamanho, grade)
        self.assertIn('Malha: DRYTECH', item.observacoes)
        self.assertIn('Gola: POLO', item.observacoes)
        self.assertEqual(
            list(item.grade.order_by('tamanho__ordem').values_list('quantidade', flat=True)),
            [2, 3],
        )
        arte = Personalizacao.objects.get(item=item)
        self.assertEqual((arte.tecnica, arte.local, arte.observacoes), ('silk', '', ''))

    def test_editor_completo_adiciona_um_item_por_grade(self):
        tamanho = Tamanho.objects.create(filial=self.filial, sigla='G', ordem=10)
        adulto = Grade.objects.create(filial=self.filial, nome='Adulto')
        oversized = Grade.objects.create(filial=self.filial, nome='OverSized')
        ItemGrade.objects.create(grade=adulto, tamanho=tamanho, ordem=10)
        ItemGrade.objects.create(grade=oversized, tamanho=tamanho, ordem=10)
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

        resposta = self.client.post(reverse('moda:op2-action', args=[self.pedido.pk]), {
            'acao': 'adicionar_item',
            'produto_id': str(self.produto.pk),
            'grades': [str(adulto.pk), str(oversized.pk)],
            f'grade_{adulto.pk}_{tamanho.pk}': '2',
            f'grade_{oversized.pk}_{tamanho.pk}': '4',
            'quantidade': '6',
            'valor_unitario': '40',
            'estrutura_tipo': 'camisa',
        })

        self.assertRedirects(resposta, reverse('moda:op2-detail', args=[self.pedido.pk]))
        adicionados = list(self.pedido.itens.order_by('grade_tamanho__nome'))
        self.assertEqual(len(adicionados), 2)
        self.assertEqual(
            [(item.grade_tamanho.nome, item.quantidade) for item in adicionados],
            [('Adulto', 2), ('OverSized', 4)],
        )

    def test_editor_completo_adiciona_nova_linha_ao_incluir_outra_grade(self):
        tamanho = Tamanho.objects.create(filial=self.filial, sigla='G1', ordem=10)
        adulto = Grade.objects.create(filial=self.filial, nome='Adulto')
        oversized = Grade.objects.create(filial=self.filial, nome='OverSized')
        ItemGrade.objects.create(grade=adulto, tamanho=tamanho, ordem=10)
        ItemGrade.objects.create(grade=oversized, tamanho=tamanho, ordem=10)
        item = self._item(quantidade=2)
        item.grade_tamanho = adulto
        item.save(update_fields=['grade_tamanho'])
        ItemGradePedido.objects.create(item=item, tamanho=tamanho, quantidade=2)
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

        resposta = self.client.post(reverse('moda:op2-action', args=[self.pedido.pk]), {
            'acao': 'editar_item',
            'item_id': str(item.pk),
            'produto_id': str(self.produto.pk),
            'grades': [str(adulto.pk), str(oversized.pk)],
            f'grade_{adulto.pk}_{tamanho.pk}': '2',
            f'grade_{oversized.pk}_{tamanho.pk}': '4',
            'quantidade': '6',
            'valor_unitario': '40',
            'estrutura_tipo': 'camisa',
        })

        self.assertRedirects(resposta, reverse('moda:op2-detail', args=[self.pedido.pk]))
        itens = list(self.pedido.itens.order_by('grade_tamanho__nome'))
        self.assertEqual(
            [(linha.grade_tamanho.nome, linha.quantidade) for linha in itens],
            [('Adulto', 2), ('OverSized', 4)],
        )

    def test_excluir_item_com_ordem_de_producao_nao_retorna_erro_500(self):
        item = self._item(quantidade=3)
        ordem = OrdemProducao.objects.create(
            filial=self.filial,
            pedido=self.pedido,
            item=item,
            ano=2026,
            sequencial=4,
            quantidade=3,
        )
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

        resposta = self.client.post(
            reverse('moda:op2-action', args=[self.pedido.pk]),
            {'acao': 'remover_item', 'item_id': str(item.pk)},
            follow=True,
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(ItemPedidoProducao.objects.filter(pk=item.pk).exists())
        self.assertContains(resposta, ordem.numero)
        self.assertContains(resposta, 'não pode ser excluído')

    def test_editor_completo_mantem_quantidade_quando_item_nao_tem_grade(self):
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

        resposta = self.client.post(reverse('moda:op2-action', args=[self.pedido.pk]), {
            'acao': 'adicionar_item',
            'produto_id': str(self.produto.pk),
            'quantidade': '7',
            'valor_unitario': '35',
            'estrutura_tipo': 'camisa',
        })

        self.assertRedirects(resposta, reverse('moda:op2-detail', args=[self.pedido.pk]))
        item = self.pedido.itens.get()
        self.assertEqual(item.quantidade, 7)
        self.assertIsNone(item.grade_tamanho)
        self.assertFalse(item.grade.exists())

    def test_editor_completo_remove_personalizacao_ao_limpar_os_campos(self):
        item = self._item(quantidade=2)
        Personalizacao.objects.create(
            item=item,
            tipo=Personalizacao.Tipo.ARTE,
            tecnica=Personalizacao.Tecnica.SUBLIMACAO,
            local='Peito',
            observacoes='Arte antiga',
        )
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

        resposta = self.client.post(reverse('moda:op2-action', args=[self.pedido.pk]), {
            'acao': 'editar_item',
            'item_id': str(item.pk),
            'produto_id': str(self.produto.pk),
            'quantidade': '2',
            'valor_unitario': '50',
            'estrutura_tipo': 'camisa',
            'arte_tipo': '',
            'arte_tecnica': '',
            'arte_local': '',
            'arte_observacoes': '',
        })

        self.assertRedirects(resposta, reverse('moda:op2-detail', args=[self.pedido.pk]))
        self.assertFalse(Personalizacao.objects.filter(item=item).exists())

    def test_tipos_de_peca_abre_um_tipo_por_vez(self):
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

        resposta = self.client.get(
            reverse('moda:op2-tipos-peca'),
            {'tipo': 'agasalho'},
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context['tipo_selecionado']['slug'], 'agasalho')
        self.assertContains(resposta, 'Adicionar opção em Agasalho')
        self.assertContains(resposta, 'class="tipo-option-row"')
        self.assertContains(resposta, 'Opções ativas / total')
        self.assertContains(resposta, 'name="opcao_texto"')
        self.assertNotContains(resposta, 'name="valor"')

    def test_edicao_do_tipo_atualiza_nome_de_todas_as_opcoes(self):
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()
        self.client.get(reverse('moda:op2-tipos-peca'))

        resposta = self.client.post(reverse('moda:op2-tipos-peca'), {
            'acao': 'editar_tipo',
            'tipo_peca': 'agasalho',
            'tipo_atual': 'agasalho',
            'tipo_label': 'Agasalho esportivo',
        })

        self.assertRedirects(
            resposta,
            f"{reverse('moda:op2-tipos-peca')}?tipo=agasalho",
        )
        labels = set(OpcaoEstruturaOP2.objects.filter(
            filial=self.filial,
            tipo_peca='agasalho',
        ).values_list('tipo_label', flat=True))
        self.assertEqual(labels, {'Agasalho esportivo'})

    def test_sincronizacao_completa_banco_parcial_com_todas_as_variaveis(self):
        OpcaoEstruturaOP2.objects.create(
            filial=self.filial, tipo_peca='camisa', tipo_label='Camisa',
            campo='malha', valor='OPÇÃO PERSONALIZADA', ordem=999,
        )

        grupos = opcoes_estrutura_filial(self.filial)

        self.assertEqual(set(grupos), set(OP2_ESTRUTURA_OPCOES))
        for tipo, padrao in OP2_ESTRUTURA_OPCOES.items():
            self.assertEqual(
                set(padrao['campos']), set(grupos[tipo]['campos']),
                msg=f'Campos incompletos em {tipo}',
            )
            self.assertEqual(
                grupos[tipo]['campos']['tipo_impressao'],
                padrao['campos']['tipo_impressao'],
            )
        self.assertIn('OPÇÃO PERSONALIZADA', grupos['camisa']['campos']['malha'])

    def test_sincronizacao_remove_opcoes_vazias_e_recria_valores_reais(self):
        OpcaoEstruturaOP2.objects.create(
            filial=self.filial, tipo_peca='agasalho', tipo_label='Agasalho',
            campo='tipo_impressao', valor='   ', ordem=1,
        )

        grupos = opcoes_estrutura_filial(self.filial)

        self.assertFalse(OpcaoEstruturaOP2.objects.filter(
            filial=self.filial, valor__regex=r'^\s*$',
        ).exists())
        self.assertEqual(
            grupos['agasalho']['campos']['tipo_impressao'],
            OP2_ESTRUTURA_OPCOES['agasalho']['campos']['tipo_impressao'],
        )

    def test_orcamento_personalizacao_fica_dentro_de_cada_produto(self):
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

        resposta = self.client.get(reverse('moda:op2-create'))

        self.assertContains(resposta, 'op2-item-personalizacao')
        self.assertContains(resposta, 'Personalizações deste produto')
        self.assertContains(resposta, 'personalizacoesDoItem(item.uid)')
        self.assertContains(resposta, '+ Outro nome / número')
        self.assertNotContains(resposta, 'individualDraft')
        self.assertNotContains(resposta, '4. Grade de personalização')

    def test_orcamento_vincula_personalizacoes_a_itens_distintos_do_mesmo_modelo(self):
        tamanho = Tamanho.objects.create(filial=self.filial, sigla='M', ordem=10)
        grade = Grade.objects.create(filial=self.filial, nome='Adulto')
        ItemGrade.objects.create(grade=grade, tamanho=tamanho, ordem=10)
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()
        dados = {'cliente': str(self.cliente.pk)}
        for idx, nome in enumerate(['ANA', 'BIA']):
            dados.update({
                f'item_{idx}_produto_id': str(self.produto.pk),
                f'item_{idx}_grade_id': str(grade.pk),
                f'item_{idx}_grade_{tamanho.pk}': '1',
                f'item_{idx}_quantidade': '1',
                f'item_{idx}_valor_unitario': '25',
                f'individual_{idx}_item_idx': str(idx),
                f'individual_{idx}_tamanho_id': str(tamanho.pk),
                f'individual_{idx}_nome': nome,
                f'individual_{idx}_numero': str(idx + 1),
            })

        resposta = self.client.post(reverse('moda:op2-create'), dados)

        criado = PedidoProducao.objects.exclude(pk=self.pedido.pk).get()
        self.assertRedirects(resposta, reverse('moda:op2-detail', args=[criado.pk]))
        itens = list(criado.itens.order_by('pk'))
        self.assertEqual(len(itens), 2)
        self.assertEqual(itens[0].individuais.get().nome, 'ANA')
        self.assertEqual(itens[1].individuais.get().nome, 'BIA')

    def test_nova_op_salva_grade_de_personalizacao_do_orcamento(self):
        tamanho = Tamanho.objects.create(filial=self.filial, sigla='M', ordem=10)
        grade = Grade.objects.create(filial=self.filial, nome='Adulto')
        ItemGrade.objects.create(grade=grade, tamanho=tamanho, ordem=10)
        self.produto.grade = grade
        self.produto.save(update_fields=['grade'])
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

        resposta = self.client.post(reverse('moda:op2-create'), {
            'cliente': str(self.cliente.pk),
            'item_0_produto_id': str(self.produto.pk),
            'item_0_grade_id': str(grade.pk),
            f'item_0_grade_{tamanho.pk}': '1',
            'item_0_quantidade': '1',
            'item_0_valor_unitario': '25',
            'individual_0_item_idx': '0',
            'individual_0_tamanho_id': str(tamanho.pk),
            'individual_0_nome': 'DIEGO',
            'individual_0_numero': '10',
        })

        criado = PedidoProducao.objects.exclude(pk=self.pedido.pk).get()
        self.assertRedirects(resposta, reverse('moda:op2-detail', args=[criado.pk]))
        pessoa = PersonalizacaoIndividual.objects.get(pedido=criado)
        self.assertEqual((pessoa.tamanho, pessoa.nome, pessoa.numero), (tamanho, 'DIEGO', '10'))
        pdf = self.client.get(reverse('moda:pedido-orcamento-pdf', args=[criado.pk]))
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b'%PDF-'))
        self.assertIn('no-store', pdf['Cache-Control'])

    def test_cancelamento_permanece_mesmo_apos_sincronizar_itens(self):
        self._item(status=ItemPedidoProducao.StatusFluxo.ENTREGUE, entregue=10)
        self.pedido.status = PedidoProducao.Status.ENTREGUE
        self.pedido.save(update_fields=['status'])
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

        resposta = self.client.post(
            reverse('moda:op2-action', args=[self.pedido.pk]), {'acao': 'cancelar'},
        )

        self.assertRedirects(resposta, reverse('moda:op2-detail', args=[self.pedido.pk]))
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.status, PedidoProducao.Status.CANCELADO)
        _sincronizar_status(self.pedido)
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.status, PedidoProducao.Status.CANCELADO)

    def test_galeria_separa_produtos_e_permite_remover_imagem(self):
        item = self._item(quantidade=1)
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()
        imagem = b'\x89PNG\r\n\x1a\nconteudo-de-teste'
        self.client.post(reverse('moda:op2-action', args=[self.pedido.pk]), {
            'acao': 'visual_item', 'item_id': str(item.pk),
            'imagens': SimpleUploadedFile('produto.png', imagem, content_type='image/png'),
        })
        visual = VisualItemPedido.objects.get(item=item)

        detalhe = self.client.get(reverse('moda:op2-detail', args=[self.pedido.pk]))
        self.assertContains(detalhe, 'Imagens separadas por produto')
        self.assertContains(detalhe, 'name="imagens"')
        self.assertContains(detalhe, 'value="remover_visual"')
        self.assertNotContains(detalhe, '+ Frente da camisa')
        self.assertNotContains(detalhe, '+ Costas da camisa')

        resposta = self.client.post(reverse('moda:op2-action', args=[self.pedido.pk]), {
            'acao': 'remover_visual', 'visual_id': str(visual.pk),
        })
        self.assertRedirects(resposta, reverse('moda:op2-detail', args=[self.pedido.pk]))
        self.assertFalse(VisualItemPedido.objects.filter(pk=visual.pk).exists())
        self.assertFalse(ArquivoPedido.objects.filter(pedido=self.pedido).exists())

    def test_cadastro_rapido_cria_modelo_ativo_so_com_nome(self):
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

        resposta = self.client.post(
            reverse('moda:op2-modelo-rapido'), {'nome': 'Camisa especial'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(resposta.status_code, 200)
        dados = resposta.json()['modelo']
        produto = ProdutoModa.objects.get(pk=dados['id'])
        self.assertEqual(produto.nome, 'Camisa especial')
        self.assertEqual(produto.status, ProdutoModa.Status.ATIVO)
        self.assertTrue(produto.ativo)

    def test_sincronizacao_repara_valor_em_branco_do_tipo_de_peca(self):
        opcao = OpcaoEstruturaOP2.objects.create(
            filial=self.filial, tipo_peca='camisa', tipo_label='Camisa',
            campo='tipo_impressao', valor='   ', ordem=1,
        )

        grupos = opcoes_estrutura_filial(self.filial)

        opcao.refresh_from_db()
        self.assertEqual(opcao.valor, 'SUBLIMAÇÃO')
        self.assertIn('SUBLIMAÇÃO', grupos['camisa']['campos']['tipo_impressao'])

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
