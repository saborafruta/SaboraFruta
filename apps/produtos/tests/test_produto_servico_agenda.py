from django.test import TestCase

from apps.core.models import Empresa, Filial
from apps.produtos.forms import ProdutoForm
from apps.produtos.models import (
    CategoriaProduto,
    CategoriaProdutoFilial,
    Produto,
    UnidadeMedida,
    UnidadeMedidaFilial,
)


class ProdutoServicoAgendaFormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Empresa Agenda LTDA',
            nome_fantasia='Empresa Agenda',
            cnpj='12345678000195',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa,
            razao_social='Filial Agenda',
            nome_fantasia='Filial Agenda',
            cnpj='12345678000196',
            uf='CE',
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
            nome='Serviços',
        )
        CategoriaProdutoFilial.objects.create(categoria=cls.categoria, filial=cls.filial)

    def dados_validos(self, **overrides):
        dados = {
            'descricao': 'Corte de cabelo',
            'categoria': self.categoria.pk,
            'tipo_produto': Produto.TipoProduto.SERVICO,
            'unidade_medida': self.unidade.pk,
            'fator_conversao_compra': '1',
            'condicao_armazenamento': Produto.CondicaoArmazenamento.AMBIENTE,
            'ncm': '00000000',
            'origem_produto': Produto.OrigemProduto.NACIONAL,
            'cfop_venda_interna': '5933',
            'cfop_venda_interestadual': '6933',
            'cfop_compra': '1933',
            'preco_venda': '50,00',
            'preco_custo': '0,00',
            'moeda': Produto.Moeda.BRL,
            'margem_desejada': '0,00',
            'estoque_minimo': '0',
            'ponto_reposicao': '0',
            'metodo_saida': Produto.MetodoSaida.FEFO,
            'unidade_pesagem': Produto.UnidadePeso.KG,
            'unidade_peso': Produto.UnidadePeso.KG,
            'unidade_dimensao': Produto.UnidadeDimensao.CM,
            'quantidade_por_embalagem': '1',
            'empilhamento_maximo': '0',
            'agendavel': 'on',
            'duracao_servico_minutos': '30',
            'intervalo_apos_servico_minutos': '10',
        }
        dados.update(overrides)
        return dados

    def criar_form(self, **overrides):
        return ProdutoForm(
            data=self.dados_validos(**overrides),
            empresa=self.empresa,
            filial=self.filial,
            estoque_atual=0,
        )

    def test_servico_agendavel_exige_duracao(self):
        form = self.criar_form(duracao_servico_minutos='')

        self.assertFalse(form.is_valid())
        self.assertIn('duracao_servico_minutos', form.errors)

    def test_produto_comum_nao_pode_ser_agendavel(self):
        form = self.criar_form(tipo_produto=Produto.TipoProduto.UNITARIO)

        self.assertFalse(form.is_valid())
        self.assertIn('agendavel', form.errors)

    def test_servico_agendavel_salva_duracao_e_intervalo(self):
        form = self.criar_form()

        self.assertTrue(form.is_valid(), form.errors.as_json())
        produto = form.save(commit=False)
        produto.filial = self.filial
        produto.save()

        self.assertTrue(produto.agendavel)
        self.assertEqual(produto.duracao_servico_minutos, 30)
        self.assertEqual(produto.intervalo_apos_servico_minutos, 10)
