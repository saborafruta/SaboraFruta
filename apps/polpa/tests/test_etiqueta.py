"""
A etiqueta do lote: o que ela diz e para onde o QR leva.

O QUE ESTES TESTES CERCAM:

  · A ETIQUETA É O ÚNICO PEDAÇO DO SISTEMA QUE SAI DA FÁBRICA. Vai colada no
    saco que chega ao supermercado, e é o que a fiscalização lê — cada campo
    dela tem uma origem única no cadastro, e nenhum é digitado de novo;

  · O CÓDIGO DE BARRAS É O EAN QUANDO EXISTE. É ele que o caixa do
    supermercado lê, e uma etiqueta com outro código faria a venda travar.
    Sem EAN vai o número do lote, que serve ao almoxarifado;

  · CÓDIGO QUE O CODE128 NÃO REPRESENTA NÃO É DESENHADO. Um desenho errado é
    lido como OUTRO código — erro silencioso, o pior tipo aqui;

  · O QR ABRE A RASTREABILIDADE daquele lote, dentro do sistema. Página
    pública mostraria fornecedor, custo e processo para quem pegasse a
    embalagem no mercado;

  · NADA É GRAVADO: a etiqueta é derivada do lote a cada impressão. Uma
    cópia gravada continuaria dizendo "válido até 10/03" depois de alguém
    corrigir a validade.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import LoteProduto
from apps.polpa.models import Camara, FichaProduto
from apps.polpa.services import ArmazenagemService, CatalogoService, EtiquetaService
from apps.produtos.models import UnidadeMedida, UnidadeMedidaFilial

T = FichaProduto.Tipo


class EtiquetaBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Polpas Etiqueta LTDA', nome_fantasia='Etiqueta',
            cnpj='63345678000191', segmento='polpa_frutas',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='63345678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='UN', descricao='Unidade',
            tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='chefe@etiqueta.local', nome='Chefe', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        self.produto = self._produto()

    def _produto(self, **extras):
        dados = {
            'tipo': T.POLPA, 'descricao': 'Polpa de manga 100 g', 'codigo': 'PM100',
            'unidade_medida': self.unidade, 'validade_dias': 180,
            'peso_liquido': Decimal('0.100'), 'sabor': 'Manga',
            'codigo_barras': '7891234567890', 'congelado': True,
            'temperatura_maxima': Decimal('-18'), 'registro_mapa': 'SIF 1234',
        }
        dados.update(extras)
        return CatalogoService.salvar(self.filial, dados).produto

    def _lote(self, produto=None, quantidade=Decimal('500'), numero='PM260825-00007'):
        return LoteProduto.objects.create(
            filial=self.filial, produto=produto or self.produto, numero_lote=numero,
            data_fabricacao=timezone.localdate(),
            data_validade=timezone.localdate() + timedelta(days=180),
            quantidade_inicial=quantidade, quantidade_atual=quantidade,
            status=LoteProduto.Status.ATIVO,
        )


class DadosTests(EtiquetaBase):
    """Cada campo com a origem dele."""

    def test_a_etiqueta_traz_tudo_que_a_secao_pede(self):
        lote = self._lote()

        dados = EtiquetaService.dados(lote)

        self.assertIn('Polpa de manga 100 g', dados['nome'])
        self.assertEqual(dados['codigo'], 'PM100')
        self.assertEqual(dados['numero_lote'], 'PM260825-00007')
        self.assertEqual(dados['fabricacao'], timezone.localdate())
        self.assertEqual(
            dados['validade'], timezone.localdate() + timedelta(days=180),
        )
        # 500 un × 0,100 kg
        self.assertEqual(dados['peso'], Decimal('50.000'))
        self.assertEqual(dados['codigo_barras'], '7891234567890')

    def test_o_nome_traz_o_sabor_quando_ele_nao_esta_na_descricao(self):
        produto = self._produto(
            descricao='Polpa 100 g', codigo='P100', sabor='Acerola',
            codigo_barras='7890000000024',
        )
        lote = self._lote(produto, numero='PA-1')

        self.assertIn('Acerola', EtiquetaService.dados(lote)['nome'])

    def test_produto_sem_peso_nao_inventa_zero(self):
        produto = self._produto(
            descricao='Polpa a granel', codigo='GRAN', peso_liquido=None,
            codigo_barras='7890000000031',
        )
        lote = self._lote(produto, numero='GR-1')

        self.assertIsNone(EtiquetaService.dados(lote)['peso'])

    def test_o_endereco_aparece_quando_o_lote_esta_guardado(self):
        camara = Camara.objects.create(
            filial=self.filial, nome='Camara 1', temperatura_max=Decimal('-18'),
        )
        lote = self._lote()
        ArmazenagemService.guardar(lote, camara, {'endereco': 'Rua 3'})

        self.assertEqual(EtiquetaService.dados(lote)['onde'], 'Rua 3')


class CodigoDeBarrasTests(EtiquetaBase):
    """O que vai desenhado em barras."""

    def test_o_ean_do_produto_tem_precedencia(self):
        """
        É ele que o caixa do supermercado lê — outro código faria a venda
        travar.
        """
        lote = self._lote()

        self.assertEqual(EtiquetaService.codigo_de_barras(lote), '7891234567890')

    def test_sem_ean_vai_o_numero_do_lote(self):
        """Para o almoxarifado, que precisa identificar aquele lote, serve mais."""
        produto = self._produto(
            descricao='Polpa sem EAN', codigo='SEMEAN', codigo_barras='',
        )
        lote = self._lote(produto, numero='SE-2026-0001')

        self.assertEqual(EtiquetaService.codigo_de_barras(lote), 'SE-2026-0001')

    def test_codigo_fora_do_code128_nao_e_desenhado(self):
        """
        Um desenho errado é lido como OUTRO código — erro silencioso, o pior
        tipo aqui.
        """
        produto = self._produto(
            descricao='Polpa acentuada', codigo='ACC', codigo_barras='',
        )
        lote = self._lote(produto, numero='LOTE-Ç-ÃO')

        self.assertEqual(EtiquetaService.codigo_de_barras(lote), '')


class PendenciaTests(EtiquetaBase):
    """O que falta para a etiqueta ir para a rua."""

    def test_etiqueta_completa_nao_acusa_nada(self):
        lote = self._lote()

        self.assertEqual(EtiquetaService.pendencias(lote), [])

    def test_lote_sem_validade_e_acusado(self):
        lote = self._lote()
        lote.data_validade = None
        lote.save(update_fields=['data_validade'])

        self.assertTrue(
            any('validade' in p.lower() for p in EtiquetaService.pendencias(lote))
        )

    def test_produto_sem_ean_e_acusado_sem_impedir(self):
        """
        A etiqueta de uso interno vale sem EAN. Travar faria a fábrica
        imprimir num Word à parte — e aí a etiqueta perde qualquer relação
        com o sistema.
        """
        produto = self._produto(
            descricao='Polpa interna', codigo='INT', codigo_barras='',
        )
        lote = self._lote(produto, numero='IN-1')

        pendencias = EtiquetaService.pendencias(lote)

        self.assertTrue(any('barras' in p.lower() for p in pendencias))
        # E mesmo assim a etiqueta desenha alguma coisa.
        self.assertEqual(EtiquetaService.codigo_de_barras(lote), 'IN-1')


class TelasEtiquetaTests(EtiquetaBase):
    """As telas e os desenhos."""

    def test_a_lista_e_a_etiqueta_abrem(self):
        lote = self._lote()

        self.assertEqual(
            self.client.get(reverse('polpa:etiqueta-list')).status_code, 200,
        )
        self.assertEqual(
            self.client.get(reverse('polpa:etiqueta-lote', args=[lote.pk])).status_code,
            200,
        )

    def test_a_rota_do_menu_nao_cai_no_placeholder(self):
        from django.urls import resolve

        from apps.polpa.views import ItemView

        achado = resolve(reverse('polpa:item', args=['frio', 'etiquetas']))

        self.assertIsNot(getattr(achado.func, 'view_class', None), ItemView)

    def test_a_etiqueta_mostra_lote_validade_e_peso(self):
        lote = self._lote()

        resposta = self.client.get(reverse('polpa:etiqueta-lote', args=[lote.pk]))

        self.assertContains(resposta, 'PM260825-00007')
        self.assertContains(resposta, 'Validade')
        self.assertContains(resposta, 'SIF 1234')

    def test_as_copias_saem_na_folha(self):
        lote = self._lote()

        resposta = self.client.get(
            reverse('polpa:etiqueta-lote', args=[lote.pk]) + '?copias=6'
        )

        self.assertEqual(len(list(resposta.context['copias'])), 6)

    def test_copias_absurdas_sao_limitadas(self):
        """
        Mil etiquetas numa página só travariam o navegador de quem digitou
        um zero a mais.
        """
        lote = self._lote()

        resposta = self.client.get(
            reverse('polpa:etiqueta-lote', args=[lote.pk]) + '?copias=999'
        )

        self.assertEqual(resposta.context['quantidade_copias'], 60)

    def test_o_qr_sai_como_png(self):
        lote = self._lote()

        resposta = self.client.get(reverse('polpa:etiqueta-qr', args=[lote.pk]))

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta['Content-Type'], 'image/png')
        self.assertTrue(resposta.content.startswith(b'\x89PNG'))

    def test_as_barras_saem_como_svg(self):
        lote = self._lote()

        resposta = self.client.get(reverse('polpa:etiqueta-barras', args=[lote.pk]))

        self.assertEqual(resposta.status_code, 200)
        self.assertIn('svg', resposta['Content-Type'])

    def test_sem_codigo_representavel_as_barras_dao_404(self):
        produto = self._produto(
            descricao='Polpa acentuada', codigo='ACC2', codigo_barras='',
        )
        lote = self._lote(produto, numero='LOTE-Ç')

        resposta = self.client.get(reverse('polpa:etiqueta-barras', args=[lote.pk]))

        self.assertEqual(resposta.status_code, 404)

    def test_o_qr_aponta_para_a_rastreabilidade_do_lote(self):
        """
        A tela de rastreabilidade já aceita `?lote=` — uma rota paralela
        daria duas telas para a mesma consulta, e a segunda é a que ninguém
        mantém.
        """
        lote = self._lote()
        pedido = self.client.get(reverse('polpa:etiqueta-lote', args=[lote.pk]))

        url = pedido.context['url_qr']

        self.assertIn(reverse('lotes:rastreabilidade'), url)
        self.assertIn(f'lote={lote.pk}', url)

    def test_lote_de_outra_filial_nao_gera_etiqueta(self):
        outra = Filial.objects.create(
            empresa=self.empresa, razao_social='Filial 2', cnpj='63345678000353',
        )
        alheio = LoteProduto.objects.create(
            filial=outra, produto=self.produto, numero_lote='X-1',
            quantidade_inicial=Decimal('10'), quantidade_atual=Decimal('10'),
        )

        resposta = self.client.get(reverse('polpa:etiqueta-lote', args=[alheio.pk]))

        self.assertEqual(resposta.status_code, 404)
