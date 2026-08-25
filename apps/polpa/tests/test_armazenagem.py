"""
O estoque de produto acabado: onde o lote está e quando ele vence.

O QUE ESTES TESTES CERCAM:

  · A ENTRADA JÁ ACONTECIA (o encerramento da ordem cria o lote e dá entrada
    no estoque). O que faltava era ONDE — e é a câmara escolhida no
    encerramento que passa a responder isso;

  · SEM CÂMARA ESCOLHIDA NÃO SE INVENTA UMA. Jogar o lote na primeira câmara
    ativa daria um endereço errado com cara de certo, e o erro só apareceria
    na separação, com o cliente esperando;

  · FEFO É A ORDEM DA LISTA. O ERP consome nessa ordem; a tela precisa
    mostrar na mesma, senão a pessoa vê uma coisa e o sistema faz outra;

  · O VENCIDO CONTINUA NA LISTA. Sumir com ele esconderia o que precisa de
    decisão — e lote vencido invisível é lote vencido que sai na carga;

  · O PESO É CALCULADO, não gravado: quantidade × peso do produto. Um
    terceiro número poderia discordar dos dois que o produzem.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import DomainError
from apps.estoque.models import Estoque, LoteProduto
from apps.polpa.models import Camara, FichaProduto, LoteArmazenado, OrdemPolpa
from apps.polpa.services import (
    ArmazenagemService, CatalogoService, OrdemPolpaService, ReceitaService,
)
from apps.producao.models import ItemFichaTecnica
from apps.produtos.models import UnidadeMedida, UnidadeMedidaFilial

T = FichaProduto.Tipo
S = OrdemPolpa.Situacao


class ArmazenagemBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Polpas Frio LTDA', nome_fantasia='Frio',
            cnpj='43345678000191', segmento='polpa_frutas',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='43345678000272',
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
            email='chefe@frio.local', nome='Chefe', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        self.acabado = self._acabado()
        self.manga = self._insumo()
        self.camara = Camara.objects.create(
            filial=self.filial, nome='Camara 1', tipo=Camara.Tipo.CONGELADOS,
            temperatura_min=Decimal('-25'), temperatura_max=Decimal('-18'),
            capacidade_kg=Decimal('10000'),
        )

    def _acabado(self):
        ficha = CatalogoService.salvar(self.filial, {
            'tipo': T.POLPA, 'descricao': 'Polpa de manga 100 g', 'codigo': 'PM100',
            'unidade_medida': self.unidade, 'validade_dias': 180,
            'peso_liquido': Decimal('0.100'), 'congelado': True,
            'temperatura_maxima': Decimal('-18'),
        })
        return ficha.produto

    def _insumo(self):
        ficha = CatalogoService.salvar(self.filial, {
            'tipo': T.FRUTA, 'descricao': 'Manga in natura', 'codigo': 'MANGA',
            'unidade_medida': self.unidade, 'preco_custo': Decimal('1.5'),
        })
        return ficha.produto

    def _lote(self, quantidade=Decimal('1000'), validade_dias=180, numero='L-1'):
        return LoteProduto.objects.create(
            filial=self.filial, produto=self.acabado, numero_lote=numero,
            data_fabricacao=timezone.localdate(),
            data_validade=(
                timezone.localdate() + timedelta(days=validade_dias)
                if validade_dias is not None else None
            ),
            quantidade_inicial=quantidade, quantidade_atual=quantidade,
            status=LoteProduto.Status.ATIVO,
        )

    def _ordem_produzida(self, camara=None):
        receita = ReceitaService.criar(self.filial, self.acabado, {
            'descricao': 'Polpa de manga', 'versao': '1.0',
            'quantidade_produzida': Decimal('1000'),
            'rendimento_esperado': Decimal('60'),
        })
        ItemFichaTecnica.objects.create(
            ficha=receita.ficha, materia_prima=self.manga,
            quantidade=Decimal('100'), perda_prevista=Decimal('0'),
        )
        from apps.polpa.models import EtapaReceita

        EtapaReceita.objects.create(receita=receita, ordem=1, nome='Despolpa')
        ReceitaService.ativar(receita)

        op = OrdemPolpaService.criar(
            self.filial, receita,
            {'quantidade_planejada': Decimal('1000')}, self.usuario,
        )
        estoque, _ = Estoque.objects.get_or_create(
            produto=self.manga, filial=self.filial,
        )
        estoque.quantidade_atual = Decimal('9999')
        estoque.quantidade_reservada = Decimal('0')
        estoque.atualizar_disponivel()
        estoque.save()
        LoteProduto.objects.create(
            filial=self.filial, produto=self.manga, numero_lote='MP-1',
            quantidade_inicial=Decimal('9999'), quantidade_atual=Decimal('9999'),
            data_validade=timezone.localdate() + timedelta(days=90),
            status=LoteProduto.Status.ATIVO,
        )
        OrdemPolpaService.mover(op, S.LIBERADA, self.usuario)
        OrdemPolpaService.mover(op, S.EM_PRODUCAO, self.usuario)
        OrdemPolpaService.concluir(
            op, self.usuario, Decimal('1000'), camara=camara,
            armazenagem={'endereco': 'Rua 3', 'temperatura_entrada': Decimal('-20')},
        )
        op.refresh_from_db()
        return op


class EntradaAutomaticaTests(ArmazenagemBase):
    """Encerrar a ordem põe o lote na câmara."""

    def test_o_lote_entra_no_estoque_com_lote_fabricacao_e_validade(self):
        op = self._ordem_produzida(camara=self.camara)

        lote = op.lote
        self.assertIsNotNone(lote)
        self.assertEqual(lote.quantidade_inicial, Decimal('1000'))
        self.assertEqual(lote.data_fabricacao, timezone.localdate())
        self.assertEqual(
            lote.data_validade, timezone.localdate() + timedelta(days=180),
        )

    def test_o_lote_fica_guardado_na_camara_escolhida(self):
        op = self._ordem_produzida(camara=self.camara)

        armazenado = op.lote.armazenamento_polpa
        self.assertEqual(armazenado.camara, self.camara)
        self.assertEqual(armazenado.endereco, 'Rua 3')
        self.assertEqual(armazenado.temperatura_entrada, Decimal('-20'))

    def test_sem_camara_escolhida_o_lote_fica_sem_endereco(self):
        """
        Jogá-lo na primeira câmara ativa daria um endereço errado com cara de
        certo — e o erro só apareceria na separação.
        """
        op = self._ordem_produzida(camara=None)

        self.assertFalse(
            LoteArmazenado.objects.filter(lote=op.lote).exists()
        )

    def test_o_sem_endereco_aparece_no_resumo(self):
        self._ordem_produzida(camara=None)

        resumo = ArmazenagemService.resumo(self.filial)

        self.assertEqual(resumo['sem_endereco'], 1)


class GuardarTests(ArmazenagemBase):
    """Pôr e mover o lote."""

    def test_guardar_duas_vezes_move_e_nao_duplica(self):
        """
        Um lote em duas câmaras ao mesmo tempo é a informação que faz a
        conferência não fechar.
        """
        lote = self._lote()
        outra = Camara.objects.create(
            filial=self.filial, nome='Camara 2', temperatura_max=Decimal('-18'),
        )

        ArmazenagemService.guardar(lote, self.camara, {'endereco': 'A1'})
        ArmazenagemService.guardar(lote, outra, {'endereco': 'B2'})

        self.assertEqual(LoteArmazenado.objects.filter(lote=lote).count(), 1)
        armazenado = LoteArmazenado.objects.get(lote=lote)
        self.assertEqual(armazenado.camara, outra)
        self.assertEqual(armazenado.endereco, 'B2')

    def test_camara_de_outra_filial_e_recusada(self):
        outra_filial = Filial.objects.create(
            empresa=self.empresa, razao_social='Filial 2', cnpj='43345678000353',
        )
        camara_alheia = Camara.objects.create(
            filial=outra_filial, nome='Camara alheia',
        )
        lote = self._lote()

        with self.assertRaises(DomainError):
            ArmazenagemService.guardar(lote, camara_alheia)

    def test_o_peso_e_calculado_pela_quantidade(self):
        """
        Peso é quantidade × peso do produto. Um terceiro número gravado
        poderia discordar dos dois que o produzem.
        """
        lote = self._lote(quantidade=Decimal('1000'))
        armazenado = ArmazenagemService.guardar(lote, self.camara)

        # 1.000 un × 0,100 kg
        self.assertEqual(armazenado.peso, Decimal('100.000'))

    def test_produto_sem_peso_nao_inventa_zero(self):
        self.acabado.peso_liquido = None
        self.acabado.save(update_fields=['peso_liquido'])
        lote = self._lote()

        armazenado = ArmazenagemService.guardar(lote, self.camara)

        self.assertIsNone(armazenado.peso)

    def test_camara_que_nao_alcanca_a_temperatura_e_acusada(self):
        """
        A resposta já existia nos dois cadastros — só faltava cruzá-los. E
        ninguém faz essa pergunta até o produto chegar mole no cliente.
        """
        quente = Camara.objects.create(
            filial=self.filial, nome='Antecamara',
            tipo=Camara.Tipo.ANTECAMARA, temperatura_max=Decimal('5'),
        )
        lote = self._lote()

        armazenado = ArmazenagemService.guardar(lote, quente)

        self.assertTrue(armazenado.fora_da_faixa)

    def test_sem_faixa_cadastrada_nao_afirma_nada(self):
        """
        "Não sei" é resposta melhor do que um "pode" inventado.
        """
        sem_faixa = Camara.objects.create(filial=self.filial, nome='Sem faixa')

        self.assertIsNone(sem_faixa.cabe(self.acabado))


class EstoqueFrioTests(ArmazenagemBase):
    """A lista, a ordem dela e o que ela conta."""

    def test_a_ordem_e_fefo(self):
        """O ERP consome nessa ordem; a tela precisa mostrar na mesma."""
        self._lote(validade_dias=90, numero='L-90')
        self._lote(validade_dias=10, numero='L-10')
        self._lote(validade_dias=200, numero='L-200')

        linhas = ArmazenagemService.estoque(self.filial)

        self.assertEqual(
            [l['lote'].numero_lote for l in linhas],
            ['L-10', 'L-90', 'L-200'],
        )

    def test_lote_sem_validade_vai_para_o_fim(self):
        """
        Não é "vence nunca", é "ninguém informou" — misturá-lo com os novos
        faria a ausência parecer folga.
        """
        self._lote(validade_dias=None, numero='L-SEM')
        self._lote(validade_dias=200, numero='L-200')

        linhas = ArmazenagemService.estoque(self.filial)

        self.assertEqual(linhas[-1]['lote'].numero_lote, 'L-SEM')

    def test_o_vencido_continua_na_lista(self):
        """Lote vencido invisível é lote vencido que sai na carga."""
        self._lote(validade_dias=-5, numero='L-VENC')

        linhas = ArmazenagemService.estoque(self.filial)

        self.assertEqual(len(linhas), 1)
        self.assertEqual(linhas[0]['situacao'], 'vencido')

    def test_o_resumo_conta_o_que_pede_decisao(self):
        self._lote(validade_dias=-1, numero='L-V')
        self._lote(validade_dias=10, numero='L-A')
        self._lote(validade_dias=300, numero='L-OK')

        resumo = ArmazenagemService.resumo(self.filial)

        self.assertEqual(resumo['vencidos'], 1)
        self.assertEqual(resumo['vencendo'], 1)
        self.assertEqual(resumo['lotes'], 3)

    def test_lote_esgotado_nao_aparece(self):
        lote = self._lote(quantidade=Decimal('0'))
        lote.quantidade_atual = Decimal('0')
        lote.save(update_fields=['quantidade_atual'])

        self.assertEqual(ArmazenagemService.estoque(self.filial), [])

    def test_so_produto_acabado_entra_na_lista(self):
        """
        Matéria-prima tem lote e validade também — mas o estoque de produto
        ACABADO é outra pergunta, e misturar os dois faria a lista da
        expedição mostrar caixa de manga in natura.
        """
        LoteProduto.objects.create(
            filial=self.filial, produto=self.manga, numero_lote='MP-X',
            quantidade_inicial=Decimal('500'), quantidade_atual=Decimal('500'),
            data_validade=timezone.localdate() + timedelta(days=30),
        )
        self._lote(numero='PA-1')

        linhas = ArmazenagemService.estoque(self.filial)

        self.assertEqual([l['lote'].numero_lote for l in linhas], ['PA-1'])

    def test_a_ocupacao_da_camara_sai_do_peso(self):
        lote = self._lote(quantidade=Decimal('10000'))
        ArmazenagemService.guardar(lote, self.camara)

        por_camara = ArmazenagemService.por_camara(self.filial)

        linha = por_camara[0]
        self.assertEqual(linha['peso'], Decimal('1000.000'))
        self.assertEqual(linha['ocupacao'], Decimal('10.0'))


class BloqueioTests(ArmazenagemBase):
    """Tirar o lote do jogo sem apagá-lo."""

    def test_bloquear_exige_motivo(self):
        lote = self._lote()

        with self.assertRaises(DomainError):
            ArmazenagemService.bloquear(lote, '')

    def test_bloquear_mantem_o_lote_e_o_saldo(self):
        """Apagar seria perder o rastro justamente do lote que deu problema."""
        lote = self._lote()

        ArmazenagemService.bloquear(lote, 'Vencido, aguardando descarte')

        lote.refresh_from_db()
        self.assertEqual(lote.status, LoteProduto.Status.BLOQUEADO)
        self.assertEqual(lote.quantidade_atual, Decimal('1000'))
        self.assertIn('Vencido', lote.motivo_bloqueio)


class TelasFrioTests(ArmazenagemBase):
    """As telas da cadeia de frio."""

    def test_as_telas_abrem(self):
        for rota in ('polpa:estoque-frio', 'polpa:camara-list', 'polpa:validade'):
            with self.subTest(rota=rota):
                self.assertEqual(self.client.get(reverse(rota)).status_code, 200)

    def test_as_rotas_do_menu_nao_caem_no_placeholder(self):
        from django.urls import resolve

        from apps.polpa.views import ItemView

        for grupo, item in (
            ('frio', 'camaras'), ('frio', 'estoque-frio'),
            ('indicadores', 'validade'),
        ):
            with self.subTest(item=item):
                achado = resolve(reverse('polpa:item', args=[grupo, item]))
                self.assertIsNot(
                    getattr(achado.func, 'view_class', None), ItemView,
                )

    def test_a_lista_mostra_lote_validade_e_endereco(self):
        lote = self._lote(numero='PA-77')
        ArmazenagemService.guardar(lote, self.camara, {'endereco': 'Rua 3'})

        resposta = self.client.get(reverse('polpa:estoque-frio'))

        self.assertContains(resposta, 'PA-77')
        self.assertContains(resposta, 'Camara 1')
        self.assertContains(resposta, 'Rua 3')

    def test_guardar_pela_tela(self):
        lote = self._lote()

        self.client.post(reverse('polpa:lote-guardar', args=[lote.pk]), {
            'camara': self.camara.pk, 'endereco': 'B2',
            'temperatura_entrada': '-19',
        })

        armazenado = LoteArmazenado.objects.get(lote=lote)
        self.assertEqual(armazenado.endereco, 'B2')
        self.assertEqual(armazenado.temperatura_entrada, Decimal('-19'))

    def test_bloquear_pela_tela(self):
        lote = self._lote(validade_dias=-2)

        self.client.post(reverse('polpa:lote-bloquear', args=[lote.pk]), {
            'motivo': 'Vencido em camara',
        })

        lote.refresh_from_db()
        self.assertEqual(lote.status, LoteProduto.Status.BLOQUEADO)

    def test_cadastrar_camara_pela_tela(self):
        resposta = self.client.post(reverse('polpa:camara-create'), {
            'nome': 'Camara 3', 'tipo': Camara.Tipo.CONGELADOS,
            'temperatura_min': '-25', 'temperatura_max': '-18',
            'capacidade_kg': '5000', 'ativo': 'on',
        })

        self.assertEqual(resposta.status_code, 302)
        self.assertTrue(
            Camara.objects.for_filial(self.filial).filter(nome='Camara 3').exists()
        )

    def test_faixa_invertida_e_recusada(self):
        """
        Em congelados os números são negativos, e é fácil inverter: -18 a -25
        parece certo e está trocado.
        """
        resposta = self.client.post(reverse('polpa:camara-create'), {
            'nome': 'Camara torta', 'tipo': Camara.Tipo.CONGELADOS,
            'temperatura_min': '-18', 'temperatura_max': '-25',
        })

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'acima da maxima')
