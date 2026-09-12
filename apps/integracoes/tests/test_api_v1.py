from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial
from apps.core.tenant_context import get_current_tenant_db
from apps.integracoes.models import CredencialIntegracao
from apps.moda.models import (
    Cor, ItemGradePedido, ItemPedidoProducao, OrdemProducao,
    PedidoProducao, PersonalizacaoIndividual, ProdutoCor, ProdutoModa,
    Tamanho, Variante,
)
from apps.produtos.models import Produto, UnidadeMedida


class IntegracaoApiV1Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Fábrica Integrada LTDA',
            nome_fantasia='Fábrica Integrada',
            cnpj='11222333000181',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
            logo_url='https://cdn.example.com/logo.png',
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa,
            razao_social='Matriz Integrada',
            nome_fantasia='Matriz',
            cnpj='11222333000182',
            uf='RN',
            is_matriz=True,
        )
        cls.outra_filial = Filial.objects.create(
            empresa=cls.empresa,
            razao_social='Filial Integrada',
            nome_fantasia='Filial',
            cnpj='11222333000183',
            uf='RN',
        )
        cls.outra_empresa = Empresa.objects.create(
            razao_social='Outra Empresa LTDA',
            nome_fantasia='Outra',
            cnpj='99888777000166',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial_de_fora = Filial.objects.create(
            empresa=cls.outra_empresa,
            razao_social='Outra Matriz',
            cnpj='99888777000167',
            uf='RN',
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='UN', descricao='Unidade', tipo='unidade',
        )
        cls.unidade_fora = UnidadeMedida.objects.create(
            empresa=cls.outra_empresa, sigla='UN', descricao='Unidade', tipo='unidade',
        )
        cls.produto = Produto.objects.create(
            filial=cls.filial,
            unidade_medida=cls.unidade,
            codigo='CAM-001',
            codigo_barras='7891234567890',
            descricao='Camisa de teste',
            ncm='61091000',
            preco_venda='89.9000',
        )
        cls.produto_fora = Produto.objects.create(
            filial=cls.filial_de_fora,
            unidade_medida=cls.unidade_fora,
            codigo='FORA-001',
            descricao='Produto de outra empresa',
            ncm='61091000',
        )
        cls.produto_moda = ProdutoModa.all_objects.create(
            filial=cls.filial,
            codigo='MOD-001',
            referencia='REF-01',
            nome='Camisa esportiva',
            status=ProdutoModa.Status.ATIVO,
        )
        cls.cor = Cor.all_objects.create(
            filial=cls.filial, nome='Azul Royal', sigla='AZR', hex_cor='#0057B8',
        )
        cls.tamanho = Tamanho.all_objects.create(
            filial=cls.filial, nome='Médio', sigla='M', ordem=30,
        )
        cls.produto_cor = ProdutoCor.objects.create(
            produto=cls.produto_moda, cor=cls.cor,
        )
        cls.variante = Variante.objects.create(
            produto=cls.produto_moda,
            produto_cor=cls.produto_cor,
            tamanho=cls.tamanho,
            sku='MOD-001-AZR-M',
            codigo_barras='7890000000001',
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial,
            tipo_pessoa='J',
            razao_social='Cliente das Etiquetas LTDA',
            nome_fantasia='Cliente Etiquetas',
        )
        cls.pedido = PedidoProducao.all_objects.create(
            filial=cls.filial,
            numero=16,
            cliente=cls.cliente,
            status=PedidoProducao.Status.LIBERADO_PRODUCAO,
        )
        cls.item = ItemPedidoProducao.all_objects.create(
            pedido=cls.pedido,
            produto=cls.produto_moda,
            descricao='Camisa esportiva',
            referencia='REF-01',
            cor=cls.cor,
            quantidade=1,
        )
        ItemGradePedido.objects.create(
            item=cls.item, tamanho=cls.tamanho, quantidade=1,
        )
        PersonalizacaoIndividual.objects.create(
            pedido=cls.pedido,
            item=cls.item,
            tamanho=cls.tamanho,
            nome='SILVA',
            numero='10',
        )
        cls.ordem = OrdemProducao.all_objects.create(
            filial=cls.filial,
            ano=2026,
            sequencial=16,
            pedido=cls.pedido,
            item=cls.item,
            quantidade=1,
            status=OrdemProducao.Status.LIBERADA,
        )
        cls.credencial, cls.token = CredencialIntegracao.criar(
            empresa=cls.empresa,
            nome='Sistema externo',
        )

    def setUp(self):
        self.client = APIClient()

    def autenticar(self, token=None):
        self.client.credentials(HTTP_X_API_KEY=token or self.token)

    def test_chave_e_armazenada_apenas_como_hash(self):
        self.assertNotEqual(self.credencial.token_hash, self.token)
        self.assertNotIn(self.token, str(self.credencial.__dict__))

    def test_raiz_exige_chave_e_informa_contexto(self):
        self.assertEqual(self.client.get(reverse('integracoes_api:raiz')).status_code, 401)
        self.autenticar()

        resposta = self.client.get(reverse('integracoes_api:raiz'))

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.data['empresa']['cnpj'], self.empresa.cnpj)
        self.assertEqual(resposta.data['versao'], 'v1')
        self.assertIsNone(get_current_tenant_db())

    def test_chave_revogada_ou_expirada_e_recusada(self):
        for alteracao in (
            {'ativo': False},
            {'ativo': True, 'expira_em': timezone.now() - timedelta(seconds=1)},
        ):
            with self.subTest(alteracao=alteracao):
                CredencialIntegracao.objects.filter(pk=self.credencial.pk).update(**alteracao)
                self.autenticar()
                resposta = self.client.get(reverse('integracoes_api:raiz'))
                self.assertEqual(resposta.status_code, 401)

    def test_chave_pode_ser_enviada_no_authorization(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'ApiKey {self.token}')

        resposta = self.client.get(reverse('integracoes_api:contexto'))

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.data['empresa']['cnpj'], self.empresa.cnpj)

    def test_restricao_de_ip_e_aplicada(self):
        self.credencial.ips_permitidos = ['10.0.0.0/8']
        self.credencial.save(update_fields=['ips_permitidos'])
        self.autenticar()

        resposta = self.client.get(
            reverse('integracoes_api:raiz'), REMOTE_ADDR='192.168.1.10',
        )

        self.assertEqual(resposta.status_code, 401)

    def test_produtos_nunca_vazam_de_outra_empresa(self):
        self.autenticar()

        resposta = self.client.get(reverse('integracoes_api:produtos'))

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual([item['id'] for item in resposta.data['dados']], [self.produto.pk])
        self.assertEqual(resposta.data['dados'][0]['preco_venda'], '89.90')
        self.assertEqual(resposta.data['dados'][0]['preco_atual'], '89.90')
        self.assertEqual(resposta.data['dados'][0]['logo_url'], 'https://cdn.example.com/logo.png')

    def test_produto_entrega_promocao_vigente_para_etiqueta(self):
        self.produto.preco_promocional = '69.9000'
        self.produto.promocao_inicio = timezone.localdate() - timedelta(days=1)
        self.produto.promocao_fim = timezone.localdate() + timedelta(days=1)
        self.produto.save(update_fields=[
            'preco_promocional', 'promocao_inicio', 'promocao_fim', 'updated_at',
        ])
        self.autenticar()

        resposta = self.client.get(reverse('integracoes_api:produtos'))

        self.assertEqual(resposta.status_code, 200)
        produto = resposta.data['dados'][0]
        self.assertEqual(produto['preco_venda'], '89.90')
        self.assertEqual(produto['preco_atual'], '69.90')
        self.assertEqual(produto['preco_atual_tipo'], 'promocional')

    def test_contexto_entrega_logo_da_empresa_e_da_filial(self):
        self.autenticar()

        resposta = self.client.get(reverse('integracoes_api:contexto'))

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.data['empresa']['logo_url'], 'https://cdn.example.com/logo.png')
        self.assertEqual(resposta.data['filiais'][0]['logo_url'], 'https://cdn.example.com/logo.png')

    def test_cliente_de_outra_empresa_nao_e_exposto(self):
        Cliente.objects.create(
            filial=self.filial_de_fora,
            tipo_pessoa='J',
            razao_social='Cliente secreto de outra empresa',
        )
        self.autenticar()

        resposta = self.client.get(reverse('integracoes_api:clientes'))

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual([item['id'] for item in resposta.data['dados']], [self.cliente.pk])

    def test_restricao_de_filial_e_aplicada_no_servidor(self):
        self.credencial.filiais.set([self.outra_filial])
        self.autenticar()

        resposta = self.client.get(reverse('integracoes_api:produtos'))

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.data['dados'], [])

    def test_filtro_de_filial_tambem_e_aplicado_nas_variantes(self):
        self.autenticar()

        resposta = self.client.get(
            reverse('integracoes_api:variantes_moda'),
            {'filial_cnpj': self.outra_filial.cnpj},
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.data['dados'], [])

    def test_escopo_insuficiente_retorna_403(self):
        self.credencial.escopos = ['filiais:ler']
        self.credencial.save(update_fields=['escopos'])
        self.autenticar()

        resposta = self.client.get(reverse('integracoes_api:produtos'))

        self.assertEqual(resposta.status_code, 403)

    def test_variante_entrega_campos_de_etiqueta(self):
        self.autenticar()

        resposta = self.client.get(
            reverse('integracoes_api:variantes_moda'), {'busca': 'MOD-001-AZR-M'},
        )

        self.assertEqual(resposta.status_code, 200)
        variante = resposta.data['dados'][0]
        self.assertEqual(variante['sku'], 'MOD-001-AZR-M')
        self.assertEqual(variante['cor']['nome'], 'Azul Royal')
        self.assertEqual(variante['tamanho']['sigla'], 'M')

    def test_detalhe_da_op_entrega_grade_e_personalizacao(self):
        self.autenticar()

        resposta = self.client.get(reverse(
            'integracoes_api:ordem_producao_detalhe', args=[self.ordem.pk],
        ))

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.data['numero'], 'OP-2026-000016')
        self.assertEqual(resposta.data['grade'][0]['quantidade'], 1)
        pessoa = resposta.data['personalizacoes_individuais'][0]
        self.assertEqual(pessoa['identificacao'], 'SILVA #10')
