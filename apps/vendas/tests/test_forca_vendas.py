"""Fluxo web da força de vendas: pedido, Kanban e separação."""

import json
import shutil
import subprocess
from decimal import Decimal
from unittest import skipUnless
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from apps.cadastros.models import Cliente, ClienteFilial
from apps.core.models import Empresa, Filial, PerfilAcesso, Permissao, Usuario
from apps.core.services.exceptions import EstoqueInsuficienteError
from apps.core.services.modulos import modulo_da_url, modulos_ativos
from apps.estoque.models import Deposito, Estoque, MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.pdv.models import VendaPDV
from apps.financeiro.constants.enums import StatusContaReceber, TipoFormaPagamento
from apps.financeiro.models import CondicaoPagamento, ContaReceber, FormaPagamento
from apps.produtos.models import (
    CategoriaProduto,
    CategoriaProdutoFilial,
    ItemTabelaPreco,
    Produto,
    ProdutoFilial,
    TabelaPreco,
    TabelaPrecoFilial,
    UnidadeMedida,
    UnidadeMedidaFilial,
)
from apps.vendas.models import PedidoVenda
from apps.vendas.services.venda_service import VendaService
from apps.whatsapp_agent.models import ConfiguracaoWhatsApp, MensagemWhatsApp


class ForcaVendasTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Distribuidora Teste LTDA',
            nome_fantasia='Distribuidora Teste',
            cnpj='82345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa,
            razao_social='Distribuidora Teste Matriz',
            nome_fantasia='Matriz',
            cnpj='82345678000192',
            uf='RN',
            cidade='Natal',
            is_matriz=True,
        )
        cls.perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa,
            nome='Gestor Comercial',
            is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='gestor-forca-vendas@inoovated.com',
            nome='Gestor Comercial',
            password='teste1234',
            empresa=cls.empresa,
            filial=cls.filial,
            perfil=cls.perfil,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa,
            sigla='UN',
            descricao='Unidade',
            tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        cls.tabela = TabelaPreco.objects.create(
            filial=cls.filial,
            descricao='Atacado externo',
            tipo=TabelaPreco.Tipo.ATACADO,
        )
        TabelaPrecoFilial.objects.create(tabela=cls.tabela, filial=cls.filial)
        cls.cliente = Cliente.objects.create(
            filial=cls.filial,
            tipo_pessoa='J',
            razao_social='Mercadinho Cliente LTDA',
            nome_fantasia='Mercadinho Cliente',
            cpf_cnpj='82345678000193',
            cidade='Natal',
            uf='RN',
            tabela_preco=cls.tabela,
            limite_credito=Decimal('1000.00'),
            prazo_pagamento_dias=15,
            ativo=True,
        )
        ClienteFilial.objects.create(cliente=cls.cliente, filial=cls.filial)
        cls.categoria = CategoriaProduto.objects.create(
            empresa=cls.empresa,
            filial=cls.filial,
            nome='Congelados',
        )
        CategoriaProdutoFilial.objects.create(categoria=cls.categoria, filial=cls.filial)
        cls.produto = Produto.objects.create(
            filial=cls.filial,
            unidade_medida=cls.unidade,
            categoria=cls.categoria,
            descricao='Caixa de Polpa',
            codigo='POLPA-001',
            ncm='20089900',
            controla_lote=False,
            permite_venda_sem_estoque=False,
            preco_venda=Decimal('10.00'),
            preco_custo=Decimal('4.00'),
            catalogo_destaque=True,
        )
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.filial)
        cls.produto_substituto = Produto.objects.create(
            filial=cls.filial,
            unidade_medida=cls.unidade,
            categoria=cls.categoria,
            descricao='Caixa de Polpa Premium',
            codigo='POLPA-002',
            ncm='20089900',
            controla_lote=False,
            permite_venda_sem_estoque=False,
            preco_venda=Decimal('12.00'),
            preco_custo=Decimal('5.00'),
        )
        ProdutoFilial.objects.create(produto=cls.produto_substituto, filial=cls.filial)
        ItemTabelaPreco.objects.create(
            tabela=cls.tabela,
            produto=cls.produto,
            preco_unitario=Decimal('7.50'),
            quantidade_minima=0,
        )
        ItemTabelaPreco.objects.create(
            tabela=cls.tabela,
            produto=cls.produto,
            preco_unitario=Decimal('6.50'),
            quantidade_minima=5,
        )
        ItemTabelaPreco.objects.create(
            tabela=cls.tabela,
            produto=cls.produto_substituto,
            preco_unitario=Decimal('8.00'),
            quantidade_minima=0,
        )
        cls.forma = FormaPagamento.objects.create(
            empresa=cls.empresa,
            filial=cls.filial,
            descricao='Boleto bancário',
            tipo=TipoFormaPagamento.BOLETO,
            gera_parcelas=True,
        )
        cls.condicao = CondicaoPagamento.objects.create(
            empresa=cls.empresa,
            descricao='15/30 dias',
            numero_parcelas=2,
            intervalo_dias=15,
            dias_primeira_parcela=15,
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        session = self.client.session
        session['filial_ativa_id'] = self.filial.pk
        session.save()

    def criar_pedido_pela_tela(self, quantidade='2'):
        return self.client.post(
            reverse('vendas:pedido-externo-novo'),
            data=json.dumps({
                'cliente_id': self.cliente.pk,
                'itens': [{
                    'produto_id': self.produto.pk,
                    'quantidade': quantidade,
                    # O servidor deve ignorar qualquer preço enviado pelo navegador.
                    'valor_unitario': '0.01',
                }],
                'prioridade': 'urgente',
                'observacao': 'Entregar pela manhã.',
                'forma_pagamento_id': self.forma.pk,
                'condicao_pagamento_id': self.condicao.pk,
                'prazo_pagamento_dias': 15,
            }),
            content_type='application/json',
        )

    def abastecer(self, quantidade='20'):
        MovimentacaoService.registrar_movimentacao(
            produto_id=self.produto.pk,
            filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal(quantidade),
            usuario_id=self.usuario.pk,
            valor_unitario=Decimal('4.00'),
        )
        MovimentacaoService.registrar_movimentacao(
            produto_id=self.produto_substituto.pk,
            filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal(quantidade),
            usuario_id=self.usuario.pk,
            valor_unitario=Decimal('5.00'),
        )

    def test_modulo_e_menu_reunem_as_quatro_telas(self):
        self.assertEqual(modulo_da_url('/vendas/acompanhamento/'), 'forca_vendas')
        self.assertIn('forca_vendas', modulos_ativos(self.filial))

        resposta = self.client.get(reverse('vendas:pedido-externo-novo'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Força de Vendas')
        self.assertContains(resposta, reverse('vendas:forca-vendas-dashboard'))
        self.assertContains(resposta, reverse('vendas:pedido-externo-novo'))
        self.assertContains(resposta, reverse('vendas:pedido-kanban'))
        self.assertContains(resposta, reverse('vendas:pedido-separacao'))
        self.assertNotContains(resposta, 'Pré-venda')

    def test_telas_da_forca_de_vendas_sao_responsivas_e_respeitam_o_tema(self):
        rotas = (
            reverse('vendas:forca-vendas-dashboard'),
            reverse('vendas:pedido-externo-novo'),
            reverse('vendas:pedido-kanban'),
            reverse('vendas:pedido-separacao'),
        )

        for rota in rotas:
            with self.subTest(rota=rota):
                resposta = self.client.get(rota)
                self.assertEqual(resposta.status_code, 200)
                self.assertContains(resposta, 'viewport-fit=cover')
                self.assertContains(resposta, 'interactive-widget=resizes-content')
                self.assertContains(resposta, 'touch-action:manipulation')
                self.assertContains(resposta, 'font-size:16px')

        separacao = self.client.get(reverse('vendas:pedido-separacao'))
        self.assertContains(separacao, '--fs-card:#242830')
        self.assertContains(separacao, 'body.tema-claro .fs-shell')
        self.assertContains(separacao, 'Imprimir fila')
        self.assertNotContains(separacao, 'var(--erp-card-bg,#fff)')

        pedido_id = self.criar_pedido_pela_tela().json()['pedido_id']
        detalhe = self.client.get(reverse('vendas:pedido-detail', args=[pedido_id]))
        self.assertContains(detalhe, 'fp-detail')
        self.assertContains(detalhe, 'max-height:calc(100dvh - 24px)')
        self.assertContains(detalhe, 'overscroll-behavior-x:contain')

    def test_dashboard_exibe_indicadores_reais_e_filtros(self):
        pedido_id = self.criar_pedido_pela_tela(quantidade='4').json()['pedido_id']
        PedidoVenda.objects.filter(pk=pedido_id).update(
            status=PedidoVenda.Status.CONFIRMADO,
        )

        resposta = self.client.get(reverse('vendas:forca-vendas-dashboard'))

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context['vendas']['pedidos'], 1)
        self.assertEqual(resposta.context['vendas']['valor'], Decimal('30.00'))
        self.assertEqual(resposta.context['vendas']['clientes'], 1)
        self.assertEqual(resposta.context['produtos'][0]['produto__descricao'], 'Caixa de Polpa')
        self.assertEqual(resposta.context['regioes'][0]['label'], 'Natal / RN')
        self.assertContains(resposta, 'Ranking de vendedores')
        self.assertContains(resposta, 'Produtos mais vendidos')
        self.assertContains(resposta, 'Boleto bancário')

        filtrada = self.client.get(reverse('vendas:forca-vendas-dashboard'), {
            'regiao': 'Mossoró|RN',
        })
        self.assertEqual(filtrada.context['vendas']['pedidos'], 0)
        self.assertEqual(filtrada.context['vendas']['valor'], Decimal('0'))

    def test_dashboard_do_vendedor_nao_exibe_pedidos_de_outros_usuarios(self):
        pedido_gestor = PedidoVenda.objects.get(
            pk=self.criar_pedido_pela_tela().json()['pedido_id'],
        )
        PedidoVenda.objects.filter(pk=pedido_gestor.pk).update(
            status=PedidoVenda.Status.CONFIRMADO,
        )
        perfil_vendedor = PerfilAcesso.objects.create(
            empresa=self.empresa,
            nome='Vendedor externo',
            is_admin=False,
        )
        Permissao.objects.create(
            perfil=perfil_vendedor,
            modulo='vendas',
            pode_ver=True,
            pode_criar=True,
        )
        vendedor = Usuario.objects.create_user(
            email='vendedor-externo@inoovated.com',
            nome='Vendedor Externo',
            password='teste1234',
            empresa=self.empresa,
            filial=self.filial,
            perfil=perfil_vendedor,
        )
        self.client.force_login(vendedor)
        pedido_vendedor = VendaService.criar_pedido(
            self.filial, vendedor, self.cliente,
        )
        pedido_vendedor.origem = PedidoVenda.Origem.EXTERNO
        pedido_vendedor.status = PedidoVenda.Status.CONFIRMADO
        pedido_vendedor.valor_total = Decimal('12.00')
        pedido_vendedor.save(update_fields=['origem', 'status', 'valor_total', 'updated_at'])

        resposta = self.client.get(reverse('vendas:forca-vendas-dashboard'))

        self.assertEqual(resposta.status_code, 200)
        self.assertFalse(resposta.context['visao_equipe'])
        self.assertEqual(resposta.context['vendas']['pedidos'], 1)
        self.assertEqual(resposta.context['vendas']['valor'], Decimal('12.00'))
        self.assertNotContains(resposta, self.usuario.nome)

    def test_busca_produto_exibe_preco_da_tabela_do_cliente(self):
        resposta = self.client.get(reverse('vendas:api-produtos'), {
            'cliente_id': self.cliente.pk,
            'q': 'Polpa',
        })

        self.assertEqual(resposta.status_code, 200)
        produto = resposta.json()['produtos'][0]
        self.assertEqual(Decimal(str(produto['preco'])), Decimal('7.5'))
        self.assertIn('Atacado externo', produto['preco_origem'])

        resposta_volume = self.client.get(reverse('vendas:api-produtos'), {
            'cliente_id': self.cliente.pk,
            'produto_id': self.produto.pk,
            'quantidade': '6',
        })
        self.assertEqual(
            Decimal(str(resposta_volume.json()['produtos'][0]['preco'])),
            Decimal('6.5'),
        )

        destaques = self.client.get(reverse('vendas:api-produtos'), {
            'cliente_id': self.cliente.pk,
            'categoria_id': self.categoria.pk,
            'destaques': '1',
        })
        self.assertEqual(destaques.json()['produtos'][0]['id'], self.produto.pk)
        self.assertTrue(destaques.json()['produtos'][0]['destaque'])

    def test_busca_produto_exibe_saldo_com_nome_de_todos_os_depositos(self):
        loja_id = Deposito.padrao_id(self.filial.pk)
        Deposito.objects.filter(pk=loja_id).update(nome='Frente de loja')
        galpao = Deposito.objects.create(
            filial=self.filial,
            nome='Galpão',
            tipo=Deposito.Tipo.REVENDA,
            permite_venda=True,
            permite_producao=False,
        )
        for deposito_id, quantidade in ((loja_id, '7'), (galpao.pk, '13')):
            MovimentacaoService.registrar_movimentacao(
                produto_id=self.produto.pk,
                filial_id=self.filial.pk,
                tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
                quantidade=Decimal(quantidade),
                usuario_id=self.usuario.pk,
                valor_unitario=Decimal('4'),
                deposito_id=deposito_id,
            )

        resposta = self.client.get(reverse('vendas:api-produtos'), {
            'cliente_id': self.cliente.pk,
            'produto_id': self.produto.pk,
        })
        produto = resposta.json()['produtos'][0]
        saldos = {saldo['nome']: saldo for saldo in produto['estoques']}

        self.assertEqual(saldos['Frente de loja']['disponivel'], 7.0)
        self.assertEqual(saldos['Galpão']['disponivel'], 13.0)
        self.assertTrue(saldos['Frente de loja']['origem_pedido'])
        self.assertFalse(saldos['Galpão']['origem_pedido'])
        tela = self.client.get(reverse('vendas:pedido-externo-novo'))
        self.assertContains(tela, 'produto.estoques')
        self.assertContains(tela, 'Depósitos de revenda aparecem em azul')

    def test_busca_cliente_por_primeiro_nome_e_prazos_rapidos(self):
        busca = self.client.get(reverse('vendas:api-clientes'), {'q': 'Mercadinho'})

        self.assertEqual(busca.status_code, 200)
        self.assertEqual(busca.json()['clientes'][0]['id'], self.cliente.pk)

        tela = self.client.get(reverse('vendas:pedido-externo-novo'))
        self.assertContains(tela, 'fv-card--client')
        self.assertContains(tela, 'Buscando clientes…')
        self.assertContains(tela, 'Abrir calendário')
        self.assertContains(tela, '[7,10,15,30]')
        self.assertContains(tela, 'prazoSelecionado:10')

    @skipUnless(shutil.which('node'), 'Node.js necessário para validar o JavaScript do pedido externo')
    def test_busca_e_categoria_dividem_a_linha_e_destaques_abrem_a_lista(self):
        resposta = self.client.get(reverse('vendas:pedido-externo-novo'))
        html = resposta.content.decode('utf-8')
        script = 'function pedidoExterno()' + html.split(
            'function pedidoExterno()', 1,
        )[1].split('</script>', 1)[0]
        script += r'''
const assert = require('node:assert/strict');
const pedido = pedidoExterno();
pedido.produtos = [{id: 1}];
pedido.buscaProdutoExecutada = true;
pedido.buscandoProdutos = true;
pedido.categoriaId = '7';
let buscas = 0;
pedido.buscarProdutos = () => { buscas += 1; };
pedido.selecionarCategoria('destaques');
assert.equal(pedido.categoriaId, 'destaques');
assert.equal(buscas, 1);
pedido.produtos = [{id: 2}];
pedido.buscaProdutoExecutada = true;
pedido.fecharBuscaProdutos();
assert.deepEqual(pedido.produtos, []);
assert.equal(pedido.buscaProdutoExecutada, false);
'''

        resultado = subprocess.run(
            [shutil.which('node')], input=script, text=True, encoding='utf-8',
            capture_output=True, timeout=20,
        )

        self.assertEqual(resultado.returncode, 0, resultado.stdout + resultado.stderr)
        self.assertContains(resposta, '@click.outside="fecharBuscaProdutos()"')
        self.assertContains(resposta, 'grid-template-columns:minmax(0,1fr) minmax(0,1fr)')
        self.assertContains(resposta, '<option value="destaques">Destaques</option>')
        self.assertContains(resposta, "if(this.categoriaId==='destaques')url+='&destaques=1'")
        self.assertNotContains(resposta, 'fv-highlights-list')

    def test_pedido_externo_usa_preco_do_servidor_e_nao_cai_no_pdv(self):
        resposta = self.criar_pedido_pela_tela()

        self.assertEqual(resposta.status_code, 200, resposta.content)
        pedido = PedidoVenda.objects.get(pk=resposta.json()['pedido_id'])
        item = pedido.itens.get()
        self.assertEqual(pedido.origem, PedidoVenda.Origem.EXTERNO)
        self.assertEqual(pedido.status, PedidoVenda.Status.AGUARDANDO_APROVACAO)
        self.assertEqual(pedido.prioridade, PedidoVenda.Prioridade.URGENTE)
        self.assertEqual(pedido.forma_pagamento, self.forma)
        self.assertEqual(pedido.condicao_pagamento, self.condicao)
        self.assertEqual(item.valor_unitario, Decimal('7.5000'))
        self.assertEqual(pedido.valor_total, Decimal('15.00'))
        self.assertEqual(VendaPDV.objects.count(), 0)
        self.assertEqual(
            resposta.json()['redirect_url'],
            f'{reverse("vendas:pedido-detail", args=[pedido.pk])}?criado=1',
        )

        detalhe = self.client.get(resposta.json()['redirect_url'])
        self.assertContains(detalhe, 'Pedido enviado para análise')
        self.assertContains(detalhe, reverse('vendas:pedido-pdf', args=[pedido.pk]))
        self.assertContains(detalhe, reverse('vendas:pedido-enviar-pdf', args=[pedido.pk]))

    def test_pdf_do_pedido_pode_ser_baixado(self):
        pedido = PedidoVenda.objects.get(pk=self.criar_pedido_pela_tela().json()['pedido_id'])

        resposta = self.client.get(reverse('vendas:pedido-pdf', args=[pedido.pk]))

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta['Content-Type'], 'application/pdf')
        self.assertIn(f'pedido-{pedido.numero_pedido}.pdf', resposta['Content-Disposition'])
        self.assertTrue(resposta.content.startswith(b'%PDF-'))
        self.assertGreater(len(resposta.content), 2000)

    @patch('apps.vendas.services.pedido_pdf.EvolutionClient')
    def test_pdf_do_pedido_pode_ser_enviado_ao_whatsapp(self, evolution_client):
        self.cliente.celular = '(84) 99999-1234'
        self.cliente.save(update_fields=['celular', 'updated_at'])
        ConfiguracaoWhatsApp.objects.create(
            filial=self.filial,
            instancia='pedido-pdf-teste',
            status=ConfiguracaoWhatsApp.Status.CONECTADO,
            ativo=True,
        )
        evolution_client.return_value.enviar_documento.return_value = {
            'key': {'id': 'mensagem-pdf-123'},
        }
        pedido = PedidoVenda.objects.get(pk=self.criar_pedido_pela_tela().json()['pedido_id'])

        resposta = self.client.post(reverse('vendas:pedido-enviar-pdf', args=[pedido.pk]))

        self.assertRedirects(resposta, reverse('vendas:pedido-detail', args=[pedido.pk]))
        chamada = evolution_client.return_value.enviar_documento.call_args
        self.assertEqual(chamada.args[0], '5584999991234')
        self.assertTrue(chamada.args[1].startswith(b'%PDF-'))
        self.assertEqual(chamada.args[2], f'pedido-{pedido.numero_pedido}.pdf')
        mensagem = MensagemWhatsApp.objects.get(tipo='pdf_pedido_venda')
        self.assertEqual(mensagem.status, 'enviada')
        self.assertEqual(mensagem.identificador_externo, 'mensagem-pdf-123')

    def test_kanban_nao_mistura_pedidos_internos(self):
        interno = VendaService.criar_pedido(
            self.filial,
            self.usuario,
            self.cliente,
        )
        externo_id = self.criar_pedido_pela_tela().json()['pedido_id']
        externo = PedidoVenda.objects.get(pk=externo_id)

        resposta = self.client.get(reverse('vendas:pedido-kanban'))

        self.assertContains(resposta, externo.numero_pedido)
        self.assertNotContains(resposta, interno.numero_pedido)
        self.assertContains(resposta, '--fk-card:#272a2f')
        self.assertContains(resposta, 'body.tema-claro .fk-shell')
        self.assertNotContains(resposta, 'var(--erp-card-bg,#fff)')

    def test_kanban_aprovacao_e_fila_de_separacao_fecham_o_fluxo(self):
        self.abastecer()
        criado = self.criar_pedido_pela_tela().json()
        pedido = PedidoVenda.objects.get(pk=criado['pedido_id'])

        kanban = self.client.get(reverse('vendas:pedido-kanban'))
        self.assertContains(kanban, pedido.numero_pedido)
        self.assertNotContains(kanban, 'Analisar e liberar')
        self.assertContains(kanban, 'Abra o pedido para analisar e aprovar')

        detalhe = self.client.get(reverse('vendas:pedido-detail', args=[pedido.pk]))
        self.assertContains(detalhe, 'Aprovar e liberar para separação')
        self.assertContains(detalhe, self.usuario.nome)

        aprovacao = self.client.post(
            reverse('vendas:pedido-aprovar-externo', args=[pedido.pk]),
        )
        self.assertRedirects(aprovacao, reverse('vendas:pedido-detail', args=[pedido.pk]))
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, PedidoVenda.Status.CONFIRMADO)
        estoque = Estoque.objects.get(produto=self.produto, filial=self.filial)
        self.assertEqual(estoque.quantidade_reservada, Decimal('2.000'))

        fila = self.client.get(reverse('vendas:pedido-separacao'))
        self.assertContains(fila, pedido.numero_pedido)
        self.assertContains(fila, 'Concluir separação')

        separacao = self.client.post(
            reverse('vendas:pedido-separar', args=[pedido.pk]),
            {
                'origem': 'fila-separacao',
                'conferencia_detalhada': '1',
                f'item_{pedido.itens.get().pk}_conferido': 'on',
                f'item_{pedido.itens.get().pk}_quantidade': '1',
                f'item_{pedido.itens.get().pk}_substituto_id': self.produto_substituto.pk,
                f'item_{pedido.itens.get().pk}_substituto_quantidade': '1',
                f'item_{pedido.itens.get().pk}_observacao': 'Cliente aceitou o substituto.',
            },
        )
        self.assertRedirects(separacao, reverse('vendas:pedido-separacao'))
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, PedidoVenda.Status.EM_SEPARACAO)
        self.assertTrue(pedido.separacoes.exists())
        item = pedido.itens.get(produto=self.produto)
        estoque_produto = Estoque.objects.get(produto=self.produto, filial=self.filial)
        estoque_substituto = Estoque.objects.get(
            produto=self.produto_substituto, filial=self.filial,
        )
        self.assertEqual(item.quantidade_solicitada, Decimal('2.000'))
        self.assertEqual(item.quantidade_atendida, Decimal('1.000'))
        self.assertTrue(item.separacao_conferida)
        self.assertEqual(estoque_produto.quantidade_atual, Decimal('19.000'))
        self.assertEqual(estoque_produto.quantidade_reservada, Decimal('0.000'))
        substituto = pedido.itens.get(produto=self.produto_substituto)
        self.assertEqual(substituto.item_substituido, item)
        self.assertEqual(substituto.quantidade_atendida, Decimal('1.000'))
        self.assertEqual(estoque_substituto.quantidade_atual, Decimal('19.000'))
        self.assertEqual(estoque_substituto.quantidade_reservada, Decimal('0.000'))

        fila_atualizada = self.client.get(reverse('vendas:pedido-separacao'))
        self.assertContains(fila_atualizada, 'Separado')

    def test_faturamento_externo_gera_contas_a_receber_e_entrega(self):
        self.abastecer()
        pedido = PedidoVenda.objects.get(pk=self.criar_pedido_pela_tela().json()['pedido_id'])
        self.client.post(reverse('vendas:pedido-aprovar-externo', args=[pedido.pk]))
        item = pedido.itens.get()
        self.client.post(reverse('vendas:pedido-separar', args=[pedido.pk]), {
            'conferencia_detalhada': '1',
            f'item_{item.pk}_conferido': 'on',
            f'item_{item.pk}_quantidade': '2',
        })
        saidas_antes = MovimentacaoEstoque.objects.filter(
            documento_tipo=MovimentacaoEstoque.DocumentoTipo.PEDIDO_VENDA,
            documento_id=pedido.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.SAIDA,
        ).count()
        self.assertEqual(saidas_antes, 1)
        Estoque.objects.filter(produto=self.produto, filial=self.filial).update(
            quantidade_atual=Decimal('0'),
            quantidade_disponivel=Decimal('0'),
        )

        faturamento = self.client.post(reverse('vendas:pedido-faturar', args=[pedido.pk]), {
            'forma_pagamento': self.forma.pk,
            'condicao_pagamento': self.condicao.pk,
            'prazo_pagamento_dias': '15',
            'referencia_pagamento': 'Boleto solicitado pelo cliente',
        })
        self.assertRedirects(faturamento, reverse('vendas:pedido-detail', args=[pedido.pk]))
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, PedidoVenda.Status.FATURADO)
        self.assertEqual(
            MovimentacaoEstoque.objects.filter(
                documento_tipo=MovimentacaoEstoque.DocumentoTipo.PEDIDO_VENDA,
                documento_id=pedido.pk,
                tipo_operacao=MovimentacaoEstoque.TipoOperacao.SAIDA,
            ).count(),
            saidas_antes,
        )
        titulos = ContaReceber.objects.filter(documento_tipo='pedido_venda', documento_id=pedido.pk)
        self.assertEqual(titulos.count(), 2)
        self.assertTrue(all(t.status == StatusContaReceber.ABERTO for t in titulos))
        self.assertEqual(sum((t.valor_original for t in titulos), Decimal('0')), pedido.valor_total)

        self.client.post(reverse('vendas:pedido-iniciar-entrega', args=[pedido.pk]), {
            'responsavel_entrega': 'João Motorista',
            'observacao_entrega': 'Rota norte',
        })
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, PedidoVenda.Status.EM_ENTREGA)
        self.client.post(reverse('vendas:pedido-concluir-entrega', args=[pedido.pk]))
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, PedidoVenda.Status.ENTREGUE)

    def test_separacao_externa_bloqueia_se_saldo_sumir_apos_analise(self):
        self.abastecer()
        pedido = PedidoVenda.objects.get(pk=self.criar_pedido_pela_tela().json()['pedido_id'])
        self.client.post(reverse('vendas:pedido-aprovar-externo', args=[pedido.pk]))
        pedido.refresh_from_db()
        item = pedido.itens.get()
        MovimentacaoService.registrar_saida_fefo(
            produto_id=self.produto.pk,
            filial_id=self.filial.pk,
            quantidade=Decimal('20'),
            usuario_id=self.usuario.pk,
            deposito_id=Estoque.objects.get(
                produto=self.produto, filial=self.filial,
            ).deposito_id,
        )

        with self.assertRaises(EstoqueInsuficienteError):
            VendaService.separar_pedido(pedido, self.usuario, [{
                'item_id': item.pk,
                'conferido': True,
                'quantidade_separada': item.quantidade,
            }])

        pedido.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(pedido.status, PedidoVenda.Status.CONFIRMADO)
        self.assertEqual(item.quantidade_reservada, Decimal('2.000'))
        self.assertFalse(pedido.separacoes.exists())

    def test_cancelamento_externo_devolve_estoque_baixado_na_separacao(self):
        self.abastecer()
        pedido = PedidoVenda.objects.get(pk=self.criar_pedido_pela_tela().json()['pedido_id'])
        VendaService.aprovar_e_confirmar_pedido(pedido, self.usuario)
        item = pedido.itens.get()
        VendaService.separar_pedido(pedido, self.usuario, [{
            'item_id': item.pk,
            'conferido': True,
            'quantidade_separada': item.quantidade,
        }])
        estoque = Estoque.objects.get(produto=self.produto, filial=self.filial)
        self.assertEqual(estoque.quantidade_atual, Decimal('18.000'))

        VendaService.cancelar_pedido(pedido, self.usuario, 'Cliente desistiu após separação')

        pedido.refresh_from_db()
        estoque.refresh_from_db()
        self.assertEqual(pedido.status, PedidoVenda.Status.CANCELADO)
        self.assertEqual(estoque.quantidade_atual, Decimal('20.000'))
        self.assertEqual(estoque.quantidade_reservada, Decimal('0.000'))
        self.assertTrue(MovimentacaoEstoque.objects.filter(
            documento_tipo=MovimentacaoEstoque.DocumentoTipo.PEDIDO_VENDA,
            documento_id=pedido.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            observacao='Retorno ao estoque por cancelamento após separação.',
        ).exists())

    def test_payload_malformado_retorna_erro_json(self):
        resposta = self.client.post(
            reverse('vendas:pedido-externo-novo'),
            data=json.dumps([]),
            content_type='application/json',
        )

        self.assertEqual(resposta.status_code, 400)
        self.assertIn('inválidos', resposta.json()['erro'])
