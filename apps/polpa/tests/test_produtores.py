"""
O histórico por produtor — de quem comprar.

NÃO É O CADASTRO DE FORNECEDOR, que já existe em `cadastros`. Esta tela responde
outra pergunta, e as CONTAS são o que ela tem de próprio: volume sozinho engana,
porque um produtor que entrega muito e leva 8% de desconto por impureza sai mais
caro que um que entrega menos e limpo.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.models import Fornecedor
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.polpa.models import Fruta, Recebimento
from apps.polpa.services import RecebimentoService

S = Recebimento.Status


class ProdutoresBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Produtores LTDA', nome_fantasia='Produtores',
            cnpj='91145678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1, segmento='polpa_frutas',
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='91145678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='compras@polpa.local', nome='Compras', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.boa_vista = Fornecedor.objects.create(
            filial=cls.filial, razao_social='Sitio Boa Vista',
            cpf_cnpj='12345678000190',
        )
        cls.santa_rita = Fornecedor.objects.create(
            filial=cls.filial, razao_social='Fazenda Santa Rita',
            cpf_cnpj='98765432000110',
        )
        cls.manga = Fruta.objects.create(
            filial=cls.filial, nome='Manga', variedade='Tommy',
            brix_minimo=Decimal('12.00'), rendimento_esperado=Decimal('50.00'),
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        self._numero = 0

    def _carga(self, produtor=None, bruto='1200', tara='200', desconto='0',
               preco='2.00', status=S.APROVADO, **extra):
        self._numero += 1
        return Recebimento.objects.create(
            filial=self.filial, numero=self._numero, fruta=self.manga,
            produtor=produtor or self.boa_vista, data=timezone.localdate(),
            peso_bruto=Decimal(bruto), tara=Decimal(tara),
            desconto_kg=Decimal(desconto), preco_kg=Decimal(preco),
            status=status, **extra,
        )

    def _linha(self, produtor):
        historico = RecebimentoService.historico_por_produtor(self.filial)
        return next(l for l in historico if l['produtor'].pk == produtor.pk)


class AsContasTests(ProdutoresBase):

    def test_soma_o_que_foi_aceito_e_nao_o_que_chegou(self):
        """
        Aceito e' liquido menos o desconto da classificacao. Somar o liquido
        faria o historico dizer que se pagou por fruta que foi descontada.
        """
        self._carga(bruto='1200', tara='200', desconto='100')

        linha = self._linha(self.boa_vista)

        self.assertEqual(linha['kg_liquido'], Decimal('1000'))
        self.assertEqual(linha['kg_aceito'], Decimal('900'))

    def test_a_taxa_de_desconto_e_sobre_o_liquido(self):
        """100 kg descontados de 1000 kg que chegaram sao 10%."""
        self._carga(bruto='1200', tara='200', desconto='100')

        self.assertEqual(self._linha(self.boa_vista)['taxa_desconto'], Decimal('10.0'))

    def test_carga_aberta_nao_conta_como_entregue(self):
        """
        Ela ainda pode voltar no caminhao. Soma-la faria o historico prometer
        fruta que talvez nao tenha chegado.
        """
        self._carga(status=S.APROVADO, bruto='1200', tara='200')
        self._carga(status=S.PESAGEM, bruto='5000', tara='200')

        linha = self._linha(self.boa_vista)

        self.assertEqual(linha['kg_aceito'], Decimal('1000'))
        self.assertEqual(linha['cargas'], 2)
        self.assertEqual(linha['abertas'], 1)

    def test_a_taxa_de_recusa_e_sobre_o_que_foi_decidido(self):
        """
        Carga em aberto ainda nao foi julgada: incluir no denominador faria a
        taxa de um produtor melhorar so' porque ele tem carga esperando.
        """
        self._carga(status=S.APROVADO)
        self._carga(status=S.RECUSADO, motivo_recusa='Fruta verde.')
        self._carga(status=S.PESAGEM)

        linha = self._linha(self.boa_vista)

        self.assertEqual(linha['decididas'], 2)
        self.assertEqual(linha['recusadas'], 1)
        self.assertEqual(linha['taxa_recusa'], Decimal('50.0'))

    def test_carga_recusada_nao_soma_peso_nem_valor(self):
        """Ela voltou no caminhao: nao entrou fruta e nao saiu dinheiro."""
        self._carga(status=S.RECUSADO, bruto='9000', tara='200',
                    motivo_recusa='Fruta verde.')

        linha = self._linha(self.boa_vista)

        self.assertEqual(linha['kg_aceito'], Decimal('0'))
        self.assertEqual(linha['valor'], Decimal('0'))

    def test_o_preco_medio_sai_do_que_se_pagou_pelo_que_se_aceitou(self):
        """
        E' a pergunta de quem negocia: quanto o quilo REALMENTE custou. Duas
        cargas a precos diferentes tem que dar a media ponderada, e nao a media
        dos precos de tabela.
        """
        self._carga(bruto='1200', tara='200', preco='2.00')   # 1000 kg a 2,00
        self._carga(bruto='400', tara='200', preco='4.00')    #  200 kg a 4,00

        linha = self._linha(self.boa_vista)

        # (1000*2 + 200*4) / 1200 = 2800/1200 = 2,3333
        self.assertEqual(linha['valor'], Decimal('2800.00'))
        self.assertEqual(linha['preco_medio'], Decimal('2.3333'))

    def test_o_brix_medio_ignora_carga_sem_medicao(self):
        """
        Contar carga nao medida como zero puxaria a media para baixo e faria um
        produtor bom parecer ruim por causa de analise que ninguem fez.
        """
        self._carga(brix=Decimal('14.00'))
        self._carga(brix=Decimal('16.00'))
        self._carga()  # sem brix

        self.assertEqual(self._linha(self.boa_vista)['brix_medio'], Decimal('15.00'))

    def test_sem_nenhuma_medicao_o_brix_e_nulo_e_nao_zero(self):
        """Zero diria "fruta sem acucar"; nulo diz "ninguem mediu"."""
        self._carga()

        self.assertIsNone(self._linha(self.boa_vista)['brix_medio'])

    def test_a_polpa_prevista_usa_o_rendimento_da_fruta(self):
        """900 kg aceitos de uma fruta que rende 50% sao 450 kg de polpa."""
        self._carga(bruto='1200', tara='200', desconto='100')

        self.assertEqual(
            self._linha(self.boa_vista)['polpa_prevista'], Decimal('450.000'),
        )


class AOrdemEOEscopoTests(ProdutoresBase):

    def test_quem_entrega_mais_aparece_primeiro(self):
        """
        E' de quem a fabrica mais depende, e portanto onde um problema de
        qualidade custa mais caro.
        """
        self._carga(produtor=self.boa_vista, bruto='600', tara='100')
        self._carga(produtor=self.santa_rita, bruto='5000', tara='100')

        historico = RecebimentoService.historico_por_produtor(self.filial)

        self.assertEqual(historico[0]['produtor'].pk, self.santa_rita.pk)

    def test_so_aparece_quem_ja_entregou(self):
        """
        Listar o cadastro inteiro encheria a tela de fornecedor de embalagem e
        de manutencao, que nao sao produtores de fruta.
        """
        self._carga(produtor=self.boa_vista)

        historico = RecebimentoService.historico_por_produtor(self.filial)

        self.assertEqual(len(historico), 1)
        self.assertEqual(historico[0]['produtor'].pk, self.boa_vista.pk)

    def test_a_busca_filtra_por_nome(self):
        self._carga(produtor=self.boa_vista)
        self._carga(produtor=self.santa_rita)

        historico = RecebimentoService.historico_por_produtor(self.filial, 'Santa')

        self.assertEqual(len(historico), 1)
        self.assertEqual(historico[0]['produtor'].pk, self.santa_rita.pk)


class ATelaTests(ProdutoresBase):

    def test_a_tela_abre_e_mostra_o_produtor(self):
        self._carga(produtor=self.boa_vista, bruto='1200', tara='200')

        resposta = self.client.get(reverse('polpa:recebimento-produtores'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Sitio Boa Vista')

    def test_a_tela_vazia_explica_de_onde_vem_o_historico(self):
        """
        Sem esta frase, quem tem fornecedor cadastrado e nenhuma entrega acha
        que a tela esta' quebrada.
        """
        resposta = self.client.get(reverse('polpa:recebimento-produtores'))

        self.assertContains(resposta, 'nasce do primeiro romaneio')

    def test_a_rota_do_menu_abre_a_tela_de_verdade(self):
        """
        O hub descobre tela pronta por RESOLUCAO DE ROTA: enquanto o endereco do
        menu resolvesse para a `ItemView`, o selo "em construcao" continuaria
        aparecendo.
        """
        from django.urls import resolve

        from apps.polpa.views import ItemView

        endereco = reverse('polpa:item', args=['recebimento', 'produtores'])

        self.assertIsNot(
            getattr(resolve(endereco).func, 'view_class', None), ItemView,
        )
        self.assertEqual(endereco, reverse('polpa:recebimento-produtores'))
