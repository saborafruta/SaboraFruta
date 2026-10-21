import json
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import (
    Deposito, EnderecoEstoque, Estoque, MovimentacaoEstoque, SaldoEndereco,
)
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.estoque.services.saldos_por_deposito import anexar_saldos_por_deposito
from apps.produtos.models import (
    Produto, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial,
)


class EnderecamentoBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Endereco LTDA', nome_fantasia='Endereco',
            cnpj='51345678000101',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Endereco Matriz',
            nome_fantasia='Matriz', cnpj='51345678000102', uf='RN',
            is_matriz=True,
        )
        cls.perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin enderecos', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='enderecos@teste.local', nome='Estoquista', password='x' * 12,
            empresa=cls.empresa, filial=cls.filial, perfil=cls.perfil,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='UN', descricao='Unidade',
            tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        cls.produto = Produto.objects.create(
            filial=cls.filial, unidade_medida=cls.unidade,
            descricao='Copo 300 ml', ncm='39241000',
            permite_venda_sem_estoque=False,
        )
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.filial)
        cls.galpao = Deposito.objects.create(
            filial=cls.filial, nome='Galpao', tipo=Deposito.Tipo.REVENDA,
        )

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.usuario)
        session = self.client.session
        session['filial_ativa_id'] = self.filial.pk
        session.save()
        self.estoque = Estoque.objects.create(
            produto=self.produto, filial=self.filial, deposito=self.galpao,
            quantidade_atual=Decimal('100'), quantidade_disponivel=Decimal('100'),
        )


class ProdutoLocalizacoesTests(EnderecamentoBase):
    def test_salva_varias_localizacoes_no_mesmo_deposito(self):
        resposta = self.client.post(
            reverse('produtos:produto-localizacoes-update', args=[self.produto.pk]),
            data=json.dumps({'localizacoes': [
                {'deposito_id': self.galpao.pk, 'codigo': 'a-01-02', 'descricao': 'Rua A', 'quantidade': '60', 'prioridade': 10},
                {'deposito_id': self.galpao.pk, 'codigo': 'A-02-01', 'descricao': '', 'quantidade': '30', 'prioridade': 20},
            ]}),
            content_type='application/json',
        )

        self.assertEqual(resposta.status_code, 200, resposta.content)
        self.assertEqual(SaldoEndereco.objects.count(), 2)
        self.assertEqual(
            list(EnderecoEstoque.objects.order_by('codigo').values_list('codigo', flat=True)),
            ['A-01-02', 'A-02-01'],
        )
        self.produto.refresh_from_db()
        self.assertEqual(self.produto.localizacao_estoque, 'A-01-02')

    def test_recusa_quantidade_enderecada_maior_que_saldo_do_deposito(self):
        resposta = self.client.post(
            reverse('produtos:produto-localizacoes-update', args=[self.produto.pk]),
            data=json.dumps({'localizacoes': [
                {'deposito_id': self.galpao.pk, 'codigo': 'A-01', 'quantidade': '100.001'},
            ]}),
            content_type='application/json',
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertIn('supera o saldo', resposta.json()['error'])
        self.assertFalse(SaldoEndereco.objects.exists())

    def test_recusa_linha_malformada_sem_erro_interno(self):
        resposta = self.client.post(
            reverse('produtos:produto-localizacoes-update', args=[self.produto.pk]),
            data=json.dumps({'localizacoes': ['linha-invalida']}),
            content_type='application/json',
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertIn('linha 1', resposta.json()['error'])


class SaldoEnderecoOperacaoTests(EnderecamentoBase):
    def _criar_saldo(self, codigo, quantidade, prioridade):
        endereco = EnderecoEstoque.objects.create(
            filial=self.filial, deposito=self.galpao, codigo=codigo,
        )
        return SaldoEndereco.objects.create(
            estoque=self.estoque, endereco=endereco,
            quantidade=Decimal(quantidade), prioridade_separacao=prioridade,
        )

    def test_saida_baixa_enderecos_na_ordem_de_prioridade(self):
        primeiro = self._criar_saldo('A-01', '20', 10)
        segundo = self._criar_saldo('B-01', '50', 20)

        MovimentacaoService.registrar_movimentacao(
            produto_id=self.produto.pk,
            filial_id=self.filial.pk,
            deposito_id=self.galpao.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.SAIDA,
            quantidade=Decimal('25'),
            usuario_id=self.usuario.pk,
        )

        primeiro.refresh_from_db()
        segundo.refresh_from_db()
        self.assertEqual(primeiro.quantidade, Decimal('0'))
        self.assertEqual(segundo.quantidade, Decimal('45'))

    def test_matriz_depositos_inclui_localizacoes_para_separacao(self):
        self._criar_saldo('A-01', '20', 10)
        anexar_saldos_por_deposito(
            produtos=[self.produto], filial=self.filial, depositos=[self.galpao],
        )
        local = self.produto.saldos_depositos[0]['localizacoes'][0]
        self.assertEqual(local['codigo'], 'A-01')
        self.assertEqual(local['quantidade'], Decimal('20'))


class RelatorioLocalizacoesTests(EnderecamentoBase):
    def test_relatorio_mostra_endereco_e_saldo_sem_endereco(self):
        endereco = EnderecoEstoque.objects.create(
            filial=self.filial, deposito=self.galpao, codigo='A-03-02',
        )
        SaldoEndereco.objects.create(
            estoque=self.estoque, endereco=endereco, quantidade=Decimal('40'),
        )
        resposta = self.client.get(reverse('estoque:relatorio-localizacoes'))
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'A-03-02')
        self.assertContains(resposta, 'SEM ENDERECO')
        linha_sem_endereco = next(
            linha for linha in resposta.context['linhas']
            if linha['codigo'] == 'SEM ENDERECO'
        )
        self.assertEqual(linha_sem_endereco['quantidade'], Decimal('60'))

    def test_exporta_csv(self):
        resposta = self.client.get(
            reverse('estoque:relatorio-localizacoes'), {'export': 'csv'},
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta['Content-Type'], 'text/csv; charset=utf-8')
        self.assertIn('SEM ENDERECO', resposta.content.decode('utf-8-sig'))
