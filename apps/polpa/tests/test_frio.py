"""
Câmara fria: posições, temperatura e os quatro alertas.

O QUE ESTES TESTES CERCAM:

  · ENDEREÇO ESTRUTURADO. "Rua 3", "rua 3", "R3" e "rua três" são a mesma
    prateleira para a pessoa e quatro endereços para o sistema — e a
    pergunta "o que está na rua 3?" volta quebrada em quatro;

  · A TEMPERATURA ATUAL É A ÚLTIMA LEITURA, com hora. Um campo editável
    guarda o número digitado por último e não diz quando;

  · DESVIO É EVENTO, o resto é CONDIÇÃO. O desvio vira notificação na hora e
    não se desliga quando a câmara volta à faixa (o que estava dentro já
    passou por aquilo); vencimento, capacidade e bloqueio são listas, que
    somem quando alguém resolve;

  · SEM MEDIDA NÃO SE AFIRMA NADA. Câmara sem leitura entra no alerta;
    câmara sem capacidade cadastrada NÃO entra no de capacidade — alerta
    baseado em palpite é o que faz a fábrica desligar os alertas.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Empresa, Filial, Notificacao, PerfilAcesso, Usuario
from apps.core.services.exceptions import DomainError
from apps.estoque.models import LoteProduto
from apps.polpa.models import (
    Camara, FichaProduto, LeituraTemperatura, LoteArmazenado, Posicao,
)
from apps.polpa.services import ArmazenagemService, CatalogoService, FrioService
from apps.produtos.models import UnidadeMedida, UnidadeMedidaFilial

T = FichaProduto.Tipo


class FrioBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Polpas Camara LTDA', nome_fantasia='Camara',
            cnpj='53345678000191', segmento='polpa_frutas',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='53345678000272',
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
            email='chefe@camara.local', nome='Chefe', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        ficha = CatalogoService.salvar(self.filial, {
            'tipo': T.POLPA, 'descricao': 'Polpa de manga 100 g', 'codigo': 'PM',
            'unidade_medida': self.unidade, 'validade_dias': 180,
            'peso_liquido': Decimal('0.100'), 'congelado': True,
            'temperatura_maxima': Decimal('-18'),
        })
        self.produto = ficha.produto
        self.camara = Camara.objects.create(
            filial=self.filial, nome='Camara 1',
            temperatura_min=Decimal('-25'), temperatura_max=Decimal('-18'),
            capacidade_kg=Decimal('1000'),
        )

    def _posicao(self, **campos):
        padrao = {
            'filial': self.filial, 'camara': self.camara,
            'corredor': 'A', 'rua': '3', 'prateleira': 'B', 'posicao': '02',
            'capacidade_kg': Decimal('500'),
        }
        padrao.update(campos)
        return Posicao.objects.create(**padrao)

    def _lote(self, quantidade=Decimal('1000'), numero='L-1', validade_dias=180):
        return LoteProduto.objects.create(
            filial=self.filial, produto=self.produto, numero_lote=numero,
            data_fabricacao=timezone.localdate(),
            data_validade=timezone.localdate() + timedelta(days=validade_dias),
            quantidade_inicial=quantidade, quantidade_atual=quantidade,
            status=LoteProduto.Status.ATIVO,
        )


class PosicaoTests(FrioBase):
    """O endereço estruturado."""

    def test_o_codigo_junta_os_niveis_que_existem(self):
        posicao = self._posicao()

        self.assertEqual(posicao.codigo, 'A-3-B-02')

    def test_camara_que_so_tem_rua_nao_vira_traco_solto(self):
        """
        Os níveis vazios somem, e o que sobra é o que a pessoa fala em voz
        alta — "rua 3", e não "—-3—-".
        """
        posicao = self._posicao(corredor='', prateleira='', posicao='')

        self.assertEqual(posicao.codigo, '3')

    def test_posicao_repetida_e_recusada(self):
        from django.db import IntegrityError

        self._posicao()
        with self.assertRaises(IntegrityError):
            self._posicao()

    def test_a_posicao_sabe_se_esta_livre(self):
        """
        Quem chega com o pallet precisa saber onde colocar sem procurar de
        porta aberta.
        """
        posicao = self._posicao()
        self.assertTrue(posicao.livre)

        lote = self._lote()
        armazenado = ArmazenagemService.guardar(lote, self.camara)
        FrioService.mover(armazenado, posicao, self.usuario)

        self.assertFalse(posicao.livre)

    def test_a_ocupacao_da_posicao_sai_do_peso(self):
        posicao = self._posicao(capacidade_kg=Decimal('200'))
        lote = self._lote(quantidade=Decimal('1000'))
        armazenado = ArmazenagemService.guardar(lote, self.camara)
        FrioService.mover(armazenado, posicao, self.usuario)

        # 1.000 un × 0,100 kg = 100 kg em 200 kg de capacidade.
        self.assertEqual(posicao.peso_ocupado, Decimal('100.000'))
        self.assertEqual(posicao.ocupacao, Decimal('50.0'))

    def test_sem_capacidade_a_ocupacao_e_nula(self):
        posicao = self._posicao(capacidade_kg=None)

        self.assertIsNone(posicao.ocupacao)


class MovimentacaoTests(FrioBase):
    """Mover o lote entre posições."""

    def test_mover_troca_posicao_camara_e_endereco(self):
        outra = Camara.objects.create(
            filial=self.filial, nome='Camara 2', temperatura_max=Decimal('-18'),
        )
        destino = Posicao.objects.create(
            filial=self.filial, camara=outra, rua='7',
        )
        lote = self._lote()
        armazenado = ArmazenagemService.guardar(lote, self.camara, {'endereco': 'A-1'})

        FrioService.mover(armazenado, destino, self.usuario, 'Reorganizacao')

        armazenado.refresh_from_db()
        self.assertEqual(armazenado.posicao, destino)
        self.assertEqual(armazenado.camara, outra)
        self.assertEqual(armazenado.endereco, '7')
        self.assertIn('Reorganizacao', armazenado.observacao)

    def test_nao_move_para_posicao_inativa(self):
        posicao = self._posicao(ativo=False)
        armazenado = ArmazenagemService.guardar(self._lote(), self.camara)

        with self.assertRaises(DomainError):
            FrioService.mover(armazenado, posicao, self.usuario)

    def test_o_onde_prefere_a_posicao_ao_texto_livre(self):
        posicao = self._posicao()
        armazenado = ArmazenagemService.guardar(
            self._lote(), self.camara, {'endereco': 'escrito a mao'},
        )

        FrioService.mover(armazenado, posicao, self.usuario)

        self.assertEqual(armazenado.onde, 'A-3-B-02')

    def test_o_texto_livre_continua_valendo_sem_posicao(self):
        """
        Obrigar o cadastro de cada prateleira antes de guardar o primeiro
        lote trocaria um problema real por um pior: não registrar nada.
        """
        armazenado = ArmazenagemService.guardar(
            self._lote(), self.camara, {'endereco': 'Rua 3'},
        )

        self.assertIsNone(armazenado.posicao)
        self.assertEqual(armazenado.onde, 'Rua 3')


class TemperaturaTests(FrioBase):
    """A leitura, o desvio e o aviso."""

    def test_a_temperatura_atual_e_a_ultima_leitura(self):
        FrioService.registrar_leitura(self.camara, Decimal('-20'), self.usuario)
        recente = FrioService.registrar_leitura(
            self.camara, Decimal('-21'), self.usuario,
        )

        self.assertEqual(FrioService.temperatura_atual(self.camara), recente)

    def test_duas_leituras_na_mesma_hora_a_ultima_gravada_vence(self):
        """
        O empate NÃO é hipótese de laboratório: a hora é digitada, e quem
        registra a leitura das 8h e corrige o valor logo depois grava duas com
        o mesmo `medida_em`. Sem desempate o banco devolve qualquer uma — e
        num painel de cadeia de frio isso é mostrar a temperatura velha como
        se fosse a de agora.

        O teste ao lado só pegou isto por acidente: no Windows o relógio de
        `timezone.now()` tem resolução de ~15 ms, e duas chamadas seguidas
        caem no mesmo instante. Num relógio mais fino ele passaria com o bug
        de pé.
        """
        as_oito = timezone.now().replace(hour=8, minute=0, second=0, microsecond=0)

        FrioService.registrar_leitura(
            self.camara, Decimal('-20'), self.usuario,
            dados={'medida_em': as_oito},
        )
        correcao = FrioService.registrar_leitura(
            self.camara, Decimal('-21'), self.usuario,
            dados={'medida_em': as_oito},
        )

        self.assertEqual(FrioService.temperatura_atual(self.camara), correcao)

    def test_a_lista_da_camara_tambem_desempata(self):
        """A ordenação padrão do modelo tinha a mesma ambiguidade."""
        as_oito = timezone.now().replace(hour=8, minute=0, second=0, microsecond=0)
        FrioService.registrar_leitura(
            self.camara, Decimal('-20'), self.usuario,
            dados={'medida_em': as_oito},
        )
        correcao = FrioService.registrar_leitura(
            self.camara, Decimal('-21'), self.usuario,
            dados={'medida_em': as_oito},
        )

        self.assertEqual(self.camara.leituras.first(), correcao)

    def test_a_leitura_dentro_da_faixa_nao_tem_desvio(self):
        leitura = FrioService.registrar_leitura(
            self.camara, Decimal('-20'), self.usuario,
        )

        self.assertIsNone(leitura.desvio)
        self.assertFalse(leitura.fora_da_faixa)

    def test_acima_do_maximo_acusa_o_desvio(self):
        leitura = FrioService.registrar_leitura(
            self.camara, Decimal('-10'), self.usuario,
        )

        self.assertTrue(leitura.fora_da_faixa)
        self.assertEqual(leitura.desvio, Decimal('8.00'))

    def test_abaixo_do_minimo_tambem_acusa(self):
        """Frio demais gasta energia e queima o produto — também é desvio."""
        leitura = FrioService.registrar_leitura(
            self.camara, Decimal('-30'), self.usuario,
        )

        self.assertTrue(leitura.fora_da_faixa)
        self.assertEqual(leitura.desvio, Decimal('-5.00'))

    def test_o_desvio_vira_notificacao_na_hora(self):
        """
        Uma câmara que subiu às 3h40 precisa ser vista às 3h45, não amanhã.
        """
        FrioService.registrar_leitura(self.camara, Decimal('-5'), self.usuario)

        aviso = Notificacao.objects.filter(filial=self.filial).first()
        self.assertIsNotNone(aviso)
        self.assertIn('fora da faixa', aviso.titulo)

    def test_dois_desvios_no_mesmo_dia_dao_dois_avisos(self):
        """
        Cada desvio é um fato próprio. Referenciar a câmara faria o segundo
        sobrescrever o primeiro pela restrição de unicidade — e o registro do
        que aconteceu de madrugada sumiria.
        """
        FrioService.registrar_leitura(self.camara, Decimal('-5'), self.usuario)
        FrioService.registrar_leitura(self.camara, Decimal('-2'), self.usuario)

        self.assertEqual(Notificacao.objects.filter(filial=self.filial).count(), 2)

    def test_leitura_dentro_da_faixa_nao_avisa(self):
        FrioService.registrar_leitura(self.camara, Decimal('-20'), self.usuario)

        self.assertEqual(Notificacao.objects.filter(filial=self.filial).count(), 0)

    def test_camara_sem_faixa_nao_inventa_desvio(self):
        sem_faixa = Camara.objects.create(filial=self.filial, nome='Sem faixa')

        leitura = FrioService.registrar_leitura(
            sem_faixa, Decimal('12'), self.usuario,
        )

        self.assertIsNone(leitura.desvio)


class AlertaTests(FrioBase):
    """Os quatro alertas."""

    def test_camara_nunca_medida_entra_no_alerta(self):
        """
        Sem leitura não é "tudo bem": é câmara que ninguém mede, e a tela
        precisa dizer isso com a mesma clareza de um desvio.
        """
        alertas = FrioService.alertas(self.filial)

        self.assertEqual(len(alertas['temperatura']), 1)
        self.assertTrue(alertas['temperatura'][0]['sem_leitura'])

    def test_camara_dentro_da_faixa_sai_do_alerta(self):
        FrioService.registrar_leitura(self.camara, Decimal('-20'), self.usuario)

        self.assertEqual(FrioService.alertas(self.filial)['temperatura'], [])

    def test_vencimento_proximo_entra_no_alerta(self):
        self._lote(validade_dias=5, numero='L-VENCE')

        alertas = FrioService.alertas(self.filial)

        self.assertEqual(len(alertas['vencimento']), 1)
        self.assertEqual(alertas['vencimento'][0]['situacao'], 'vencendo')

    def test_capacidade_estourada_entra_no_alerta(self):
        # 20.000 un × 0,100 kg = 2.000 kg numa câmara de 1.000 kg.
        lote = self._lote(quantidade=Decimal('20000'), numero='L-CHEIO')
        ArmazenagemService.guardar(lote, self.camara)

        alertas = FrioService.alertas(self.filial)

        self.assertEqual(len(alertas['capacidade']), 1)
        self.assertEqual(alertas['capacidade'][0]['ocupacao'], Decimal('200.0'))

    def test_camara_sem_capacidade_nao_entra_no_alerta(self):
        """
        Não dá para afirmar que estourou o que ninguém mediu — e alerta
        baseado em palpite é o que faz a fábrica desligar os alertas.
        """
        sem_capacidade = Camara.objects.create(
            filial=self.filial, nome='Sem capacidade',
        )
        lote = self._lote(quantidade=Decimal('99999'), numero='L-X')
        ArmazenagemService.guardar(lote, sem_capacidade)

        self.assertEqual(FrioService.alertas(self.filial)['capacidade'], [])

    def test_lote_bloqueado_com_saldo_entra_no_alerta(self):
        lote = self._lote(numero='L-BLOQ')
        ArmazenagemService.bloquear(lote, 'Vencido, aguardando descarte')

        alertas = FrioService.alertas(self.filial)

        self.assertEqual(alertas['bloqueados'].count(), 1)

    def test_lote_bloqueado_sem_saldo_sai_do_alerta(self):
        """Zerado não ocupa espaço — e o alerta é sobre espaço parado."""
        lote = self._lote(numero='L-ZERO')
        ArmazenagemService.bloquear(lote, 'Descartado por vencimento')
        lote.quantidade_atual = Decimal('0')
        lote.save(update_fields=['quantidade_atual'])

        self.assertEqual(FrioService.alertas(self.filial)['bloqueados'].count(), 0)


class TelasFrioTests(FrioBase):
    """As telas."""

    def test_as_telas_abrem(self):
        for rota, args in (
            ('polpa:temperatura', []),
            ('polpa:alertas-frio', []),
            ('polpa:camara-mapa', [self.camara.pk]),
        ):
            with self.subTest(rota=rota):
                resposta = self.client.get(reverse(rota, args=args))
                self.assertEqual(resposta.status_code, 200)

    def test_as_rotas_do_menu_nao_caem_no_placeholder(self):
        from django.urls import resolve

        from apps.polpa.views import ItemView

        for item in ('temperatura', 'alertas'):
            with self.subTest(item=item):
                achado = resolve(reverse('polpa:item', args=['frio', item]))
                self.assertIsNot(
                    getattr(achado.func, 'view_class', None), ItemView,
                )

    def test_registrar_leitura_pela_tela(self):
        self.client.post(reverse('polpa:temperatura'), {
            'camara': self.camara.pk, 'temperatura': '-19.5',
            'medida_em': '', 'observacao': 'Turno da manha',
        })

        leitura = LeituraTemperatura.objects.for_filial(self.filial).first()
        self.assertIsNotNone(leitura)
        self.assertEqual(leitura.temperatura, Decimal('-19.50'))
        self.assertEqual(leitura.medido_por, self.usuario)

    def test_criar_posicao_pela_tela(self):
        self.client.post(reverse('polpa:posicao-create', args=[self.camara.pk]), {
            'camara': self.camara.pk, 'corredor': 'B', 'rua': '1',
            'prateleira': '', 'posicao': '', 'capacidade_kg': '300', 'ativo': 'on',
        })

        posicao = Posicao.objects.for_filial(self.filial).first()
        self.assertIsNotNone(posicao)
        self.assertEqual(posicao.codigo, 'B-1')

    def test_posicao_sem_nenhum_nivel_e_recusada(self):
        """
        Seria um endereço que aponta para a câmara inteira — e a câmara já é
        o endereço de quem não mapeou nada.
        """
        self.client.post(reverse('polpa:posicao-create', args=[self.camara.pk]), {
            'camara': self.camara.pk, 'corredor': '', 'rua': '',
            'prateleira': '', 'posicao': '', 'ativo': 'on',
        })

        self.assertEqual(Posicao.objects.count(), 0)

    def test_mover_lote_pela_tela(self):
        posicao = self._posicao()
        armazenado = ArmazenagemService.guardar(self._lote(), self.camara)

        self.client.post(reverse('polpa:lote-mover', args=[armazenado.pk]), {
            'posicao': posicao.pk, 'motivo': 'Liberando a rua 1',
        })

        armazenado.refresh_from_db()
        self.assertEqual(armazenado.posicao, posicao)

    def test_o_mapa_mostra_o_que_esta_livre(self):
        self._posicao()

        resposta = self.client.get(reverse('polpa:camara-mapa', args=[self.camara.pk]))

        self.assertContains(resposta, 'A-3-B-02')
        self.assertContains(resposta, 'livre')

    def test_a_tela_de_alertas_lista_o_que_resolver(self):
        self._lote(validade_dias=3, numero='L-URGENTE')

        resposta = self.client.get(reverse('polpa:alertas-frio'))

        self.assertContains(resposta, 'L-URGENTE')
        self.assertContains(resposta, 'Temperatura fora do padrão')
        self.assertContains(resposta, 'Câmara acima da capacidade')
