"""
Adicionar mercadoria para venda fora do estabelecimento.

O QUE DIFERENCIA ESTA OPERAÇÃO das outras não é o formulário — é que a
mercadoria sai da empresa continuando a ser da empresa. Por isso o lote
importa, e por isso o fiscal precisa estar visível ANTES de incluir: depois
que a carga fecha, a mercadoria já saiu do estoque e o caminhão está esperando.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.models import Cliente, ClienteFilial
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import LoteProduto
from apps.fiscal.models import NaturezaOperacao, RegraNaturezaOperacao
from apps.logistica.forms_carga import PERFIS, ItemCargaForm
from apps.logistica.models import ItemCarga, Viagem
from apps.produtos.models import Produto, ProdutoFilial
from apps.produtos.models.unidade import UnidadeMedida, UnidadeMedidaFilial

VENDA_FORA = NaturezaOperacao.Especie.REMESSA_VENDA_FORA


class VendaForaDoEstabelecimentoTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Fora LTDA', nome_fantasia='Fora',
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
            email='fora@viagem.local', nome='Fora', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.produto = Produto.objects.create(
            filial=cls.filial, unidade_medida=cls.unidade,
            descricao='Caixa de polpa', codigo='CX1', ncm='20079900',
            controla_lote=True, preco_venda=Decimal('10'),
        )
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.filial)

        cls.natureza = NaturezaOperacao.objects.create(
            filial=cls.filial, codigo='remessa_venda_fora',
            descricao='Remessa para venda fora do estabelecimento',
            especie=VENDA_FORA, exige_destinatario=False, gera_financeiro=False,
        )
        # A regra parametrizada: 5904 dentro do estado, 6904 para fora.
        RegraNaturezaOperacao.objects.create(
            natureza=cls.natureza, cfop='5904', csosn='400',
            cst_pis='49', cst_cofins='49',
        )
        RegraNaturezaOperacao.objects.create(
            natureza=cls.natureza, cfop='6904', somente_interestadual=True,
            csosn='400',
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        self.viagem = Viagem.objects.create(
            filial=self.filial, numero=1, motorista_nome='Seu Zé',
            veiculo_placa='ABC1D23', vendedor=self.usuario,
        )
        self.url_detalhe = reverse('logistica:viagem-detail', args=[self.viagem.pk])
        self.url_fiscal = reverse(
            'logistica:viagem-tratamento-fiscal', args=[self.viagem.pk],
        )

    def _lote(self, numero='L1', quantidade='500'):
        return LoteProduto.objects.create(
            filial=self.filial, produto=self.produto, numero_lote=numero,
            quantidade_inicial=Decimal(quantidade),
            quantidade_atual=Decimal(quantidade),
            data_validade=timezone.localdate(),
        )

    def _incluir(self, **campos):
        base = {'produto': self.produto.pk, 'quantidade': '200', 'valor_unitario': '10'}
        base.update(campos)
        return self.client.post(
            reverse('logistica:viagem-item-create', args=[self.viagem.pk, VENDA_FORA]),
            base, follow=True,
        )

    # ── Os campos que a operação pede ────────────────────────────────────

    def test_o_formulario_oferece_lote(self):
        """
        A mercadoria passa dias na rua e volta em parte; sem saber qual lote
        saiu, o que retorna não tem como voltar para o lote certo.
        """
        self._lote()

        html = self.client.get(self.url_detalhe).content.decode()

        self.assertIn('name="lote"', html)

    def test_so_lote_com_saldo_e_oferecido(self):
        """
        Oferecer lote zerado convida a carregar o que não existe, e o erro só
        aparece na baixa de estoque, já no fechamento.
        """
        com_saldo = self._lote('L1', '500')
        LoteProduto.objects.create(
            filial=self.filial, produto=self.produto, numero_lote='L2',
            quantidade_inicial=Decimal('100'), quantidade_atual=Decimal('0'),
        )

        form = ItemCargaForm(viagem=self.viagem, especie=VENDA_FORA)

        self.assertEqual(list(form.fields['lote'].queryset), [com_saldo])

    def test_o_lote_escolhido_fica_na_linha_da_carga(self):
        lote = self._lote()

        self._incluir(lote=lote.pk)

        self.assertEqual(ItemCarga.objects.get().lote, lote)

    def test_o_valor_total_sai_da_quantidade_pelo_unitario(self):
        self._incluir(quantidade='200', valor_unitario='10')

        self.assertEqual(ItemCarga.objects.get().valor_total, Decimal('2000.00'))

    def test_o_peso_entra_quando_informado(self):
        self._incluir(peso_kg='500')

        self.assertEqual(ItemCarga.objects.get().peso_kg, Decimal('500.000'))

    # ── O fiscal é parametrizado, e visível antes de incluir ─────────────

    def test_o_cfop_vem_da_parametrizacao(self):
        """
        Não é digitado: sai da regra que a contabilidade cadastrou para esta
        natureza.
        """
        dados = self.client.get(self.url_fiscal, {
            'produto': self.produto.pk, 'natureza': self.natureza.pk,
        }).json()

        self.assertTrue(dados['ok'])
        self.assertEqual(dados['fiscal']['cfop'], '5904')

    def test_o_cst_csosn_vem_da_parametrizacao(self):
        dados = self.client.get(self.url_fiscal, {
            'produto': self.produto.pk, 'natureza': self.natureza.pk,
        }).json()

        self.assertEqual(dados['fiscal']['csosn'], '400')
        self.assertEqual(dados['fiscal']['cst_pis'], '49')
        self.assertEqual(dados['fiscal']['cst_cofins'], '49')

    def test_o_ncm_e_a_unidade_vem_do_produto(self):
        dados = self.client.get(self.url_fiscal, {
            'produto': self.produto.pk, 'natureza': self.natureza.pk,
        }).json()

        self.assertEqual(dados['produto']['ncm'], '20079900')
        self.assertEqual(dados['produto']['unidade'], 'CX')

    def test_destino_em_outra_uf_muda_o_cfop(self):
        """A mesma remessa é 5904 dentro do estado e 6904 para fora."""
        cliente_pb = Cliente.objects.create(
            filial=self.filial, razao_social='Cliente PB',
            cpf_cnpj='12345678902', uf='PB',
        )
        ClienteFilial.objects.create(cliente=cliente_pb, filial=self.filial)

        dados = self.client.get(self.url_fiscal, {
            'produto': self.produto.pk, 'natureza': self.natureza.pk,
            'cliente': cliente_pb.pk,
        }).json()

        self.assertEqual(dados['fiscal']['cfop'], '6904')
        self.assertTrue(dados['fiscal']['justificativa']['interestadual'])

    def test_sem_regra_o_erro_aparece_antes_de_incluir(self):
        """
        Descobrir que a regra estava errada na hora de transmitir é tarde: a
        mercadoria já saiu do estoque e o caminhão está esperando.
        """
        RegraNaturezaOperacao.objects.filter(natureza=self.natureza).delete()

        dados = self.client.get(self.url_fiscal, {
            'produto': self.produto.pk, 'natureza': self.natureza.pk,
        }).json()

        self.assertFalse(dados['ok'])
        self.assertIn('Cadastre a regra', dados['erro'])

    def test_sem_produto_o_endpoint_pede_o_produto(self):
        dados = self.client.get(self.url_fiscal, {
            'natureza': self.natureza.pk,
        }).json()

        self.assertFalse(dados['ok'])
        self.assertIn('Escolha o produto', dados['erro'])

    def test_produto_de_outra_filial_nao_resolve(self):
        outra = Filial.objects.create(
            empresa=self.empresa, razao_social='Segunda',
            cnpj='31345678000677', uf='RN', cidade='Mossoro',
        )
        alheio = Produto.objects.create(
            filial=outra, unidade_medida=self.unidade,
            descricao='Alheio', codigo='AL1', ncm='20079900',
        )
        ProdutoFilial.objects.create(produto=alheio, filial=outra)

        dados = self.client.get(self.url_fiscal, {
            'produto': alheio.pk, 'natureza': self.natureza.pk,
        }).json()

        self.assertFalse(dados['ok'])

    def test_a_tela_mostra_o_painel_fiscal(self):
        html = self.client.get(self.url_detalhe).content.decode()

        self.assertIn('tratamentoFiscal', html)
        for rotulo in ('CFOP', 'CST / CSOSN', 'NCM', 'Unidade', 'Valor total'):
            self.assertIn(rotulo, html, f'{rotulo} sumiu do painel fiscal')

    # ── A identificação na carga ─────────────────────────────────────────

    def test_a_carga_identifica_a_mercadoria_em_venda_fora(self):
        """
        Ela sai da empresa mas continua sendo da empresa, e vai ter que voltar
        ou ser vendida -- quem olha a lista precisa distinguir isso de uma venda
        comum sem ler o CFOP.
        """
        self._incluir()

        html = self.client.get(self.url_detalhe).content.decode()

        self.assertIn('Mercadoria em venda fora do estabelecimento', html)

    def test_a_etiqueta_esta_declarada_no_perfil(self):
        self.assertEqual(
            PERFIS[VENDA_FORA]['etiqueta'],
            'Mercadoria em venda fora do estabelecimento',
        )

    def test_o_lote_aparece_na_linha_da_carga(self):
        lote = self._lote('LOTE-A')

        self._incluir(lote=lote.pk)
        html = self.client.get(self.url_detalhe).content.decode()

        self.assertIn('LOTE-A', html)

    def test_o_titulo_da_pagina_nao_carrega_script(self):
        """
        Um `{% endblock %}` errado joga o script inteiro para dentro do
        `{% block title %}`, e a aba do navegador passa a exibir codigo. A
        pagina continua funcionando, e por isso ninguem percebe.
        """
        import re

        html = self.client.get(self.url_detalhe).content.decode()
        titulo = re.search(r'<title>(.*?)</title>', html, re.S)

        self.assertIsNotNone(titulo)
        self.assertNotIn('<script', titulo.group(1))
        self.assertNotIn('function', titulo.group(1))

    def test_a_tela_nao_vaza_sintaxe_de_template(self):
        self._incluir()

        html = self.client.get(self.url_detalhe).content.decode()

        for resto in ('{#', '#}', '{%', '%}'):
            self.assertNotIn(resto, html, 'vazou sintaxe de template no HTML')
