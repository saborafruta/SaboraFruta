import json
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.models import Cliente, ClienteFilial
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import DadosInvalidosError, EstoqueInsuficienteError
from apps.estoque.models import Estoque, MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.financeiro.constants.enums import TipoFormaPagamento
from apps.financeiro.models import FormaPagamento
from apps.pdv.models import Caixa, ItemVendaPDV, SessaoPDV, VendaPDV
from apps.pdv.services.produto_vendavel_service import ProdutoVendavelService
from apps.pdv.services.venda_pdv_service import VendaPDVService
from apps.produtos.models import (
    BrindeProduto, BrindeProdutoItem, CondicaoQuantidade, KitCategoria,
    KitCategoriaRegra, KitProduto, KitProdutoItem, Produto, ProdutoFilial,
    PromocaoQuantidade, PromocaoQuantidadeFaixa, TipoDesconto, UnidadeMedida,
    UnidadeMedidaFilial, TabelaPreco, TabelaPrecoFilial, ItemTabelaPreco,
)


class VendaPDVServiceTests(TestCase):
    def test_observacao_item_persiste_na_venda_e_orcamento(self):
        produto = self.criar_produto('Produto com observação')
        self.abastecer(produto, '5')
        nota = '  Embalar separado — cliente <VIP>  '
        venda = VendaPDVService.finalizar_venda(
            sessao=self.sessao, filial=self.filial, usuario=self.usuario,
            itens=[{'produto_id': produto.pk, 'quantidade': '1', 'obs': nota}],
            pagamentos=[{'forma_id': self.forma.pk, 'valor': '10'}],
        )
        item = venda.itens.get()
        self.assertEqual(item.observacao, 'Embalar separado — cliente <VIP>')

        self.client.force_login(self.usuario)
        session = self.client.session
        session['filial_ativa_id'] = self.filial.pk
        session.save()
        detalhe = self.client.get(reverse('pdv:api_venda_detalhe', args=[venda.pk])).json()
        self.assertEqual(detalhe['itens'][0]['observacao'], item.observacao)
        resposta = self.client.post(
            reverse('pdv:api_venda_orcamento'),
            data=json.dumps({'cliente_id': None, 'itens': [{
                'produto_id': produto.pk, 'quantidade': 1, 'valor_unitario': 10,
                'observacao': 'Orçamento em negrito',
            }]}), content_type='application/json',
        )
        self.assertEqual(resposta.status_code, 200, resposta.content)
        orcamento = VendaPDV.objects.get(pk=resposta.json()['venda_id'])
        self.assertEqual(orcamento.itens.get().observacao, 'Orçamento em negrito')
        detalhe_orcamento = self.client.get(reverse('pdv:api_orcamento_detalhe', args=[orcamento.pk])).json()
        self.assertEqual(detalhe_orcamento['itens'][0]['obs'], 'Orçamento em negrito')
        self.assertEqual(detalhe_orcamento['itens'][0]['estoque_disponivel'], 4)
        self.assertEqual(detalhe_orcamento['itens'][0]['preco_tabela'], 10)
        # The cart projection must start from the current stock, never a persisted snapshot.
        self.abastecer(produto, '2')
        atualizado = self.client.get(reverse('pdv:api_orcamento_detalhe', args=[orcamento.pk])).json()
        self.assertEqual(atualizado['itens'][0]['estoque_disponivel'], 6)

    def test_link_publico_com_desconto_e_isolamento(self):
        from urllib.parse import urlsplit

        produto = self.criar_produto('Açúcar & Café')
        venda = VendaPDV.objects.create(
            filial=self.filial, usuario=self.usuario, numero_venda=91,
            data_venda=timezone.now(), status='finalizada', valor_total=280,
        )
        ItemVendaPDV.objects.create(
            venda_pdv=venda, produto=produto, numero_item=1, unidade_medida='UN',
            quantidade=4, valor_unitario=100, desconto_valor=120,
            desconto_percentual=30, valor_total=280,
            observacao='Separar por tamanho — urgente',
        )
        endpoint = reverse('pdv:api_comprovante_link', args=[venda.pk])
        anonymous = Client()
        self.assertNotEqual(anonymous.post(endpoint).status_code, 200)
        self.client.force_login(self.usuario)
        self.assertEqual(self.client.get(endpoint).status_code, 405)
        resposta = self.client.post(endpoint)
        self.assertEqual(resposta.status_code, 200)
        url = urlsplit(resposta.json()['url']).path
        self.assertEqual(resposta.json()['url'], self.client.post(endpoint).json()['url'])
        venda.refresh_from_db()
        self.assertEqual(len(venda.comprovante_token), 43)
        page = anonymous.get(url)
        self.assertContains(page, 'Desconto 30,00%')
        self.assertContains(page, '120,00')
        self.assertContains(page, '400,00')
        self.assertContains(page, '280,00')
        self.assertContains(page, 'Açúcar &amp; Café')
        self.assertContains(page, '<div class="observacao">Observação: Separar por tamanho — urgente</div>', html=True)
        self.assertNotContains(page, self.usuario.email)
        self.assertEqual(page['Cache-Control'], 'private, no-store')
        self.assertEqual(page['Referrer-Policy'], 'no-referrer')
        pdf = anonymous.get(url + 'pdf/')
        self.assertEqual(pdf['Content-Type'], 'application/pdf')
        self.assertTrue(pdf.content.startswith(b'%PDF-'))
        self.assertGreater(len(pdf.content), 1000)
        detalhe = self.client.get(reverse('pdv:api_venda_detalhe', args=[venda.pk])).json()
        self.assertEqual(detalhe['itens'][0]['desconto_valor'], 120)
        outra = Filial.objects.create(empresa=self.empresa, nome_fantasia='Outra', cnpj='52345678000194', uf='RN')
        venda.filial = outra
        venda.save(update_fields=['filial'])
        self.assertEqual(self.client.post(endpoint).status_code, 404)
        for status in ['pendente', 'orcamento', 'cancelada', 'aberta']:
            venda.status = status
            venda.save(update_fields=['status'])
            self.assertEqual(anonymous.get(url).status_code, 404)
            self.assertEqual(anonymous.get(url + 'pdf/').status_code, 404)
        self.assertEqual(anonymous.get('/comprovante/91/').status_code, 404)
        self.assertEqual(anonymous.get('/comprovante/' + 'x'*43 + '/').status_code, 404)

    def test_forma_oculta_nao_finaliza_venda_de_tela_desatualizada(self):
        produto = self.criar_produto()
        self.abastecer(produto, '10')
        self.forma.exibir_no_pdv = False
        self.forma.save(update_fields=['exibir_no_pdv'])
        with self.assertRaisesMessage(DadosInvalidosError, 'oculta no PDV'):
            VendaPDVService.finalizar_venda(
                sessao=self.sessao, filial=self.filial, usuario=self.usuario,
                itens=[{'produto_id': produto.pk, 'quantidade': '1', 'valor_unitario': '10.00'}],
                pagamentos=[{'forma_id': self.forma.pk, 'valor': '10.00'}],
            )
        self.assertFalse(VendaPDV.objects.filter(sessao_pdv=self.sessao).exists())

    def test_busca_produtos_permite_carregar_resultados_apos_os_primeiros_vinte(self):
        for indice in range(21):
            self.criar_produto(f'Polo paginação {indice:02d}')
        self.client.force_login(self.usuario)
        session = self.client.session
        session['filial_ativa_id'] = self.filial.pk
        session.save()

        primeira = self.client.get(reverse('pdv:api_produtos'), {'q': 'Polo'}).json()
        segunda = self.client.get(reverse('pdv:api_produtos'), {'q': 'Polo', 'pagina': 2}).json()

        self.assertEqual(len(primeira['produtos']), 20)
        self.assertIn('estoque_disponivel', primeira['produtos'][0])
        self.assertTrue(primeira['tem_mais'])
        self.assertEqual(primeira['pagina'], 1)
        self.assertEqual(len(segunda['produtos']), 1)
        self.assertFalse(segunda['tem_mais'])
        self.assertEqual(segunda['pagina'], 2)
        self.assertFalse({item['id'] for item in primeira['produtos']} & {item['id'] for item in segunda['produtos']})

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social="Empresa PDV LTDA",
            nome_fantasia="Empresa PDV",
            cnpj="52345678000191",
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa,
            razao_social="Filial PDV",
            nome_fantasia="Matriz",
            cnpj="52345678000192",
            uf="RN",
        )
        cls.perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa,
            nome="Operador PDV",
            is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email="pdv-estoque@inoovated.com",
            nome="Usuario PDV",
            password="teste1234",
            empresa=cls.empresa,
            filial=cls.filial,
            perfil=cls.perfil,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa,
            sigla="UN",
            descricao="Unidade",
            tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        cls.caixa = Caixa.objects.create(filial=cls.filial, numero=1, descricao="Caixa 1")
        cls.forma = FormaPagamento.objects.create(
            empresa=cls.empresa,
            descricao="Dinheiro",
            tipo=TipoFormaPagamento.DINHEIRO,
        )

    def setUp(self):
        self.sessao = SessaoPDV.objects.create(
            filial=self.filial,
            caixa=self.caixa,
            usuario=self.usuario,
            valor_abertura=Decimal("0.00"),
            status="aberto",
        )

    def criar_produto(self, descricao="Polpa PDV"):
        produto = Produto.objects.create(
            filial=self.filial,
            unidade_medida=self.unidade,
            descricao=descricao,
            ncm="20089900",
            controla_lote=False,
            permite_venda_sem_estoque=False,
            preco_venda=Decimal("10.00"),
            preco_custo=Decimal("4.00"),
        )
        ProdutoFilial.objects.create(produto=produto, filial=self.filial)
        return produto

    def abastecer(self, produto, quantidade="10"):
        return MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk,
            filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal(quantidade),
            usuario_id=self.usuario.pk,
            valor_unitario=Decimal("4.00"),
        )

    def aplicar_promocoes(self, produto):
        ProdutoFilial.objects.filter(produto=produto, filial=self.filial).update(
            preco_promocional=Decimal("7.00"),
            preco_promocional_ativo=True,
            promocao_tipo_desconto="preco_final",
            promocao_valor_desconto=Decimal("7.00"),
            promocao_dias_semana="0,1,2,3,4,5,6",
        )
        kit = KitCategoria.objects.create(
            filial=self.filial,
            nome="Desconto geral PDV",
            permite_preco_promocional=False,
        )
        KitCategoriaRegra.objects.create(
            kit=kit,
            quantidade_minima=Decimal("1"),
            tipo_desconto=TipoDesconto.PERCENTUAL,
            valor_desconto=Decimal("50.00"),
        )

    def test_finalizar_venda_usa_preco_vivo_e_baixa_estoque(self):
        produto = self.criar_produto()
        self.abastecer(produto, "10")
        self.aplicar_promocoes(produto)

        venda = VendaPDVService.finalizar_venda(
            sessao=self.sessao,
            filial=self.filial,
            usuario=self.usuario,
            itens=[{
                "produto_id": produto.pk,
                "quantidade": "2",
                "valor_unitario": "99.00",
            }],
            pagamentos=[{"forma_id": self.forma.pk, "valor": "10.00"}],
        )

        item = ItemVendaPDV.objects.get(venda_pdv=venda)
        estoque = Estoque.objects.get(produto=produto, filial=self.filial)
        movimento = MovimentacaoEstoque.objects.get(pk=item.movimentacoes_estoque_ids[0])

        self.assertEqual(venda.valor_total, Decimal("10.00"))
        self.assertEqual(item.valor_unitario, Decimal("5.0000"))
        self.assertEqual(item.valor_unitario_tabela, Decimal("10.0000"))
        self.assertEqual(item.custo_unitario_snapshot, Decimal("4.0000"))
        self.assertEqual(item.preco_origem, "categoria")
        self.assertTrue(item.estoque_baixado)
        self.assertEqual(estoque.quantidade_atual, Decimal("8.000"))
        self.assertEqual(estoque.quantidade_disponivel, Decimal("8.000"))
        self.assertEqual(movimento.documento_tipo, MovimentacaoEstoque.DocumentoTipo.NFCE)
        self.assertEqual(movimento.documento_id, venda.pk)

    def test_finalizar_venda_respeita_preco_normal_escolhido_no_modal(self):
        produto = self.criar_produto("Produto com escolha")
        self.abastecer(produto, "5")
        self.aplicar_promocoes(produto)

        venda = VendaPDVService.finalizar_venda(
            sessao=self.sessao,
            filial=self.filial,
            usuario=self.usuario,
            itens=[{
                "produto_id": produto.pk,
                "quantidade": "2",
                "oferta_tipo": "normal",
            }],
            pagamentos=[{"forma_id": self.forma.pk, "valor": "20.00"}],
        )

        item = ItemVendaPDV.objects.get(venda_pdv=venda)
        self.assertEqual(item.valor_unitario, Decimal("10.0000"))
        self.assertEqual(item.preco_origem, "normal")
        self.assertEqual(venda.valor_total, Decimal("20.00"))

    def test_tabela_do_cliente_precifica_item_e_faz_fallback_no_ausente(self):
        produto_tabela = self.criar_produto("Produto na tabela")
        produto_padrao = self.criar_produto("Produto fora da tabela")
        produto_padrao.preco_venda = Decimal("12.00")
        produto_padrao.save(update_fields=["preco_venda"])
        self.abastecer(produto_tabela, "5")
        self.abastecer(produto_padrao, "5")

        tabela = TabelaPreco.objects.create(
            filial=self.filial,
            descricao="Atacado especial",
            tipo=TabelaPreco.Tipo.ATACADO,
        )
        TabelaPrecoFilial.objects.create(tabela=tabela, filial=self.filial)
        ItemTabelaPreco.objects.create(
            tabela=tabela,
            produto=produto_tabela,
            preco_unitario=Decimal("6.00"),
            quantidade_minima=Decimal("0"),
        )
        cliente = Cliente.objects.create(
            filial=self.filial,
            tipo_pessoa="J",
            razao_social="Cliente Atacado LTDA",
            tabela_preco=tabela,
        )
        ClienteFilial.objects.create(cliente=cliente, filial=self.filial)

        venda = VendaPDVService.finalizar_venda(
            sessao=self.sessao,
            filial=self.filial,
            usuario=self.usuario,
            cliente_id=cliente.pk,
            itens=[
                {"produto_id": produto_tabela.pk, "quantidade": "1"},
                {"produto_id": produto_padrao.pk, "quantidade": "1"},
            ],
            pagamentos=[{"forma_id": self.forma.pk, "valor": "18.00"}],
        )

        itens = {
            item.produto_id: item
            for item in ItemVendaPDV.objects.filter(venda_pdv=venda)
        }
        self.assertEqual(itens[produto_tabela.pk].valor_unitario, Decimal("6.0000"))
        self.assertEqual(itens[produto_tabela.pk].preco_origem, "tabela_cliente")
        self.assertEqual(itens[produto_padrao.pk].valor_unitario, Decimal("12.0000"))
        self.assertEqual(venda.valor_total, Decimal("18.00"))

    def test_finalizar_venda_respeita_desconto_manual_do_item(self):
        produto = self.criar_produto("Produto com desconto")
        self.abastecer(produto, "5")

        venda = VendaPDVService.finalizar_venda(
            sessao=self.sessao,
            filial=self.filial,
            usuario=self.usuario,
            itens=[{
                "produto_id": produto.pk,
                "quantidade": "2",
                "desconto_valor": "3.00",
            }],
            pagamentos=[{"forma_id": self.forma.pk, "valor": "17.00"}],
        )

        item = ItemVendaPDV.objects.get(venda_pdv=venda)
        self.assertEqual(venda.valor_subtotal, Decimal("17.00"))
        self.assertEqual(venda.valor_total, Decimal("17.00"))
        self.assertEqual(item.desconto_valor, Decimal("3.00"))
        self.assertEqual(item.desconto_percentual, Decimal("15.00"))
        self.assertEqual(item.valor_total, Decimal("17.00"))

    def test_preco_manual_abaixo_do_custo_e_permitido(self):
        produto = self.criar_produto("Produto protegido por custo")
        self.abastecer(produto, "2")

        venda = VendaPDVService.finalizar_venda(
            sessao=self.sessao, filial=self.filial, usuario=self.usuario,
            itens=[{"produto_id": produto.pk, "quantidade": "1", "oferta_tipo": "normal", "preco_manual": "3.00"}],
            pagamentos=[{"forma_id": self.forma.pk, "valor": "3.00"}],
        )
        item = venda.itens.get()
        self.assertEqual(item.valor_unitario, Decimal("3.00"))
        self.assertEqual(item.custo_unitario_snapshot, Decimal("4.00"))
        self.assertEqual(venda.valor_total, Decimal("3.00"))

    def test_preco_manual_negativo_continua_bloqueado(self):
        produto = self.criar_produto()
        self.abastecer(produto, "2")
        for preco in ("-0.01", "-1"):
            with self.subTest(preco=preco), self.assertRaisesMessage(DadosInvalidosError, "Preco manual nao pode ser negativo"):
                VendaPDVService.finalizar_venda(
                    sessao=self.sessao, filial=self.filial, usuario=self.usuario,
                    itens=[{"produto_id": produto.pk, "quantidade": "1", "preco_manual": preco}],
                    pagamentos=[{"forma_id": self.forma.pk, "valor": "3.00"}],
                )

    def test_item_gratis_finaliza_sem_pagamento_e_baixa_estoque(self):
        produto = self.criar_produto('Produto grátis')
        self.abastecer(produto, '3')
        venda = VendaPDVService.finalizar_venda(
            sessao=self.sessao, filial=self.filial, usuario=self.usuario,
            itens=[{'produto_id': produto.pk, 'quantidade': '2', 'preco_manual': 0}],
            pagamentos=[],
        )
        item = venda.itens.get()
        self.assertEqual(item.valor_unitario, 0)
        self.assertEqual(item.valor_total, 0)
        self.assertEqual(item.oferta_contexto['preco_manual'], 0)
        self.assertEqual(item.custo_unitario_snapshot, Decimal('4'))
        self.assertEqual(venda.valor_total, 0)
        self.assertFalse(venda.pagamentos.exists())
        self.assertEqual(Estoque.objects.get(produto=produto, filial=self.filial).quantidade_atual, 1)

    def test_venda_mista_cobra_apenas_itens_pagos(self):
        produto = self.criar_produto()
        self.abastecer(produto, '3')
        venda = VendaPDVService.finalizar_venda(
            sessao=self.sessao, filial=self.filial, usuario=self.usuario,
            itens=[{'produto_id': produto.pk, 'quantidade': 1, 'preco_manual': 0},
                   {'produto_id': produto.pk, 'quantidade': 1}],
            pagamentos=[{'forma_id': self.forma.pk, 'valor': '10'}],
        )
        self.assertEqual(venda.valor_total, 10)
        self.assertEqual(venda.itens.filter(valor_total=0).count(), 1)

    def test_venda_positiva_sem_pagamento_continua_bloqueada(self):
        produto = self.criar_produto()
        self.abastecer(produto, '2')
        with self.assertRaisesMessage(DadosInvalidosError, 'Informe ao menos uma forma de pagamento'):
            VendaPDVService.finalizar_venda(
                sessao=self.sessao, filial=self.filial, usuario=self.usuario,
                itens=[{'produto_id': produto.pk, 'quantidade': 1}], pagamentos=[],
            )
        self.assertEqual(Estoque.objects.get(produto=produto, filial=self.filial).quantidade_atual, 2)

    def test_zero_manual_persiste_no_orcamento_e_pendente(self):
        produto = self.criar_produto()
        self.client.force_login(self.usuario)
        session = self.client.session
        session['filial_ativa_id'] = self.filial.pk
        session.save()
        for endpoint, detalhe in (('api_venda_orcamento', 'api_orcamento_detalhe'), ('api_venda_pendente', 'api_pendente_detalhe')):
            with self.subTest(endpoint=endpoint):
                response = self.client.post(reverse('pdv:' + endpoint), data=json.dumps({'itens': [{
                    'produto_id': produto.pk, 'quantidade': 1, 'valor_unitario': 0,
                    'preco_manual': 0, '_precoOriginal': 10,
                }]}), content_type='application/json')
                self.assertEqual(response.status_code, 200, response.content)
                venda = VendaPDV.objects.get(pk=response.json()['venda_id'])
                item = self.client.get(reverse('pdv:' + detalhe, args=[venda.pk])).json()['itens'][0]
                self.assertEqual(item['valor_total'], 0)
                self.assertEqual(item['valor_unitario'], 0)
                self.assertEqual(item['preco_manual'], 0)
                self.assertEqual(item['preco_original'], 10)

    def test_finalizar_venda_sem_estoque_faz_rollback(self):
        produto = self.criar_produto("Produto sem saldo")
        self.abastecer(produto, "1")

        # `forcar_estoque_negativo` e' True por padrao de proposito: no balcao
        # a mercadoria ja' esta' na mao do cliente, e no fechamento de comanda
        # a comida ja' foi servida -- recusar a venda por divergencia de saldo
        # travaria o caixa. Quem NAO forca e' que recebe a recusa, e e' esse
        # caminho que este teste cobre.
        with self.assertRaises(EstoqueInsuficienteError):
            VendaPDVService.finalizar_venda(
                sessao=self.sessao,
                filial=self.filial,
                usuario=self.usuario,
                itens=[{"produto_id": produto.pk, "quantidade": "2"}],
                pagamentos=[{"forma_id": self.forma.pk, "valor": "20.00"}],
                forcar_estoque_negativo=False,
            )

        estoque = Estoque.objects.get(produto=produto, filial=self.filial)
        self.assertEqual(estoque.quantidade_atual, Decimal("1.000"))
        self.assertEqual(VendaPDV.objects.count(), 0)
        self.assertEqual(ItemVendaPDV.objects.count(), 0)

    def test_produto_vendavel_retorna_contrato_claro_para_pdv(self):
        produto = self.criar_produto("Produto contrato")
        self.abastecer(produto, "6")
        self.aplicar_promocoes(produto)

        contrato = ProdutoVendavelService.consultar(
            produto=produto,
            filial=self.filial,
            quantidade=Decimal("2"),
        )

        self.assertTrue(contrato["pode_vender"])
        self.assertEqual(contrato["saldo_disponivel"], Decimal("6.000"))
        self.assertEqual(contrato["custo_atual"], Decimal("4.0000"))
        self.assertEqual(contrato["preco_aplicado"], Decimal("5.0000"))
        self.assertEqual(contrato["margem_percentual"], Decimal("20.00"))
        self.assertEqual(contrato["status_comercial"], "pendente_fiscal_cadastro")
        self.assertFalse(contrato["lote_obrigatorio"])
        self.assertTrue(contrato["promocoes_aplicaveis"])

    def test_finalizar_venda_bloqueia_produto_sem_preco_ou_custo_valido(self):
        produto_sem_preco = self.criar_produto("Produto sem preco")
        produto_sem_preco.preco_venda = Decimal("0")
        produto_sem_preco.save(update_fields=["preco_venda"])
        self.abastecer(produto_sem_preco, "2")

        with self.assertRaises(DadosInvalidosError):
            VendaPDVService.finalizar_venda(
                sessao=self.sessao,
                filial=self.filial,
                usuario=self.usuario,
                itens=[{"produto_id": produto_sem_preco.pk, "quantidade": "1"}],
                pagamentos=[{"forma_id": self.forma.pk, "valor": "1.00"}],
            )

    def test_produto_sem_custo_avisa_mas_nao_impede_a_venda(self):
        """
        SEM PRECO NAO SE VENDE; SEM CUSTO SE VENDE AVISANDO. Custo em branco
        so' impede calcular margem e CMV -- travar o caixa por uma lacuna do
        cadastro pararia a venda por um problema de retaguarda.
        """
        produto_sem_custo = self.criar_produto("Produto sem custo")
        produto_sem_custo.preco_custo = Decimal("0")
        produto_sem_custo.preco_custo_medio = Decimal("0")
        produto_sem_custo.save(update_fields=["preco_custo", "preco_custo_medio"])

        contrato = ProdutoVendavelService.validar_venda(
            produto=produto_sem_custo,
            filial=self.filial,
            quantidade=Decimal("1"),
        )

        self.assertTrue(contrato["pode_vender"])
        self.assertIn(
            "custo_atual_invalido",
            [item["codigo"] for item in contrato["alertas"]],
            "a venda passou sem nem avisar que o custo esta em branco",
        )

    def test_promocao_com_margem_negativa_permite_venda(self):
        produto = self.criar_produto("Produto margem negativa")
        self.abastecer(produto, "5")
        ProdutoFilial.objects.filter(produto=produto, filial=self.filial).update(
            preco_promocional=Decimal("3.00"),
            preco_promocional_ativo=True,
            promocao_tipo_desconto="preco_final",
            promocao_valor_desconto=Decimal("3.00"),
            promocao_dias_semana="0,1,2,3,4,5,6",
        )

        contrato = ProdutoVendavelService.consultar(produto=produto, filial=self.filial)
        self.assertTrue(contrato['pode_vender'])
        self.assertIn('margem_negativa', {alerta['codigo'] for alerta in contrato['alertas']})
        venda = VendaPDVService.finalizar_venda(
            sessao=self.sessao, filial=self.filial, usuario=self.usuario,
            itens=[{"produto_id": produto.pk, "quantidade": "1"}],
            pagamentos=[{"forma_id": self.forma.pk, "valor": "3.00"}],
        )
        self.assertEqual(venda.valor_total, Decimal("3.00"))

    def test_combo_quantidade_entra_no_preco_vivo_do_pdv(self):
        produto = self.criar_produto("Produto combo")
        self.abastecer(produto, "10")
        combo = PromocaoQuantidade.objects.create(
            filial=self.filial,
            produto=produto,
            nome="Leve 3",
            usar_preco_promocional=False,
        )
        faixa = PromocaoQuantidadeFaixa.objects.create(
            promocao=combo,
            condicao_quantidade=CondicaoQuantidade.A_PARTIR_DE,
            quantidade_minima=Decimal("3"),
            tipo_desconto=TipoDesconto.PRECO_FINAL,
            valor=Decimal("6.00"),
        )

        venda = VendaPDVService.finalizar_venda(
            sessao=self.sessao,
            filial=self.filial,
            usuario=self.usuario,
            itens=[{
                "produto_id": produto.pk,
                "quantidade": "3",
                "oferta_tipo": "combo",
                "promocao_id": combo.pk,
                "faixa_id": faixa.pk,
            }],
            pagamentos=[{"forma_id": self.forma.pk, "valor": "18.00"}],
        )

        item = ItemVendaPDV.objects.get(venda_pdv=venda)
        self.assertEqual(item.valor_unitario, Decimal("6.0000"))
        self.assertEqual(item.preco_origem, "combo")
        self.assertEqual(venda.valor_total, Decimal("18.00"))

    def test_combo_desconto_total_consistente_com_promocao_individual(self):
        from apps.pdv.views.pdv import _ofertas_produto
        from apps.produtos.views.promocao import _valor_combo
        from apps.produtos.services.preco_service import PrecoService
        produto = self.criar_produto('Basqueteira')
        produto.preco_venda = Decimal('89.99')
        produto.save(update_fields=['preco_venda'])
        ProdutoFilial.objects.filter(produto=produto,filial=self.filial).update(
            preco_promocional=Decimal('49.99'),preco_promocional_ativo=True,
            promocao_tipo_desconto='preco_final',promocao_valor_desconto=Decimal('49.99'),
            promocao_dias_semana='0,1,2,3,4,5,6',
        )
        self.abastecer(produto,'10')
        combo=PromocaoQuantidade.objects.create(filial=self.filial,produto=produto,nome='Saldão',usar_preco_promocional=True)
        faixa=PromocaoQuantidadeFaixa.objects.create(promocao=combo,quantidade_minima=3,
            condicao_quantidade=CondicaoQuantidade.A_PARTIR_DE,tipo_desconto=TipoDesconto.VALOR,valor=Decimal('20'))
        for flag, expected, base in [(True,Decimal('129.97'),Decimal('49.99')),(False,Decimal('249.97'),Decimal('89.99'))]:
            combo.usar_preco_promocional=flag
            combo.save(update_fields=['usar_preco_promocional'])
            with self.subTest(usar_promocional=flag):
                oferta=next(o for o in _ofertas_produto(produto,self.filial) if o['tipo']=='combo')
                self.assertEqual(Decimal(str(oferta['total'])),expected)
                self.assertEqual(oferta['acumula_promocao'],flag)
                self.assertEqual(Decimal(str(oferta['preco_base_combo'])),base)
                total_base,total_combo,unitario=_valor_combo(combo,faixa)
                self.assertEqual(total_base,base*3)
                self.assertEqual(total_combo,expected,'Cadastro não deve descontar o próprio combo duas vezes')
                self.assertEqual(Decimal(str(oferta['preco'])),unitario)
        combo.usar_preco_promocional=True
        combo.save(update_fields=['usar_preco_promocional'])
        self.assertEqual(PrecoService.precos_combo_quantidade_vigentes(produto,filial=self.filial,quantidade=2),[])
        self.assertEqual(PrecoService.precos_combo_quantidade_vigentes(produto,filial=self.filial,quantidade=6),[Decimal('43.3233')])
        venda=VendaPDVService.finalizar_venda(sessao=self.sessao,filial=self.filial,usuario=self.usuario,
            itens=[{'produto_id':produto.pk,'quantidade':3,'oferta_tipo':'combo','promocao_id':combo.pk,'faixa_id':faixa.pk}],
            pagamentos=[{'forma_id':self.forma.pk,'valor':'129.97'}])
        self.assertEqual(venda.itens.get().valor_unitario,Decimal('43.3233'))
        self.assertEqual(venda.valor_total,Decimal('129.97'))

    def test_combo_exato_permite_multiplos_completos_e_rejeita_fracoes(self):
        produto = self.criar_produto('Combo exato')
        produto.preco_venda = Decimal('49.99')
        produto.save(update_fields=['preco_venda'])
        self.abastecer(produto, '8')
        combo = PromocaoQuantidade.objects.create(
            filial=self.filial, produto=produto, nome='Trio', usar_preco_promocional=False,
        )
        faixa = PromocaoQuantidadeFaixa.objects.create(
            promocao=combo, quantidade_minima=3, condicao_quantidade=CondicaoQuantidade.IGUAL,
            tipo_desconto=TipoDesconto.VALOR, valor=Decimal('20'),
        )
        oferta = {'produto_id': produto.pk, 'oferta_tipo': 'combo',
                  'promocao_id': combo.pk, 'faixa_id': faixa.pk,
                  '_quantidadeMinOferta': 3, '_quantidadeExata': True, '_ofertaSelecionada': True}
        for quantidade in [2, 4, 5, Decimal('6.5')]:
            with self.subTest(quantidade=quantidade), self.assertRaises(DadosInvalidosError):
                VendaPDVService.finalizar_venda(
                    sessao=self.sessao, filial=self.filial, usuario=self.usuario,
                    itens=[{**oferta, 'quantidade': quantidade}],
                    pagamentos=[{'forma_id': self.forma.pk, 'valor': '500'}],
                )
        venda = VendaPDVService.finalizar_venda(
            sessao=self.sessao, filial=self.filial, usuario=self.usuario,
            itens=[{**oferta, 'quantidade': 6}],
            pagamentos=[{'forma_id': self.forma.pk, 'valor': '259.94'}],
        )
        self.assertEqual(venda.itens.get().quantidade, Decimal('6'))
        self.assertEqual(venda.valor_total, Decimal('259.94'))
        self.assertEqual(Estoque.objects.get(produto=produto, filial=self.filial).quantidade_atual, 2)
        combo.ativo = False
        combo.save(update_fields=['ativo'])
        with self.assertRaises(DadosInvalidosError):
            VendaPDVService.resolver_oferta_selecionada(
                produto=produto, filial=self.filial, quantidade=Decimal('6'), cliente=None,
                item_dados=oferta, preco_automatico={},
            )

    def test_kit_baixa_componentes_item_a_item(self):
        produto_a = self.criar_produto("Componente A")
        produto_b = self.criar_produto("Componente B")
        self.abastecer(produto_a, "10")
        self.abastecer(produto_b, "10")
        kit = KitProduto.objects.create(
            filial=self.filial,
            nome="Kit PDV",
            tipo_desconto=TipoDesconto.PERCENTUAL,
            valor_desconto=Decimal("10.00"),
        )
        KitProdutoItem.objects.create(kit=kit, produto=produto_a, quantidade=Decimal("2"))
        KitProdutoItem.objects.create(kit=kit, produto=produto_b, quantidade=Decimal("1"))

        venda = VendaPDVService.finalizar_venda(
            sessao=self.sessao,
            filial=self.filial,
            usuario=self.usuario,
            itens=[{"tipo_venda": "kit", "kit_id": kit.pk, "quantidade": "1"}],
            pagamentos=[{"forma_id": self.forma.pk, "valor": "27.00"}],
        )

        itens = list(ItemVendaPDV.objects.filter(venda_pdv=venda).order_by("numero_item"))
        estoque_a = Estoque.objects.get(produto=produto_a, filial=self.filial)
        estoque_b = Estoque.objects.get(produto=produto_b, filial=self.filial)
        self.assertEqual(len(itens), 2)
        self.assertTrue(all(item.tipo_venda == "kit" for item in itens))
        self.assertEqual(venda.valor_total, Decimal("27.00"))
        self.assertEqual(estoque_a.quantidade_atual, Decimal("8.000"))
        self.assertEqual(estoque_b.quantidade_atual, Decimal("9.000"))

    def test_kit_abaixo_do_custo_permite_venda(self):
        produto = self.criar_produto("Kit em liquidação")
        self.abastecer(produto, "3")
        kit = KitProduto.objects.create(
            filial=self.filial, nome="Liquidação",
            tipo_desconto=TipoDesconto.PERCENTUAL, valor_desconto=Decimal("80"),
        )
        KitProdutoItem.objects.create(kit=kit, produto=produto, quantidade=Decimal("2"))
        venda = VendaPDVService.finalizar_venda(
            sessao=self.sessao, filial=self.filial, usuario=self.usuario,
            itens=[{"tipo_venda": "kit", "kit_id": kit.pk, "quantidade": "1"}],
            pagamentos=[{"forma_id": self.forma.pk, "valor": "4.00"}],
        )
        self.assertEqual(venda.valor_total, Decimal("4.00"))
        self.assertEqual(venda.itens.get().custo_unitario_snapshot, Decimal("4.00"))

    def test_kit_com_preco_manual_zero_baixa_componentes_sem_pagamento(self):
        produto = self.criar_produto('Componente grátis')
        self.abastecer(produto, '4')
        kit = KitProduto.objects.create(filial=self.filial, nome='Kit grátis',
            tipo_desconto=TipoDesconto.PERCENTUAL, valor_desconto=Decimal('10'))
        KitProdutoItem.objects.create(kit=kit, produto=produto, quantidade=Decimal('2'))
        venda = VendaPDVService.finalizar_venda(
            sessao=self.sessao, filial=self.filial, usuario=self.usuario,
            itens=[{'tipo_venda': 'kit', 'kit_id': kit.pk, 'quantidade': 1, 'preco_manual': 0}],
            pagamentos=[],
        )
        self.assertEqual(venda.valor_total, 0)
        self.assertEqual(venda.itens.get().valor_unitario, 0)
        self.assertEqual(Estoque.objects.get(produto=produto, filial=self.filial).quantidade_atual, 2)

    def test_brinde_baixa_produto_gratis_com_movimento_de_brinde(self):
        gatilho = self.criar_produto("Produto gatilho")
        brinde_produto = self.criar_produto("Produto brinde")
        self.abastecer(gatilho, "10")
        self.abastecer(brinde_produto, "10")
        brinde = BrindeProduto.objects.create(
            filial=self.filial,
            nome="Brinde PDV",
            produto_gatilho=gatilho,
            quantidade_gatilho=Decimal("2"),
        )
        BrindeProdutoItem.objects.create(
            brinde=brinde,
            produto=brinde_produto,
            quantidade=Decimal("1"),
        )

        venda = VendaPDVService.finalizar_venda(
            sessao=self.sessao,
            filial=self.filial,
            usuario=self.usuario,
            itens=[{
                "produto_id": gatilho.pk,
                "quantidade": "2",
                "oferta_tipo": "brinde",
                "brinde_id": brinde.pk,
            }],
            pagamentos=[{"forma_id": self.forma.pk, "valor": "20.00"}],
        )

        itens = list(ItemVendaPDV.objects.filter(venda_pdv=venda).order_by("numero_item"))
        mov_brinde = MovimentacaoEstoque.objects.get(
            pk=itens[1].movimentacoes_estoque_ids[0],
        )
        estoque_brinde = Estoque.objects.get(produto=brinde_produto, filial=self.filial)
        self.assertEqual(len(itens), 2)
        self.assertEqual(itens[1].tipo_venda, "brinde")
        self.assertEqual(itens[0].oferta_contexto["brinde_id"], brinde.pk)
        self.assertEqual(itens[0].oferta_contexto["oferta_tipo"], "brinde")
        self.assertEqual(itens[1].valor_total, Decimal("0.00"))
        self.assertEqual(mov_brinde.tipo_operacao, MovimentacaoEstoque.TipoOperacao.BRINDE)
        self.assertEqual(estoque_brinde.quantidade_atual, Decimal("9.000"))

    def test_brinde_nao_entra_sem_selecao_da_campanha(self):
        gatilho = self.criar_produto("Gatilho sem selecao")
        presente = self.criar_produto("Presente nao selecionado")
        self.abastecer(gatilho, "5")
        self.abastecer(presente, "5")
        brinde = BrindeProduto.objects.create(
            filial=self.filial,
            nome="Escolha o brinde",
            produto_gatilho=gatilho,
            quantidade_gatilho=Decimal("1"),
        )
        BrindeProdutoItem.objects.create(brinde=brinde, produto=presente, quantidade=Decimal("1"))

        venda = VendaPDVService.finalizar_venda(
            sessao=self.sessao,
            filial=self.filial,
            usuario=self.usuario,
            itens=[{"produto_id": gatilho.pk, "quantidade": "1", "oferta_tipo": "normal"}],
            pagamentos=[{"forma_id": self.forma.pk, "valor": "10.00"}],
        )

        self.assertEqual(ItemVendaPDV.objects.filter(venda_pdv=venda).count(), 1)
        self.assertFalse(ItemVendaPDV.objects.filter(venda_pdv=venda, tipo_venda="brinde").exists())
