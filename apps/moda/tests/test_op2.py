import json
import re
from datetime import timedelta
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
    AprovacaoPedido, ArquivoPedido, Grade, ItemGrade, ItemGradePedido,
    ItemPedidoProducao,
    OpcaoEstruturaOP2, OrdemProducao, PedidoProducao, Personalizacao,
    PersonalizacaoIndividual, ProdutoModa, RegistroCriacaoArte, Tamanho,
    VisualItemPedido,
)
from apps.moda.forms_cliente import ClienteRapidoForm
from apps.moda.services.op2_estrutura import (
    OP2_ESTRUTURA_OPCOES, juntar_observacoes_item, opcoes_estrutura_filial,
)
from apps.moda.services.kanban_comercial import COLUNAS
from apps.moda.views_op2 import Op2CreateView, _sincronizar_status


class Op2Tests(TestCase):
    @staticmethod
    def _modelo_completo(prefixo=''):
        """Escolhas explícitas obrigatórias, inclusive quando não se aplicam."""
        dados = {'estrutura_tipo': 'camisa', 'valor_unitario': '10.00'}
        dados.update({f'estrutura_{campo}': 'N/A' for campo in OP2_ESTRUTURA_OPCOES['camisa']['campos']})
        return {prefixo + chave: valor for chave, valor in dados.items()}

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

    def _login_op2(self):
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

    def test_na_cadastrado_em_todos_os_campos_inclusive_personalizados(self):
        OpcaoEstruturaOP2.objects.create(
            filial=self.filial, tipo_peca='especial', tipo_label='Especial',
            campo='detalhe_extra', valor='Opção própria',
        )
        grupos = opcoes_estrutura_filial(self.filial)
        for tipo, grupo in grupos.items():
            for campo, opcoes in grupo['campos'].items():
                self.assertIn('N/A', opcoes)
                self.assertEqual(OpcaoEstruturaOP2.objects.for_filial(self.filial).filter(
                    tipo_peca=tipo, campo=campo, valor='N/A', ativo=True,
                ).count(), 1)
        quantidade = OpcaoEstruturaOP2.objects.for_filial(self.filial).count()
        opcoes_estrutura_filial(self.filial)
        self.assertEqual(OpcaoEstruturaOP2.objects.for_filial(self.filial).count(), quantidade)
        self._login_op2()
        resposta = self.client.get(reverse('moda:op2-tipos-peca') + '?tipo=especial')
        self.assertContains(resposta, 'N/A')

    def test_todos_os_campos_da_estrutura_sao_obrigatorios(self):
        from apps.moda.services.op2_estrutura import validar_estrutura_item

        grupos = opcoes_estrutura_filial(self.filial)
        for tipo, grupo in grupos.items():
            dados = {'estrutura_tipo': tipo}
            dados.update({f'estrutura_{campo}': 'N/A' for campo in grupo['campos']})
            validar_estrutura_item(dados, grupos)
            for campo in grupo['campos']:
                with self.subTest(tipo=tipo, campo=campo):
                    incompletos = {**dados, f'estrutura_{campo}': ''}
                    with self.assertRaisesMessage(ValueError, 'preenchimento obrigatório'):
                        validar_estrutura_item(incompletos, grupos)
                    invalidos = {**dados, f'estrutura_{campo}': 'OPÇÃO INEXISTENTE'}
                    with self.assertRaisesMessage(ValueError, 'opção válida'):
                        validar_estrutura_item(invalidos, grupos)

    def test_valor_unitario_obrigatorio_positivo_e_decimal_valido(self):
        from apps.moda.services.op2_estrutura import validar_valor_unitario

        for valor in ('', None, '0', '-1', 'NaN', 'Infinity', '1.001', '10000000000', 'abc'):
            with self.subTest(valor=valor), self.assertRaises(ValueError):
                validar_valor_unitario(valor)
        self.assertEqual(validar_valor_unitario('59,90'), Decimal('59.90'))
        self.assertEqual(validar_valor_unitario('0.01'), Decimal('0.01'))

    def test_nova_op_incompleta_nao_persiste_pedido(self):
        self._login_op2()
        base = {
            **self._modelo_completo('item_0_'), 'cliente': self.cliente.pk,
            'item_0_produto_id': self.produto.pk, 'item_0_quantidade': 1,
        }
        for campo in ('estrutura_tipo', 'estrutura_tipo_impressao', 'estrutura_malha', 'valor_unitario'):
            with self.subTest(campo=campo):
                resposta = self.client.post(reverse('moda:op2-create'), {**base, f'item_0_{campo}': ''})
                self.assertEqual(resposta.status_code, 200)
                self.assertFalse(PedidoProducao.objects.exclude(pk=self.pedido.pk).exists())

    def test_adicao_e_edicao_incompletas_preservam_o_item(self):
        self._login_op2()
        item = self._item(quantidade=2)
        item.valor_unitario = Decimal('20')
        item.observacoes = 'Dados anteriores'
        item.save()
        for acao in ('adicionar_item', 'editar_item'):
            for campo, valor in (
                ('estrutura_gola', ''), ('estrutura_tipo_impressao', ''),
                ('valor_unitario', ''), ('valor_unitario', '0'), ('valor_unitario', 'NaN'),
            ):
                with self.subTest(acao=acao, campo=campo, valor=valor):
                    dados = {**self._modelo_completo(), 'acao': acao, 'item_id': item.pk,
                             'produto_id': self.produto.pk, 'quantidade': '10', campo: valor}
                    resposta = self.client.post(reverse('moda:op2-action', args=[self.pedido.pk]), dados)
                    self.assertEqual(resposta.status_code, 302)
                    item.refresh_from_db()
                    self.assertEqual(item.quantidade, 2)
                    self.assertEqual(item.valor_unitario, Decimal('20'))
                    self.assertEqual(item.observacoes, 'Dados anteriores')
                    self.assertEqual(self.pedido.itens.count(), 1)

    def test_nova_op_com_na_preserva_escolhas_e_preco(self):
        self._login_op2()
        dados = {**self._modelo_completo('item_0_'), 'cliente': self.cliente.pk,
                 'item_0_produto_id': self.produto.pk, 'item_0_quantidade': 1,
                 'item_0_valor_unitario': '59,90',
                 'pagamento_0_forma': 'nao_informado',
                 'pagamento_0_valor': '59.90'}
        resposta = self.client.post(reverse('moda:op2-create'), dados)
        self.assertEqual(resposta.status_code, 302)
        novo = PedidoProducao.objects.exclude(pk=self.pedido.pk).get().itens.get()
        self.assertEqual(novo.valor_unitario, Decimal('59.90'))
        self.assertIn('Tipo impressao: N/A', novo.observacoes)
        self.assertIn('Malha: N/A', novo.observacoes)
        self.assertFalse(novo.personalizacoes.exists())

    def test_nova_op_cria_primeiro_registro_da_linha_do_tempo(self):
        self._login_op2()
        usuario = self._usuario()
        dados = {
            **self._modelo_completo('item_0_'), 'cliente': self.cliente.pk,
            'item_0_produto_id': self.produto.pk, 'item_0_quantidade': 1,
            'item_0_valor_unitario': '59,90',
            'pagamento_0_forma': 'nao_informado',
            'pagamento_0_valor': '59.90',
            'informacoes_criacao': 'Cliente pediu a logo centralizada.',
        }

        resposta = self.client.post(reverse('moda:op2-create'), dados)

        self.assertEqual(resposta.status_code, 302)
        novo = PedidoProducao.objects.exclude(pk=self.pedido.pk).get()
        registro = novo.historico_criacao.get()
        self.assertEqual(registro.texto, 'Cliente pediu a logo centralizada.')
        self.assertEqual(registro.criado_por, usuario)

    def test_editores_exigem_campos_e_iniciam_valor_vazio(self):
        self._login_op2()
        for url in (reverse('moda:op2-create'), reverse('moda:op2-detail', args=[self.pedido.pk])):
            resposta = self.client.get(url)
            self.assertContains(resposta, 'js/op2_modelo_validacao.js')
            self.assertContains(resposta, 'validarModeloOp2(this.draft,')
            self.assertContains(resposta, "valor_unitario:''")
            self.assertContains(resposta, 'required placeholder="Informe o valor"')
            self.assertContains(resposta, '<option value="N/A">N/A</option>')

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
            **self._modelo_completo('item_0_'),
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
            'Não informado', 'Dinheiro', 'Boleto', 'PIX', 'Cartão de débito',
            'Crédito parcelado', 'Crédito à vista',
        ):
            self.assertContains(resposta, rotulo)
        self.assertContains(resposta, '+ Outra forma')
        self.assertContains(resposta, 'não gera lançamentos no financeiro')
        self.assertContains(resposta, 'op2-payment-required')
        self.assertContains(resposta, 'Selecione a forma de pagamento')
        self.assertContains(resposta, 'pagamento.valor=totalValor()')

    def test_nova_op_exige_escolha_explicita_da_forma_de_pagamento(self):
        self._login_op2()

        resposta = self.client.post(reverse('moda:op2-create'), {
            'cliente': str(self.cliente.pk),
            **self._modelo_completo('item_0_'),
            'item_0_produto_id': str(self.produto.pk),
            'item_0_quantidade': '1',
            'item_0_valor_unitario': '25',
            'pagamento_0_forma': '',
            'pagamento_0_valor': '25.00',
        })

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'selecione a forma de pagamento')
        self.assertFalse(PedidoProducao.objects.exclude(pk=self.pedido.pk).exists())

    def test_previsao_de_entrega_aparece_vazia_e_e_opcional(self):
        self._login_op2()

        resposta = self.client.get(reverse('moda:op2-create'))

        self.assertContains(resposta, 'Previsão de entrega')
        self.assertContains(
            resposta,
            '<input type="date" name="data_prevista_entrega" value="" class="form-input w-full">',
            html=True,
        )

        resposta = self.client.post(reverse('moda:op2-create'), {
            'cliente': str(self.cliente.pk),
            **self._modelo_completo('item_0_'),
            'item_0_produto_id': str(self.produto.pk),
            'item_0_quantidade': '1',
            'item_0_valor_unitario': '25',
            'pagamento_0_forma': 'nao_informado',
            'pagamento_0_valor': '25.00',
        })
        criado = PedidoProducao.objects.exclude(pk=self.pedido.pk).get()
        self.assertEqual(resposta.status_code, 302)
        self.assertIsNone(criado.data_prevista_entrega)

    def test_nova_op_salva_divisao_do_pagamento_previsto(self):
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

        resposta = self.client.post(reverse('moda:op2-create'), {
            'cliente': str(self.cliente.pk),
            **self._modelo_completo('item_0_'),
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
            **self._modelo_completo('item_0_'),
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
            **self._modelo_completo('item_0_'),
            'item_0_produto_id': str(self.produto.pk),
            'item_0_quantidade': '2',
            'item_0_valor_unitario': '10',
            'pagamento_0_forma': 'nao_informado',
            'pagamento_0_valor': '20.00',
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
            **self._modelo_completo('item_0_'),
            'item_0_produto_id': str(self.produto.pk),
            'item_0_quantidade': '2',
            'item_0_valor_unitario': '25.50',
            'pagamento_0_forma': 'nao_informado',
            'pagamento_0_valor': '51.00',
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
            **self._modelo_completo('item_0_'),
            'item_0_produto_id': str(self.produto.pk),
            'item_0_quantidade': '2',
            'item_0_valor_unitario': '25.50',
            'pagamento_0_forma': 'nao_informado',
            'pagamento_0_valor': '51.00',
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
        self.assertContains(resposta, "abrirDuplicarProduto('")
        self.assertContains(resposta, '>Duplicar</button>')
        self.assertContains(resposta, "this.draft.item_id=''")
        self.assertContains(resposta, 'Duplicar produto da OP')
        self.assertContains(resposta, 'Adicionar cópia')
        self.assertContains(resposta, 'abrirNovoProduto()')
        self.assertContains(resposta, 'rel="noopener"')
        self.assertContains(resposta, 'op2-order-header')
        self.assertContains(resposta, '<h2 class="font-bold">Fotos e mockups</h2>')
        self.assertContains(resposta, 'op2-aside .op2-gallery-grid')
        self.assertContains(resposta, 'Todos os produtos acompanham o status geral da OP.')
        self.assertContains(resposta, '>Responsável</span>')
        self.assertContains(resposta, 'name="cliente" :value="clienteId"')
        self.assertContains(resposta, 'Editar cadastro do cliente atual')
        self.assertContains(
            resposta,
            reverse('cadastros:cliente-update', args=[self.cliente.pk]),
        )
        self.assertContains(resposta, 'buscarClientes()')
        self.assertNotContains(resposta, 'Quantidade entregue')
        self.assertNotContains(resposta, 'Atualizar entrega')
        self.assertNotContains(resposta, 'name="acao" value="item_fluxo"')

    def test_abrir_whatsapp_libera_link_publico_antes_de_exibir_mensagem(self):
        self._login_op2()

        pagina = self.client.get(
            reverse('moda:op2-detail', args=[self.pedido.pk]),
        )
        self.assertContains(pagina, '@click="prepararWhatsapp"')

        resposta = self.client.post(
            reverse('moda:op2-action', args=[self.pedido.pk]),
            {'acao': 'enviar_whatsapp'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(resposta.status_code, 302)
        aprovacao = AprovacaoPedido.objects.get(pedido=self.pedido)
        self.assertTrue(aprovacao.liberado)
        link = reverse('moda_publico:pedido', args=[self.pedido.token_publico])
        self.assertEqual(self.client.get(link).status_code, 200)

    def test_whatsapp_nao_apaga_nome_de_quem_ja_aprovou(self):
        self._login_op2()
        self.pedido.status = PedidoProducao.Status.CONFIRMADO
        self.pedido.save(update_fields=['status'])
        aprovacao = AprovacaoPedido.objects.create(pedido=self.pedido)
        aprovacao.liberar(self._usuario())
        aprovacao.responder(
            AprovacaoPedido.Resposta.APROVADO,
            'Maria da Silva',
            ip='192.0.2.10',
        )
        respondido_em = aprovacao.respondido_em

        self.client.post(
            reverse('moda:op2-action', args=[self.pedido.pk]),
            {'acao': 'enviar_whatsapp'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        aprovacao.refresh_from_db()
        self.assertTrue(aprovacao.aprovado_pelo_cliente)
        self.assertEqual(aprovacao.respondido_por, 'Maria da Silva')
        self.assertEqual(aprovacao.respondido_em, respondido_em)
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.status, PedidoProducao.Status.CONFIRMADO)

        pagina = self.client.get(
            reverse('moda:op2-detail', args=[self.pedido.pk]),
        )
        self.assertContains(pagina, 'Orçamento aprovado por Maria da Silva')
        self.assertContains(pagina, 'data, o horário e o IP')

    def test_tipo_de_impressao_aparece_somente_no_campo_identificado(self):
        item = self._item(quantidade=4)
        Personalizacao.objects.create(
            item=item, tipo=Personalizacao.Tipo.ARTE,
            tecnica=Personalizacao.Tecnica.SUBLIMACAO,
        )
        self._login_op2()

        resposta = self.client.get(reverse('moda:op2-detail', args=[self.pedido.pk]))

        self.assertContains(
            resposta,
            '<span class="op2-label block">Tipo de impressão</span>'
            '<span class="text-xs">Sublimação</span>',
            html=True,
        )
        self.assertNotContains(
            resposta, '<p class="text-xs opacity-65 mt-1">Sublimação</p>',
            html=True,
        )

    def test_cliente_da_op_pode_ser_trocado_em_qualquer_etapa(self):
        novo_cliente = Cliente.objects.create(
            filial=self.filial, tipo_pessoa='J',
            razao_social='Cliente corrigido', contato_nome='Novo contato',
            celular='84999990000', ativo=True,
        )
        self.pedido.status = PedidoProducao.Status.EM_PRODUCAO
        self.pedido.save(update_fields=['status'])
        self._login_op2()

        resposta = self.client.post(
            reverse('moda:op2-action', args=[self.pedido.pk]),
            {
                'acao': 'cabecalho',
                'cliente': novo_cliente.pk,
                'data_pedido': self.pedido.data_pedido.isoformat(),
                'data_prevista_entrega': '',
                'prioridade': self.pedido.prioridade,
                'contato_nome': novo_cliente.contato_nome,
                'contato_telefone': novo_cliente.celular,
                'observacoes': self.pedido.observacoes,
            },
        )

        self.assertRedirects(
            resposta, reverse('moda:op2-detail', args=[self.pedido.pk]),
        )
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.cliente, novo_cliente)
        self.assertEqual(self.pedido.status, PedidoProducao.Status.EM_PRODUCAO)
        self.assertEqual(self.pedido.contato_nome, 'Novo contato')
        self.assertEqual(self.pedido.contato_telefone, '84999990000')

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

    def test_op_existente_permite_editar_nome_numero_e_tamanho(self):
        tamanho_p = Tamanho.objects.create(filial=self.filial, sigla='P', ordem=10)
        tamanho_m = Tamanho.objects.create(filial=self.filial, sigla='M', ordem=20)
        item = self._item(quantidade=2)
        ItemGradePedido.objects.create(item=item, tamanho=tamanho_p, quantidade=1)
        ItemGradePedido.objects.create(item=item, tamanho=tamanho_m, quantidade=1)
        pessoa = PersonalizacaoIndividual.objects.create(
            pedido=self.pedido, item=item, tamanho=tamanho_p,
            nome='NOME ANTIGO', numero='7',
        )
        self.client.force_login(self._usuario())
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()

        resposta = self.client.post(reverse('moda:op2-action', args=[self.pedido.pk]), {
            'acao': 'editar_individual',
            'individual_id': str(pessoa.pk),
            'tamanho': str(tamanho_m.pk),
            'nome': 'NOME CORRIGIDO',
            'numero': '12',
        })

        self.assertRedirects(resposta, reverse('moda:op2-detail', args=[self.pedido.pk]))
        pessoa.refresh_from_db()
        self.assertEqual(pessoa.tamanho, tamanho_m)
        self.assertEqual(pessoa.nome, 'NOME CORRIGIDO')
        self.assertEqual(pessoa.numero, '12')

        detalhe = self.client.get(reverse('moda:op2-detail', args=[self.pedido.pk]))
        self.assertContains(detalhe, 'Editar personalizações')
        self.assertContains(detalhe, 'Salvar alteração')
        self.assertContains(detalhe, 'value="NOME CORRIGIDO"')

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
            **self._modelo_completo(),
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
            'estrutura_tipo_impressao': 'SILK',
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
            **self._modelo_completo(),
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
        detalhe = self.client.get(reverse('moda:op2-detail', args=[self.pedido.pk]))
        html = detalhe.content.decode()
        self.assertEqual(html.count('data-product-group='), 1)
        self.assertContains(detalhe, 'Adulto · 2 peças')
        self.assertContains(detalhe, 'OverSized · 4 peças')
        self.assertContains(detalhe, 'As grades ficam juntas no mesmo produto')

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
            **self._modelo_completo(),
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

    def test_editar_variante_sincroniza_dados_comuns_sem_alterar_grade_e_personalizacao(self):
        tamanho_p = Tamanho.objects.create(filial=self.filial, sigla='P2', ordem=10)
        tamanho_g = Tamanho.objects.create(filial=self.filial, sigla='G2', ordem=20)
        adulto = Grade.objects.create(filial=self.filial, nome='Adulto 2')
        oversized = Grade.objects.create(filial=self.filial, nome='OverSized 2')
        ItemGrade.objects.create(grade=adulto, tamanho=tamanho_p, ordem=10)
        ItemGrade.objects.create(grade=oversized, tamanho=tamanho_g, ordem=10)
        item = self._item(quantidade=2)
        item.grade_tamanho = adulto
        item.save(update_fields=['grade_tamanho'])
        ItemGradePedido.objects.create(item=item, tamanho=tamanho_p, quantidade=2)
        Personalizacao.objects.create(item=item, tecnica='silk')
        irmao = self._item(quantidade=4)
        irmao.grade_tamanho = oversized
        irmao.observacoes = 'Malha: ANTIGA'
        irmao.save(update_fields=['grade_tamanho', 'observacoes'])
        ItemGradePedido.objects.create(item=irmao, tamanho=tamanho_g, quantidade=4)
        Personalizacao.objects.create(item=irmao, tecnica='sublimacao')
        self._login_op2()

        resposta = self.client.post(reverse('moda:op2-action', args=[self.pedido.pk]), {
            **self._modelo_completo(),
            'acao': 'editar_item',
            'item_id': str(item.pk),
            'produto_id': str(self.produto.pk),
            'grades': str(adulto.pk),
            f'grade_{adulto.pk}_{tamanho_p.pk}': '2',
            'quantidade': '2',
            'valor_unitario': '75.50',
            'referencia': 'REF-COMUM',
            'acabamento': 'Barra comum',
            'estrutura_tipo': 'camisa',
            'estrutura_malha': 'DRYTECH',
            'estrutura_tipo_impressao': 'SILK',
        })

        self.assertRedirects(resposta, reverse('moda:op2-detail', args=[self.pedido.pk]))
        irmao.refresh_from_db()
        self.assertEqual(irmao.valor_unitario, Decimal('75.50'))
        self.assertEqual(irmao.referencia, 'REF-COMUM')
        self.assertEqual(irmao.acabamento, 'Barra comum')
        self.assertIn('Malha: DRYTECH', irmao.observacoes)
        self.assertEqual(irmao.grade_tamanho, oversized)
        self.assertEqual(irmao.quantidade, 4)
        self.assertEqual(
            list(irmao.grade.values_list('tamanho__sigla', 'quantidade')),
            [('G2', 4)],
        )
        self.assertEqual(irmao.personalizacoes.get().tecnica, 'sublimacao')

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
            **self._modelo_completo(),
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

    def test_editor_completo_registra_na_sem_apagar_arte_anterior(self):
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
            **self._modelo_completo(),
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
        arte = Personalizacao.objects.get(item=item)
        self.assertEqual(arte.tecnica, 'N/A')
        self.assertEqual(arte.observacoes, 'Arte antiga')

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
            dados.update(self._modelo_completo(f'item_{idx}_'))
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

        dados.update({
            'pagamento_0_forma': 'nao_informado',
            'pagamento_0_valor': '50.00',
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
            **self._modelo_completo('item_0_'),
            'item_0_produto_id': str(self.produto.pk),
            'item_0_grade_id': str(grade.pk),
            f'item_0_grade_{tamanho.pk}': '1',
            'item_0_quantidade': '1',
            'item_0_valor_unitario': '25',
            'individual_0_item_idx': '0',
            'individual_0_tamanho_id': str(tamanho.pk),
            'individual_0_nome': 'DIEGO',
            'individual_0_numero': '10',
            'pagamento_0_forma': 'nao_informado',
            'pagamento_0_valor': '25.00',
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

    def test_informacoes_criacao_salvam_sem_alterar_dados_comerciais(self):
        self._login_op2()
        usuario = self._usuario()
        self.pedido.observacoes = 'Observações comerciais'
        self.pedido.save()
        url = reverse('moda:op2-action', args=[self.pedido.pk])
        for status in ('orcamento', 'aguardando_arte'):
            self.pedido.status = status
            self.pedido.save()
            texto = f'Conferir cores\nAlinhar escudo & nome — {status}'
            resposta = self.client.post(url, {
                'acao': 'criacao', 'informacoes_criacao': texto,
            })
            self.assertEqual(resposta.status_code, 302)
            registro = self.pedido.historico_criacao.get(texto=texto)
            self.assertEqual(registro.criado_por, usuario)
            self.assertIsNotNone(registro.criado_em)
            self.pedido.refresh_from_db()
            self.assertEqual(self.pedido.observacoes, 'Observações comerciais')
            self.assertEqual(self.pedido.status, status)
        quantidade = self.pedido.historico_criacao.count()
        self.client.post(url, {'acao': 'criacao', 'informacoes_criacao': '   '})
        self.assertEqual(self.pedido.historico_criacao.count(), quantidade)
        recente = RegistroCriacaoArte.objects.create(
            pedido=self.pedido, texto='Informação mais recente', criado_por=usuario,
        )
        detalhe = self.client.get(reverse('moda:op2-detail', args=[self.pedido.pk]))
        self.assertContains(detalhe, 'Criação de artes', count=2)
        self.assertContains(detalhe, 'Usuario OP 2')
        self.assertContains(detalhe, 'Ver mais (1)', count=2)
        self.assertContains(detalhe, 'value="editar_criacao"')
        self.assertContains(detalhe, 'value="remover_criacao"')
        html = detalhe.content.decode()
        self.assertLess(html.index('Informação mais recente'),
                        html.index('Conferir cores'))
        self.assertLess(html.index('Fotos e mockups'), html.rindex('Criação de artes'))
        self.assertLess(html.rindex('Criação de artes'), html.index('Resumo de conferência'))
        self.assertEqual(recente, self.pedido.historico_criacao.first())

    def test_criacao_permite_editar_apagar_e_isola_outro_pedido(self):
        self._login_op2()
        registro = RegistroCriacaoArte.objects.create(
            pedido=self.pedido, texto='Texto original', criado_por=self._usuario(),
        )
        outro = PedidoProducao.objects.create(filial=self.filial, cliente=self.cliente)
        registro_outro = RegistroCriacaoArte.objects.create(
            pedido=outro, texto='Não alterar', criado_por=self._usuario(),
        )
        url = reverse('moda:op2-action', args=[self.pedido.pk])

        resposta = self.client.post(url, {
            'acao': 'editar_criacao', 'registro_id': registro.pk,
            'informacoes_criacao': 'Texto corrigido',
        })
        self.assertEqual(resposta.status_code, 302)
        registro.refresh_from_db()
        self.assertEqual(registro.texto, 'Texto corrigido')
        self.client.post(url, {
            'acao': 'editar_criacao', 'registro_id': registro.pk,
            'informacoes_criacao': '   ',
        })
        registro.refresh_from_db()
        self.assertEqual(registro.texto, 'Texto corrigido')
        for acao in ('editar_criacao', 'remover_criacao'):
            resposta = self.client.post(url, {
                'acao': acao, 'registro_id': registro_outro.pk,
                'informacoes_criacao': 'Inválido',
            })
            self.assertEqual(resposta.status_code, 404)
        registro_outro.refresh_from_db()
        self.assertEqual(registro_outro.texto, 'Não alterar')
        self.client.post(url, {
            'acao': 'remover_criacao', 'registro_id': registro.pk,
        })
        self.assertFalse(RegistroCriacaoArte.objects.filter(pk=registro.pk).exists())

    def test_descricao_visual_edita_limpa_e_valida_sem_mudar_imagem(self):
        self._login_op2()
        visual = VisualItemPedido.objects.create(
            item=self._item(), posicao='frente_camisa', imagem='moda/visuais/teste.png',
        )
        url = reverse('moda:op2-action', args=[self.pedido.pk])
        for texto in ('Frente <azul> & branca\nEscudo central', '', 'A' * 160):
            self.assertEqual(self.client.post(url, {
                'acao': 'descricao_visual', 'visual_id': visual.pk, 'descricao': texto,
            }).status_code, 302)
            visual.refresh_from_db()
            self.assertEqual(visual.observacoes, texto)
            self.assertEqual(visual.imagem.name, 'moda/visuais/teste.png')
        self.client.post(url, {
            'acao': 'descricao_visual', 'visual_id': visual.pk, 'descricao': 'B' * 161,
        })
        visual.refresh_from_db()
        self.assertEqual(visual.observacoes, 'A' * 160)

    def test_descricao_nao_altera_imagem_de_outro_pedido(self):
        self._login_op2()
        outro = PedidoProducao.objects.create(filial=self.filial, cliente=self.cliente)
        item = ItemPedidoProducao.objects.create(pedido=outro, quantidade=1)
        visual = VisualItemPedido.objects.create(item=item, posicao='frente_camisa', observacoes='Original')
        resposta = self.client.post(reverse('moda:op2-action', args=[self.pedido.pk]), {
            'acao': 'descricao_visual', 'visual_id': visual.pk, 'descricao': 'Inválido',
        })
        self.assertEqual(resposta.status_code, 404)
        visual.refresh_from_db()
        self.assertEqual(visual.observacoes, 'Original')

    def test_descricao_visual_autosalva_json_sem_redirect_ou_toast(self):
        self._login_op2()
        visual = VisualItemPedido.objects.create(item=self._item(), posicao='frente_camisa')
        url = reverse('moda:op2-action', args=[self.pedido.pk])
        for texto in ('Frente & verso', ''):
            resposta = self.client.post(url, {
                'acao': 'descricao_visual', 'visual_id': visual.pk, 'descricao': texto,
            }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
            self.assertEqual(resposta.status_code, 200)
            self.assertEqual(resposta.json(), {'ok': True, 'descricao': texto})
            visual.refresh_from_db()
            self.assertEqual(visual.observacoes, texto)
        resposta = self.client.post(url, {
            'acao': 'descricao_visual', 'visual_id': visual.pk, 'descricao': 'A' * 161,
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(resposta.json()['ok'])
        visual.refresh_from_db()
        self.assertEqual(visual.observacoes, '')

    def test_criacao_nao_altera_pedido_de_outra_filial(self):
        self._login_op2()
        filial = Filial.objects.create(
            empresa=self.filial.empresa, razao_social='Outra', cnpj='53345678000353',
        )
        outro = PedidoProducao.objects.create(filial=filial, cliente=self.cliente)
        resposta = self.client.post(reverse('moda:op2-action', args=[outro.pk]), {
            'acao': 'criacao', 'informacoes_criacao': 'Não autorizado',
        })
        self.assertEqual(resposta.status_code, 404)
        self.assertFalse(outro.historico_criacao.exists())

    def test_duplicar_preserva_criacao_e_descricao_da_imagem(self):
        self._login_op2()
        usuario = self._usuario()
        primeiro = RegistroCriacaoArte.objects.create(
            pedido=self.pedido, texto='Briefing da criação', criado_por=usuario,
            criado_em=timezone.now() - timedelta(hours=2),
        )
        segundo = RegistroCriacaoArte.objects.create(
            pedido=self.pedido, texto='Cliente aprovou a alteração', criado_por=usuario,
        )
        VisualItemPedido.objects.create(item=self._item(), posicao='frente_camisa', observacoes='Escudo azul')
        self.client.post(reverse('moda:op2-action', args=[self.pedido.pk]), {'acao': 'duplicar'})
        novo = PedidoProducao.objects.exclude(pk=self.pedido.pk).get()
        copias = list(novo.historico_criacao.order_by('criado_em'))
        self.assertEqual([r.texto for r in copias], [primeiro.texto, segundo.texto])
        self.assertEqual([r.criado_por for r in copias], [usuario, usuario])
        self.assertEqual([r.criado_em for r in copias], [primeiro.criado_em, segundo.criado_em])
        self.assertEqual(novo.itens.get().visuais.get().observacoes, 'Escudo azul')

    def test_anexo_permite_baixar_editar_observacao_e_remover_individualmente(self):
        self._login_op2()
        anexo = ArquivoPedido.objects.create(
            pedido=self.pedido, arquivo='moda/pedidos/teste/imagem.png',
            tipo=ArquivoPedido.Tipo.ARTE, descricao='Observação antiga',
        )
        url = reverse('moda:op2-action', args=[self.pedido.pk])
        detalhe = self.client.get(reverse('moda:op2-detail', args=[self.pedido.pk]))
        self.assertContains(detalhe, 'download')
        self.assertContains(detalhe, 'value="editar_anexo"')
        self.assertContains(detalhe, 'value="remover_anexo"')
        resposta = self.client.post(url, {
            'acao': 'editar_anexo', 'arquivo_id': anexo.pk,
            'descricao': 'Aprovada pelo cliente',
        })
        self.assertEqual(resposta.status_code, 302)
        anexo.refresh_from_db()
        self.assertEqual(anexo.descricao, 'Aprovada pelo cliente')
        resposta = self.client.post(url, {
            'acao': 'remover_anexo', 'arquivo_id': anexo.pk,
        })
        self.assertEqual(resposta.status_code, 302)
        self.assertFalse(ArquivoPedido.objects.filter(pk=anexo.pk).exists())

    def test_anexo_de_outro_pedido_nao_pode_ser_editado_ou_removido(self):
        self._login_op2()
        outro = PedidoProducao.objects.create(filial=self.filial, cliente=self.cliente)
        anexo = ArquivoPedido.objects.create(
            pedido=outro, arquivo='moda/pedidos/teste/outro.png', descricao='Original',
        )
        url = reverse('moda:op2-action', args=[self.pedido.pk])
        for acao in ('editar_anexo', 'remover_anexo'):
            resposta = self.client.post(url, {
                'acao': acao, 'arquivo_id': anexo.pk, 'descricao': 'Inválida',
            })
            self.assertEqual(resposta.status_code, 404)
        anexo.refresh_from_db()
        self.assertEqual(anexo.descricao, 'Original')

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
        self.assertContains(detalhe, 'op2LegendaAutomatica()')
        self.assertContains(detalhe, 'placeholder="Texto da imagem"')
        self.assertNotContains(detalhe, 'Salvar descrição')
        self.assertNotContains(detalhe, 'Aparece no PDF da OP e do orçamento')
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
