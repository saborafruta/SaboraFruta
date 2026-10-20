from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.cadastros.models import Cliente, ClienteFilial
from apps.core.models import Empresa, Filial, ParametrosSistema, PerfilAcesso, Usuario
from apps.estoque.models import Deposito, LoteProduto, MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.financeiro.constants.enums import TipoFormaPagamento
from apps.financeiro.models import CondicaoPagamento, FormaPagamento
from apps.pdv.models import Caixa, SessaoPDV, VendaPDV
from apps.produtos.models import (
    CategoriaProduto,
    CategoriaProdutoFilial,
    Produto,
    ProdutoFilial,
    UnidadeMedida,
    UnidadeMedidaFilial,
)
from apps.vendas.models import ItemSeparacao, PedidoVenda, SeparacaoPedido
from apps.vendas.services.venda_service import VendaService


class ForcaVendasMobileApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='ITED Mobile Teste LTDA',
            nome_fantasia='ITED Mobile',
            cnpj='71345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa,
            razao_social='ITED Mobile Matriz',
            nome_fantasia='Matriz Mobile',
            cnpj='71345678000192',
            uf='RN',
            cidade='Natal',
            is_matriz=True,
        )
        cls.perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa,
            nome='Administrador Mobile',
            is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='mobile@ited.test',
            nome='Usuário Mobile',
            password='senha-mobile-123',
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
        cls.categoria = CategoriaProduto.objects.create(
            empresa=cls.empresa,
            filial=cls.filial,
            nome='Destaques mobile',
        )
        CategoriaProdutoFilial.objects.create(
            categoria=cls.categoria,
            filial=cls.filial,
        )
        cls.produto = Produto.objects.create(
            filial=cls.filial,
            unidade_medida=cls.unidade,
            categoria=cls.categoria,
            descricao='Produto para pedido mobile',
            codigo='MOB-001',
            ncm='20089900',
            controla_lote=False,
            permite_venda_sem_estoque=False,
            preco_venda=Decimal('12.50'),
            preco_custo=Decimal('5.00'),
            catalogo_destaque=True,
        )
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.filial)
        cls.cliente = Cliente.objects.create(
            filial=cls.filial,
            tipo_pessoa='J',
            razao_social='Cliente Mobile LTDA',
            nome_fantasia='Cliente Mobile',
            cpf_cnpj='71345678000193',
            cidade='Natal',
            uf='RN',
            limite_credito=Decimal('1000.00'),
            ativo=True,
        )
        ClienteFilial.objects.create(cliente=cls.cliente, filial=cls.filial)
        cls.forma = FormaPagamento.objects.create(
            empresa=cls.empresa,
            filial=cls.filial,
            descricao='Boleto mobile',
            tipo=TipoFormaPagamento.BOLETO,
        )
        cls.condicao = CondicaoPagamento.objects.create(
            empresa=cls.empresa,
            descricao='À vista mobile',
            numero_parcelas=1,
        )
        cls.forma_checkout = FormaPagamento.objects.create(
            empresa=cls.empresa,
            filial=cls.filial,
            descricao='Dinheiro checkout mobile',
            tipo=TipoFormaPagamento.DINHEIRO,
            exibir_no_pdv=True,
        )
        cls.parametros = ParametrosSistema.objects.create(
            filial=cls.filial,
            checkout_venda_ativo=True,
        )
        cls.caixa = Caixa.objects.create(
            filial=cls.filial,
            numero=91,
            descricao='Caixa Android',
        )

    def setUp(self):
        cache.clear()
        token = self.client.post(
            reverse('forca_vendas_mobile_api:token'),
            data={'email': self.usuario.email, 'password': 'senha-mobile-123'},
            content_type='application/json',
        )
        self.assertEqual(token.status_code, 200, token.content)
        self.headers = {
            'HTTP_AUTHORIZATION': f'Bearer {token.json()["access"]}',
            'HTTP_X_FILIAL_ID': str(self.filial.pk),
            'HTTP_X_TENANT_ALIAS': 'default',
        }

    def _payload_pedido(self):
        return {
            'cliente_id': self.cliente.pk,
            'forma_pagamento_id': self.forma.pk,
            'condicao_pagamento_id': self.condicao.pk,
            'prazo_pagamento_dias': 10,
            'prioridade': 'urgente',
            'observacao': 'Criado no Android.',
            'itens': [{
                'produto_id': self.produto.pk,
                'quantidade': '2.000',
            }],
        }

    def test_insumo_nao_aparece_no_catalogo_mobile(self):
        insumo = Produto.objects.create(
            filial=self.filial,
            unidade_medida=self.unidade,
            categoria=self.categoria,
            descricao='Conservante interno mobile',
            codigo='INSUMO-MOBILE-001',
            ncm='38249989',
            preco_venda=Decimal('40.00'),
            finalidade=Produto.Finalidade.MATERIA_PRIMA,
            permite_venda=False,
        )
        ProdutoFilial.objects.create(produto=insumo, filial=self.filial)

        resposta = self.client.get(
            reverse('forca_vendas_mobile_api:produtos'),
            {'cliente_id': self.cliente.pk, 'q': insumo.codigo},
            **self.headers,
        )

        self.assertEqual(resposta.status_code, 200, resposta.content)
        self.assertEqual(resposta.json()['produtos'], [])

    def test_login_contexto_e_catalogos_do_pedido(self):
        self.produto.foto_url = 'https://cdn.example/produtos/mobile.jpg'
        self.produto.save(update_fields=['foto_url', 'updated_at'])
        contexto = self.client.get(
            reverse('forca_vendas_mobile_api:contexto'),
            HTTP_AUTHORIZATION=self.headers['HTTP_AUTHORIZATION'],
        )
        self.assertEqual(contexto.status_code, 200, contexto.content)
        self.assertEqual(contexto.json()['contextos'][0]['filial_id'], self.filial.pk)

        clientes = self.client.get(
            reverse('forca_vendas_mobile_api:clientes'),
            {'q': str(self.cliente.pk)},
            **self.headers,
        )
        self.assertEqual(clientes.status_code, 200, clientes.content)
        self.assertEqual(clientes.json()['clientes'][0]['nome'], 'Cliente Mobile')
        self.assertIn('tabela_preco_id', clientes.json()['clientes'][0])

        produtos = self.client.get(
            reverse('forca_vendas_mobile_api:produtos'),
            {'cliente_id': self.cliente.pk, 'q': 'MOB-001'},
            **self.headers,
        )
        self.assertEqual(produtos.status_code, 200, produtos.content)
        self.assertEqual(produtos.json()['produtos'][0]['preco'], '12.50')
        self.assertEqual(
            produtos.json()['produtos'][0]['categoria'],
            self.categoria.nome,
        )
        self.assertEqual(produtos.json()['produtos'][0]['categoria_id'], self.categoria.pk)
        self.assertTrue(produtos.json()['produtos'][0]['destaque'])
        self.assertEqual(
            produtos.json()['produtos'][0]['foto_url'],
            'https://cdn.example/produtos/mobile.jpg',
        )

        destaques = self.client.get(
            reverse('forca_vendas_mobile_api:produtos'),
            {'cliente_id': self.cliente.pk, 'destaques': '1'},
            **self.headers,
        )
        self.assertEqual(destaques.status_code, 200, destaques.content)
        self.assertEqual(destaques.json()['produtos'][0]['id'], self.produto.pk)

        por_categoria = self.client.get(
            reverse('forca_vendas_mobile_api:produtos'),
            {'cliente_id': self.cliente.pk, 'categoria_id': self.categoria.pk},
            **self.headers,
        )
        self.assertEqual(por_categoria.status_code, 200, por_categoria.content)
        self.assertEqual(por_categoria.json()['produtos'][0]['id'], self.produto.pk)

        opcoes = self.client.get(
            reverse('forca_vendas_mobile_api:opcoes_pedido'),
            **self.headers,
        )
        self.assertEqual(opcoes.status_code, 200, opcoes.content)
        self.assertIn(
            self.forma.pk,
            [forma['id'] for forma in opcoes.json()['formas_pagamento']],
        )
        self.assertIn(
            self.categoria.pk,
            [categoria['id'] for categoria in opcoes.json()['categorias']],
        )

    def test_sincronizacao_mobile_nao_limita_clientes_a_cinquenta(self):
        novos = Cliente.objects.bulk_create([
            Cliente(
                filial=self.filial,
                tipo_pessoa='F',
                razao_social=f'Cliente offline {indice:02d}',
                ativo=True,
            )
            for indice in range(55)
        ])
        ClienteFilial.objects.bulk_create([
            ClienteFilial(cliente=cliente, filial=self.filial)
            for cliente in novos
        ])

        resposta_normal = self.client.get(
            reverse('forca_vendas_mobile_api:clientes'),
            **self.headers,
        )
        resposta_sync = self.client.get(
            reverse('forca_vendas_mobile_api:clientes'),
            {'sync': '1'},
            **self.headers,
        )

        self.assertEqual(len(resposta_normal.json()['clientes']), 50)
        self.assertEqual(len(resposta_sync.json()['clientes']), 56)

    @patch('apps.vendas.api_mobile.views.CepService.consultar')
    def test_cadastra_cliente_completo_e_consulta_cep(self, consultar_cep):
        consultar_cep.return_value = {
            'cep': '59064100',
            'endereco': 'Rua dos Testes',
            'complemento': '',
            'bairro': 'Lagoa Nova',
            'cidade': 'Natal',
            'uf': 'RN',
            'codigo_municipio_ibge': '2408102',
        }
        cep = self.client.get(
            reverse('forca_vendas_mobile_api:cliente_cep'),
            {'cep': '59064-100'},
            **self.headers,
        )
        self.assertEqual(cep.status_code, 200, cep.content)
        self.assertEqual(cep.json()['endereco']['bairro'], 'Lagoa Nova')

        resposta = self.client.post(
            reverse('forca_vendas_mobile_api:clientes'),
            data={
                'tipo_pessoa': 'F',
                'tipo': 'varejo',
                'razao_social': 'Cliente Completo Android',
                'cpf_cnpj': '123.456.789-09',
                'celular': '(84) 99999-1111',
                'email': 'cliente@android.test',
                'cep': '59064-100',
                'endereco': 'Rua dos Testes',
                'numero': '120',
                'complemento': 'Casa',
                'ponto_referencia': 'Próximo à praça',
                'bairro': 'Lagoa Nova',
                'cidade': 'Natal',
                'uf': 'rn',
                'codigo_municipio_ibge': '2408102',
                'limite_credito': '150.00',
                'prazo_pagamento_dias': 15,
                'observacao': 'Cadastrado no aplicativo.',
            },
            content_type='application/json',
            **self.headers,
        )
        self.assertEqual(resposta.status_code, 201, resposta.content)
        cliente = Cliente.objects.get(pk=resposta.json()['cliente']['id'])
        self.assertEqual(cliente.ponto_referencia, 'Próximo à praça')
        self.assertEqual(cliente.codigo_municipio_ibge, '2408102')
        self.assertEqual(cliente.uf, 'RN')
        self.assertEqual(cliente.prazo_pagamento_dias, 15)

    @patch('apps.vendas.api_mobile.views.CnpjService.consultar')
    def test_consulta_cnpj_para_preencher_cliente_pessoa_juridica(self, consultar_cnpj):
        consultar_cnpj.return_value = {
            'cnpj': '71345678000194',
            'razao_social': 'Empresa Consultada LTDA',
            'nome_fantasia': 'Empresa Consultada',
            'inscricao_estadual': '20202020',
            'endereco': 'Rua da Receita',
            'numero': '99',
            'complemento': 'Sala 2',
            'bairro': 'Centro',
            'cidade': 'Natal',
            'uf': 'RN',
            'cep': '59000000',
            'codigo_municipio_ibge': '2408102',
            'telefone': '8433334444',
            'email': 'empresa@consultada.test',
            'situacao_cadastral': 'ATIVA',
        }
        resposta = self.client.get(
            reverse('forca_vendas_mobile_api:cliente_cnpj'),
            {'cnpj': '71.345.678/0001-94'},
            **self.headers,
        )
        self.assertEqual(resposta.status_code, 200, resposta.content)
        self.assertEqual(resposta.json()['empresa']['razao_social'], 'Empresa Consultada LTDA')
        self.assertEqual(resposta.json()['empresa']['situacao_cadastral'], 'ATIVA')

    def test_catalogo_mobile_exibe_saldo_de_todos_os_depositos(self):
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
                valor_unitario=Decimal('5'),
                deposito_id=deposito_id,
            )

        resposta = self.client.get(
            reverse('forca_vendas_mobile_api:produtos'),
            {'cliente_id': self.cliente.pk, 'q': self.produto.codigo},
            **self.headers,
        )
        self.assertEqual(resposta.status_code, 200, resposta.content)
        saldos = {
            saldo['deposito_nome']: saldo
            for saldo in resposta.json()['produtos'][0]['estoques']
        }

        self.assertEqual(saldos['Frente de loja']['disponivel'], '7.000')
        self.assertEqual(saldos['Galpão']['disponivel'], '13.000')
        self.assertTrue(saldos['Frente de loja']['origem_pedido'])
        self.assertTrue(saldos['Galpão']['permite_venda'])
        self.assertFalse(saldos['Galpão']['origem_pedido'])

    def test_checkout_mobile_consulta_estado_clientes_e_produtos(self):
        estado_fechado = self.client.get(
            reverse('forca_vendas_mobile_api:checkout_estado'),
            **self.headers,
        )
        self.assertEqual(estado_fechado.status_code, 200, estado_fechado.content)
        self.assertIsNone(estado_fechado.json()['sessao'])
        self.assertIn(
            self.forma_checkout.pk,
            [forma['id'] for forma in estado_fechado.json()['formas_pagamento']],
        )

        clientes = self.client.get(
            reverse('forca_vendas_mobile_api:checkout_clientes'),
            {'q': 'Cliente Mobile'},
            **self.headers,
        )
        self.assertEqual(clientes.status_code, 200, clientes.content)
        self.assertEqual(clientes.json()['clientes'][0]['id'], self.cliente.pk)

        criado = self.client.post(
            reverse('forca_vendas_mobile_api:checkout_clientes'),
            data={
                'nome': 'Cliente rápido Android',
                'cpf_cnpj': '123.456.789-09',
                'celular': '84999990000',
            },
            content_type='application/json',
            **self.headers,
        )
        self.assertEqual(criado.status_code, 201, criado.content)
        self.assertEqual(criado.json()['cliente']['nome'], 'Cliente rápido Android')
        self.assertEqual(criado.json()['cliente']['cpf_cnpj'], '12345678909')

        produtos = self.client.get(
            reverse('forca_vendas_mobile_api:checkout_produtos'),
            {'q': 'MOB-001'},
            **self.headers,
        )
        self.assertEqual(produtos.status_code, 200, produtos.content)
        self.assertEqual(produtos.json()['produtos'][0]['preco'], '12.50')

    def test_checkout_mobile_finaliza_uma_unica_venda_e_baixa_estoque(self):
        SessaoPDV.objects.create(
            filial=self.filial,
            caixa=self.caixa,
            usuario=self.usuario,
            valor_abertura=Decimal('100.00'),
            status='aberto',
        )
        MovimentacaoService.registrar_movimentacao(
            produto_id=self.produto.pk,
            filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal('10'),
            usuario_id=self.usuario.pk,
            valor_unitario=Decimal('5'),
            deposito_id=Deposito.venda_id(self.filial.pk),
        )
        payload = {
            'cliente_id': self.cliente.pk,
            'desconto': '0.00',
            'itens': [{'produto_id': self.produto.pk, 'quantidade': '2.000'}],
            'pagamentos': [{'forma_id': self.forma_checkout.pk, 'valor': '30.00'}],
        }
        url = reverse('forca_vendas_mobile_api:checkout_vendas')
        primeira = self.client.post(
            url,
            data=payload,
            content_type='application/json',
            HTTP_IDEMPOTENCY_KEY='checkout-mobile-0001',
            **self.headers,
        )
        self.assertEqual(primeira.status_code, 201, primeira.content)
        self.assertEqual(primeira.json()['valor_total'], '25.00')
        self.assertEqual(primeira.json()['troco'], '5.00')

        repetida = self.client.post(
            url,
            data=payload,
            content_type='application/json',
            HTTP_IDEMPOTENCY_KEY='checkout-mobile-0001',
            **self.headers,
        )
        self.assertEqual(repetida.status_code, 200, repetida.content)
        self.assertTrue(repetida.json()['repetida'])
        self.assertEqual(VendaPDV.objects.filter(idempotency_key='checkout-mobile-0001').count(), 1)

    def test_checkout_mobile_respeita_parametro_de_estoque_negativo(self):
        SessaoPDV.objects.create(
            filial=self.filial,
            caixa=self.caixa,
            usuario=self.usuario,
            valor_abertura=Decimal('100.00'),
            status='aberto',
        )
        payload = {
            'cliente_id': self.cliente.pk,
            'desconto': '0.00',
            'itens': [{'produto_id': self.produto.pk, 'quantidade': '2.000'}],
            'pagamentos': [{'forma_id': self.forma_checkout.pk, 'valor': '25.00'}],
        }
        url = reverse('forca_vendas_mobile_api:checkout_vendas')

        self.parametros.checkout_permitir_estoque_negativo = False
        self.parametros.save(update_fields=['checkout_permitir_estoque_negativo'])
        bloqueada = self.client.post(
            url,
            data=payload,
            content_type='application/json',
            HTTP_IDEMPOTENCY_KEY='checkout-mobile-negativo-bloqueado',
            **self.headers,
        )
        self.assertEqual(bloqueada.status_code, 400, bloqueada.content)
        self.assertIn('estoque', bloqueada.json())

        self.parametros.checkout_permitir_estoque_negativo = True
        self.parametros.save(update_fields=['checkout_permitir_estoque_negativo'])
        liberada = self.client.post(
            url,
            data=payload,
            content_type='application/json',
            HTTP_IDEMPOTENCY_KEY='checkout-mobile-negativo-liberado',
            **self.headers,
        )

        self.assertEqual(liberada.status_code, 201, liberada.content)
        estoque = self.produto.estoques.get(
            filial=self.filial,
            deposito_id=Deposito.venda_id(self.filial.pk),
        )
        self.assertEqual(estoque.quantidade_atual, Decimal('-2.000'))

    def test_checkout_mobile_salva_pendencia_sem_caixa_e_sem_pagamento(self):
        payload = {
            'cliente_id': self.cliente.pk,
            'desconto': '0.00',
            'itens': [{'produto_id': self.produto.pk, 'quantidade': '2.000'}],
            'pagamentos': [],
        }
        criada = self.client.post(
            reverse('forca_vendas_mobile_api:checkout_pendencias'),
            data=payload,
            content_type='application/json',
            HTTP_IDEMPOTENCY_KEY='checkout-pendente-mobile-0001',
            **self.headers,
        )

        self.assertEqual(criada.status_code, 201, criada.content)
        pendencia = VendaPDV.objects.get(pk=criada.json()['id'])
        self.assertEqual(pendencia.status, 'aberta')
        self.assertEqual(pendencia.origem, 'checkout_mobile')
        self.assertIsNone(pendencia.sessao_pdv_id)
        self.assertEqual(pendencia.valor_total, Decimal('25.00'))
        self.assertFalse(pendencia.itens.get().estoque_baixado)

        lista = self.client.get(
            reverse('forca_vendas_mobile_api:checkout_pendencias'),
            **self.headers,
        )
        self.assertEqual(lista.status_code, 200, lista.content)
        self.assertEqual(lista.json()['pendencias'][0]['id'], pendencia.pk)

        detalhe = self.client.get(
            reverse('forca_vendas_mobile_api:checkout_pendente_detalhe', args=[pendencia.pk]),
            **self.headers,
        )
        self.assertEqual(detalhe.status_code, 200, detalhe.content)
        self.assertEqual(detalhe.json()['cliente']['id'], self.cliente.pk)
        self.assertEqual(detalhe.json()['itens'][0]['produto']['id'], self.produto.pk)
        self.assertEqual(detalhe.json()['itens'][0]['quantidade'], '2.000')

    def test_checkout_mobile_aplica_politica_de_desconto_e_salva_na_pendencia(self):
        self.parametros.checkout_desconto_percentual_1 = Decimal('2.00')
        self.parametros.checkout_desconto_percentual_2 = Decimal('3.00')
        self.parametros.checkout_desconto_percentual_3 = Decimal('5.00')
        self.parametros.save(update_fields=[
            'checkout_desconto_percentual_1',
            'checkout_desconto_percentual_2',
            'checkout_desconto_percentual_3',
        ])

        estado = self.client.get(
            reverse('forca_vendas_mobile_api:checkout_estado'),
            **self.headers,
        )
        self.assertEqual(estado.status_code, 200, estado.content)
        self.assertEqual(estado.json()['descontos']['percentuais'], ['2.0', '3.0', '5.0'])
        self.assertEqual(estado.json()['descontos']['percentual_maximo'], '5.0')

        payload = {
            'cliente_id': self.cliente.pk,
            'desconto': '1.25',
            'itens': [{'produto_id': self.produto.pk, 'quantidade': '2.000'}],
            'pagamentos': [],
        }
        dentro_da_politica = self.client.post(
            reverse('forca_vendas_mobile_api:checkout_pendencias'),
            data=payload,
            content_type='application/json',
            HTTP_IDEMPOTENCY_KEY='checkout-desconto-livre-mobile',
            **self.headers,
        )
        self.assertEqual(dentro_da_politica.status_code, 201, dentro_da_politica.content)
        pendencia_livre = VendaPDV.objects.get(pk=dentro_da_politica.json()['id'])
        self.assertEqual(pendencia_livre.valor_desconto, Decimal('1.25'))
        self.assertEqual(pendencia_livre.valor_total, Decimal('23.75'))

        payload['desconto'] = '1.26'
        acima_sem_autorizacao = self.client.post(
            reverse('forca_vendas_mobile_api:checkout_pendencias'),
            data=payload,
            content_type='application/json',
            HTTP_IDEMPOTENCY_KEY='checkout-desconto-bloqueado-mobile',
            **self.headers,
        )
        self.assertEqual(acima_sem_autorizacao.status_code, 403, acima_sem_autorizacao.content)
        self.assertFalse(
            VendaPDV.objects.filter(idempotency_key='checkout-desconto-bloqueado-mobile').exists()
        )

        autorizacao = self.client.post(
            reverse('forca_vendas_mobile_api:checkout_desconto_autorizar'),
            data={'email': self.usuario.email, 'senha': 'senha-mobile-123'},
            content_type='application/json',
            **self.headers,
        )
        self.assertEqual(autorizacao.status_code, 200, autorizacao.content)
        payload['autorizacao_desconto'] = autorizacao.json()['token']
        acima_autorizado = self.client.post(
            reverse('forca_vendas_mobile_api:checkout_pendencias'),
            data=payload,
            content_type='application/json',
            HTTP_IDEMPOTENCY_KEY='checkout-desconto-autorizado-mobile',
            **self.headers,
        )
        self.assertEqual(acima_autorizado.status_code, 201, acima_autorizado.content)
        pendencia_autorizada = VendaPDV.objects.get(pk=acima_autorizado.json()['id'])
        self.assertEqual(pendencia_autorizada.valor_desconto, Decimal('1.26'))
        self.assertEqual(pendencia_autorizada.valor_total, Decimal('23.74'))

    def test_caixa_finaliza_pendencia_mobile_e_remove_apenas_apos_sucesso(self):
        criada = self.client.post(
            reverse('forca_vendas_mobile_api:checkout_pendencias'),
            data={
                'cliente_id': self.cliente.pk,
                'itens': [{'produto_id': self.produto.pk, 'quantidade': '2.000'}],
                'pagamentos': [],
            },
            content_type='application/json',
            HTTP_IDEMPOTENCY_KEY='checkout-pendente-mobile-0002',
            **self.headers,
        )
        self.assertEqual(criada.status_code, 201, criada.content)
        pendencia_id = criada.json()['id']

        sem_caixa = self.client.post(
            reverse('forca_vendas_mobile_api:checkout_vendas'),
            data={
                'pendencia_id': pendencia_id,
                'cliente_id': self.cliente.pk,
                'itens': [{'produto_id': self.produto.pk, 'quantidade': '2.000'}],
                'pagamentos': [{'forma_id': self.forma_checkout.pk, 'valor': '25.00'}],
            },
            content_type='application/json',
            HTTP_IDEMPOTENCY_KEY='checkout-baixa-mobile-0002',
            **self.headers,
        )
        self.assertEqual(sem_caixa.status_code, 400, sem_caixa.content)
        self.assertTrue(VendaPDV.objects.filter(pk=pendencia_id, status='aberta').exists())

        SessaoPDV.objects.create(
            filial=self.filial,
            caixa=self.caixa,
            usuario=self.usuario,
            valor_abertura=Decimal('100.00'),
            status='aberto',
        )
        MovimentacaoService.registrar_movimentacao(
            produto_id=self.produto.pk,
            filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal('10'),
            usuario_id=self.usuario.pk,
            valor_unitario=Decimal('5'),
            deposito_id=Deposito.venda_id(self.filial.pk),
        )
        finalizada = self.client.post(
            reverse('forca_vendas_mobile_api:checkout_vendas'),
            data={
                'pendencia_id': pendencia_id,
                'cliente_id': self.cliente.pk,
                'itens': [{'produto_id': self.produto.pk, 'quantidade': '2.000'}],
                'pagamentos': [{'forma_id': self.forma_checkout.pk, 'valor': '25.00'}],
            },
            content_type='application/json',
            HTTP_IDEMPOTENCY_KEY='checkout-baixa-mobile-0002',
            **self.headers,
        )

        self.assertEqual(finalizada.status_code, 201, finalizada.content)
        self.assertFalse(VendaPDV.objects.filter(pk=pendencia_id).exists())
        venda = VendaPDV.objects.get(pk=finalizada.json()['venda_id'])
        self.assertEqual(venda.status, 'finalizada')
        self.assertEqual(venda.valor_total, Decimal('25.00'))

    def test_pedido_mobile_e_idempotente_e_recebe_numero_oficial(self):
        chave = 'pedido-mobile-0001'
        url = reverse('forca_vendas_mobile_api:pedidos')
        primeira = self.client.post(
            url,
            data=self._payload_pedido(),
            content_type='application/json',
            HTTP_IDEMPOTENCY_KEY=chave,
            **self.headers,
        )
        self.assertEqual(primeira.status_code, 201, primeira.content)
        self.assertFalse(primeira.json()['repetido'])
        self.assertTrue(primeira.json()['pedido']['numero_pedido'].startswith('PV-'))

        repetida = self.client.post(
            url,
            data=self._payload_pedido(),
            content_type='application/json',
            HTTP_IDEMPOTENCY_KEY=chave,
            **self.headers,
        )
        self.assertEqual(repetida.status_code, 200, repetida.content)
        self.assertTrue(repetida.json()['repetido'])
        self.assertEqual(PedidoVenda.objects.count(), 1)

    def test_fila_e_conclusao_da_separacao_mobile_sao_idempotentes(self):
        MovimentacaoService.registrar_movimentacao(
            produto_id=self.produto.pk,
            filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal('10'),
            usuario_id=self.usuario.pk,
            valor_unitario=Decimal('5'),
        )
        criado = self.client.post(
            reverse('forca_vendas_mobile_api:pedidos'),
            data=self._payload_pedido(),
            content_type='application/json',
            HTTP_IDEMPOTENCY_KEY='pedido-mobile-separar-1',
            **self.headers,
        )
        pedido = PedidoVenda.objects.get(pk=criado.json()['pedido']['id'])
        VendaService.aprovar_e_confirmar_pedido(pedido, self.usuario)

        fila = self.client.get(
            reverse('forca_vendas_mobile_api:separacoes'),
            **self.headers,
        )
        self.assertEqual(fila.status_code, 200, fila.content)
        item = fila.json()['pedidos'][0]['itens'][0]
        deposito_id = Deposito.padrao_id(self.filial.pk)
        payload = {
            'observacao': 'Conferido pelo Android.',
            'itens': [{
                'item_id': item['id'],
                'deposito_id': deposito_id,
                'quantidade_separada': '2.000',
                'conferido': True,
            }],
        }
        url = reverse('forca_vendas_mobile_api:concluir_separacao', args=[pedido.pk])
        primeira = self.client.post(
            url,
            data=payload,
            content_type='application/json',
            HTTP_IDEMPOTENCY_KEY='separacao-mobile-0001',
            **self.headers,
        )
        self.assertEqual(primeira.status_code, 201, primeira.content)
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, PedidoVenda.Status.EM_SEPARACAO)

        repetida = self.client.post(
            url,
            data=payload,
            content_type='application/json',
            HTTP_IDEMPOTENCY_KEY='separacao-mobile-0001',
            **self.headers,
        )
        self.assertEqual(repetida.status_code, 200, repetida.content)
        self.assertTrue(repetida.json()['repetido'])
        self.assertEqual(SeparacaoPedido.objects.count(), 1)

    def test_separacao_mobile_permite_dividir_quantidade_entre_lotes(self):
        self.produto.controla_lote = True
        self.produto.save(update_fields=['controla_lote', 'updated_at'])
        lote = LoteProduto.objects.create(
            produto=self.produto,
            filial=self.filial,
            numero_lote='LOTE-MOBILE-02',
            quantidade_inicial=Decimal('0'),
            quantidade_atual=Decimal('0'),
            custo_unitario=Decimal('5'),
        )
        outro_lote = LoteProduto.objects.create(
            produto=self.produto,
            filial=self.filial,
            numero_lote='LOTE-MOBILE-03',
            quantidade_inicial=Decimal('0'),
            quantidade_atual=Decimal('0'),
            custo_unitario=Decimal('5'),
        )
        for lote_entrada in [lote, outro_lote]:
            MovimentacaoService.registrar_movimentacao(
                produto_id=self.produto.pk,
                filial_id=self.filial.pk,
                tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
                quantidade=Decimal('10'),
                usuario_id=self.usuario.pk,
                lote_id=lote_entrada.pk,
                valor_unitario=Decimal('5'),
            )
        criado = self.client.post(
            reverse('forca_vendas_mobile_api:pedidos'),
            data=self._payload_pedido(),
            content_type='application/json',
            HTTP_IDEMPOTENCY_KEY='pedido-mobile-lote-001',
            **self.headers,
        )
        self.assertEqual(criado.status_code, 201, criado.content)
        pedido = PedidoVenda.objects.get(pk=criado.json()['pedido']['id'])
        VendaService.aprovar_e_confirmar_pedido(pedido, self.usuario)

        fila = self.client.get(
            reverse('forca_vendas_mobile_api:separacoes'),
            **self.headers,
        )
        item = fila.json()['pedidos'][0]['itens'][0]
        self.assertEqual(item['lote_modo'], 'manual_ou_fefo')
        resposta = self.client.post(
            reverse('forca_vendas_mobile_api:concluir_separacao', args=[pedido.pk]),
            data={
                'itens': [{
                    'item_id': item['id'],
                    'deposito_id': Deposito.padrao_id(self.filial.pk),
                    'quantidade_separada': '2.000',
                    'conferido': True,
                    'lotes': [
                        {'lote_id': lote.pk, 'quantidade': '1.000'},
                        {'lote_id': outro_lote.pk, 'quantidade': '1.000'},
                    ],
                }],
            },
            content_type='application/json',
            HTTP_IDEMPOTENCY_KEY='separacao-mobile-lote-001',
            **self.headers,
        )
        self.assertEqual(resposta.status_code, 201, resposta.content)
        itens_separados = ItemSeparacao.objects.filter(
            separacao_id=resposta.json()['separacao_id'],
        ).order_by('lote_id')
        self.assertEqual(itens_separados.count(), 2)
        self.assertEqual(
            {item.lote_id: item.quantidade_separada for item in itens_separados},
            {lote.pk: Decimal('1.000'), outro_lote.pk: Decimal('1.000')},
        )
