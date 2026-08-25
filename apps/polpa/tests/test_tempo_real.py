"""
O painel de chão de fábrica: o dia, agora, contra a meta.

O QUE ESTES TESTES CERCAM:

  · O RITMO IMPORTA TANTO QUANTO O TOTAL. Às 10h, 3.000 kg de uma meta de
    10.000 podem ser ótimo ou péssimo — sem o esperado ATÉ AGORA, o número
    só vira cobrança às 17h, quando não dá mais para reagir;

  · SEM META NÃO SE INVENTA UMA. Assumir zero faria qualquer produção
    parecer infinita; assumir um número faria a fábrica ser cobrada por uma
    meta que ninguém combinou;

  · DUAS METAS, UMA TABELA. A padrão vale todo dia sem meta própria; a do
    dia substitui só naquele dia. E UMA POR DIA: duas dariam dois
    atingimentos para a mesma produção, e a fábrica escolheria o que lhe
    convém;

  · O DIA É O DO TÉRMINO. Ordem aberta ontem e fechada hoje produziu hoje —
    e é hoje que ela conta para a meta.
"""
from datetime import date, time, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import Estoque, LoteProduto
from apps.polpa.models import (
    Etapa, EtapaReceita, FichaProduto, MetaProducao, OrdemPolpa,
)
from apps.polpa.services import (
    CatalogoService, OrdemPolpaService, ProcessoService, ReceitaService,
    TempoRealService,
)
from apps.producao.models import ItemFichaTecnica
from apps.produtos.models import UnidadeMedida, UnidadeMedidaFilial

T = FichaProduto.Tipo
S = OrdemPolpa.Situacao


class TempoRealBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Polpas Hoje LTDA', nome_fantasia='Hoje',
            cnpj='83345678000191', segmento='polpa_frutas',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='83345678000272',
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
            email='chefe@hoje.local', nome='Chefe', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        self.acabado = self._item(
            T.POLPA, 'Polpa de manga 1 kg', 'PM1',
            peso_liquido=Decimal('1'), validade_dias=180,
        )
        self.manga = self._item(T.FRUTA, 'Manga in natura', 'MANGA')
        self.receita = self._receita()

    def _item(self, tipo, descricao, codigo, **extras):
        dados = {
            'tipo': tipo, 'descricao': descricao, 'codigo': codigo,
            'unidade_medida': self.unidade,
        }
        dados.update(extras)
        return CatalogoService.salvar(self.filial, dados).produto

    def _receita(self):
        receita = ReceitaService.criar(self.filial, self.acabado, {
            'descricao': 'Polpa de manga', 'versao': '1.0',
            'quantidade_produzida': Decimal('1000'),
            'rendimento_esperado': Decimal('60'),
        })
        ItemFichaTecnica.objects.create(
            ficha=receita.ficha, materia_prima=self.manga,
            quantidade=Decimal('100'), perda_prevista=Decimal('0'),
        )
        EtapaReceita.objects.create(
            receita=receita, ordem=1, nome='Despolpa', etapa=Etapa.DESPOLPAMENTO,
        )
        ReceitaService.ativar(receita)
        return receita

    def _estoque(self):
        estoque, _ = Estoque.objects.get_or_create(
            produto=self.manga, filial=self.filial,
        )
        numero = f'MP-{LoteProduto.objects.count() + 1}'
        estoque.quantidade_atual = Decimal('999999')
        estoque.quantidade_reservada = Decimal('0')
        estoque.atualizar_disponivel()
        estoque.save()
        LoteProduto.objects.create(
            filial=self.filial, produto=self.manga, numero_lote=numero,
            quantidade_inicial=Decimal('999999'), quantidade_atual=Decimal('999999'),
            data_validade=timezone.localdate() + timedelta(days=60),
            status=LoteProduto.Status.ATIVO,
        )

    def _ordem(self, planejada=Decimal('1000')):
        self._estoque()
        return OrdemPolpaService.criar(
            self.filial, self.receita,
            {'quantidade_planejada': planejada}, self.usuario,
        )

    def _produzir(self, planejada=Decimal('1000'), produzida=Decimal('1000'),
                  apontar=None):
        op = self._ordem(planejada)
        OrdemPolpaService.mover(op, S.LIBERADA, self.usuario)
        OrdemPolpaService.mover(op, S.EM_PRODUCAO, self.usuario)
        if apontar:
            apontar(op)
        OrdemPolpaService.concluir(op, self.usuario, produzida)
        op.refresh_from_db()
        return op


class MetaTests(TempoRealBase):
    """A meta padrão e a do dia."""

    def test_a_meta_do_dia_tem_precedencia_sobre_a_padrao(self):
        MetaProducao.objects.create(filial=self.filial, meta_kg=Decimal('10000'))
        MetaProducao.objects.create(
            filial=self.filial, data=timezone.localdate(), meta_kg=Decimal('4000'),
            observacao='Manutencao',
        )

        meta = MetaProducao.do_dia(self.filial, timezone.localdate())

        self.assertEqual(meta.meta_kg, Decimal('4000.000'))
        self.assertFalse(meta.e_padrao)

    def test_sem_meta_do_dia_vale_a_padrao(self):
        MetaProducao.objects.create(filial=self.filial, meta_kg=Decimal('10000'))

        meta = MetaProducao.do_dia(self.filial, timezone.localdate())

        self.assertTrue(meta.e_padrao)

    def test_sem_meta_nenhuma_nao_se_inventa_uma(self):
        """
        Assumir zero faria qualquer produção parecer infinita; assumir um
        número faria a fábrica ser cobrada por meta que ninguém combinou.
        """
        self.assertIsNone(MetaProducao.do_dia(self.filial, timezone.localdate()))

    def test_duas_metas_para_o_mesmo_dia_sao_recusadas(self):
        from django.db import IntegrityError

        hoje = timezone.localdate()
        MetaProducao.objects.create(filial=self.filial, data=hoje, meta_kg=Decimal('1'))

        with self.assertRaises(IntegrityError):
            MetaProducao.objects.create(
                filial=self.filial, data=hoje, meta_kg=Decimal('2'),
            )

    def test_duas_metas_padrao_sao_recusadas(self):
        from django.db import IntegrityError

        MetaProducao.objects.create(filial=self.filial, meta_kg=Decimal('1'))

        with self.assertRaises(IntegrityError):
            MetaProducao.objects.create(filial=self.filial, meta_kg=Decimal('2'))


class PainelTests(TempoRealBase):
    """O dia, agora."""

    def test_o_exemplo_do_roteiro(self):
        """Meta 10.000 kg, produzido 8.500 kg, atingimento 85%."""
        MetaProducao.objects.create(filial=self.filial, meta_kg=Decimal('10000'))
        self._produzir(planejada=Decimal('8500'), produzida=Decimal('8500'))

        dados = TempoRealService.hoje(self.filial)

        self.assertEqual(dados['kg'], Decimal('8500.000'))
        self.assertEqual(dados['atingimento'], Decimal('85.0'))

    def test_as_ordens_sao_separadas_por_situacao(self):
        self._ordem()
        # A LIBERADA CONTA COMO PLANEJADA no painel: as duas ainda não
        # começaram, e para quem olha a parede a pergunta é "o que falta
        # começar hoje" — não em que estado burocrático a ordem está.
        liberada = self._ordem()
        OrdemPolpaService.mover(liberada, S.LIBERADA, self.usuario)
        em_producao = self._ordem()
        OrdemPolpaService.mover(em_producao, S.LIBERADA, self.usuario)
        OrdemPolpaService.mover(em_producao, S.EM_PRODUCAO, self.usuario)
        parada = self._ordem()
        OrdemPolpaService.mover(parada, S.LIBERADA, self.usuario)
        OrdemPolpaService.mover(parada, S.EM_PRODUCAO, self.usuario)
        OrdemPolpaService.mover(parada, S.PAUSADA, self.usuario, {'motivo': 'Sem fruta'})
        self._produzir()

        dados = TempoRealService.hoje(self.filial)

        self.assertEqual(len(dados['planejadas']), 2)  # a planejada e a liberada
        self.assertEqual(len(dados['em_producao']), 1)
        self.assertEqual(len(dados['pausadas']), 1)
        self.assertEqual(len(dados['produzidas']), 1)

    def test_ordem_de_ontem_nao_conta_como_produzida_hoje(self):
        """
        O dia é o do TÉRMINO: é ele que marca quando a produção existiu, e é
        contra a meta daquele dia que ela é lida.
        """
        op = self._produzir()
        ontem = timezone.now() - timedelta(days=1)
        op.ordem.data_fim_real = ontem
        op.ordem.save(update_fields=['data_fim_real'])

        dados = TempoRealService.hoje(self.filial)

        self.assertEqual(len(dados['produzidas']), 0)
        self.assertEqual(dados['kg'], Decimal('0.000'))

    def test_sem_meta_o_atingimento_e_nulo(self):
        self._produzir()

        dados = TempoRealService.hoje(self.filial)

        self.assertIsNone(dados['atingimento'])
        self.assertIsNone(dados['ritmo'])

    def test_o_ritmo_compara_com_o_esperado_ate_agora(self):
        """
        É o que faz o painel servir de manhã: às 12h, metade do turno, o
        esperado é metade da meta.
        """
        MetaProducao.objects.create(filial=self.filial, meta_kg=Decimal('10000'))
        self._produzir(planejada=Decimal('6000'), produzida=Decimal('6000'))

        meio_dia = timezone.make_aware(
            timezone.datetime.combine(timezone.localdate(), time(12, 0)),
            timezone.get_current_timezone(),
        )
        dados = TempoRealService.hoje(self.filial, agora=meio_dia)

        # 07h → 17h = 10 h de turno; às 12h, 50% → 5.000 kg esperados.
        self.assertEqual(dados['ritmo']['esperado'], Decimal('5000.000'))
        self.assertTrue(dados['ritmo']['no_ritmo'])
        self.assertEqual(dados['ritmo']['diferenca'], Decimal('1000.000'))

    def test_o_atraso_no_ritmo_e_marcado(self):
        MetaProducao.objects.create(filial=self.filial, meta_kg=Decimal('10000'))
        self._produzir(planejada=Decimal('2000'), produzida=Decimal('2000'))

        meio_dia = timezone.make_aware(
            timezone.datetime.combine(timezone.localdate(), time(12, 0)),
            timezone.get_current_timezone(),
        )
        dados = TempoRealService.hoje(self.filial, agora=meio_dia)

        self.assertFalse(dados['ritmo']['no_ritmo'])

    def test_antes_do_turno_o_esperado_e_zero(self):
        """Às 6h ninguém está atrasado — o turno nem começou."""
        MetaProducao.objects.create(filial=self.filial, meta_kg=Decimal('10000'))

        cedo = timezone.make_aware(
            timezone.datetime.combine(timezone.localdate(), time(5, 0)),
            timezone.get_current_timezone(),
        )
        dados = TempoRealService.hoje(self.filial, agora=cedo)

        self.assertEqual(dados['ritmo']['esperado'], Decimal('0.000'))
        self.assertTrue(dados['ritmo']['no_ritmo'])

    def test_o_rendimento_do_dia_sai_das_ordens_fechadas(self):
        self._produzir(planejada=Decimal('1000'), produzida=Decimal('950'))

        dados = TempoRealService.hoje(self.filial)

        self.assertEqual(dados['rendimento'], Decimal('95.00'))

    def test_sem_ordem_fechada_o_rendimento_e_nulo(self):
        """Zero faria toda fábrica aparecer em colapso às 9h da manhã."""
        self._ordem()

        self.assertIsNone(TempoRealService.hoje(self.filial)['rendimento'])

    def test_as_perdas_do_dia_saem_dos_apontamentos(self):
        def despolpa(op):
            ProcessoService.apontar(
                op.etapas_processo.get(etapa=Etapa.DESPOLPAMENTO),
                {
                    'quantidade_entrada': Decimal('1000'),
                    'quantidade_saida': Decimal('700'),
                },
                self.usuario,
            )

        self._produzir(apontar=despolpa)

        perdas = TempoRealService.hoje(self.filial)['perdas']

        self.assertEqual(perdas['perda'], Decimal('300'))
        self.assertEqual(perdas['percentual'], Decimal('30.00'))
        self.assertEqual(len(perdas['maiores']), 1)

    def test_o_grafico_agrupa_por_hora_de_termino(self):
        """
        Ratear pelo tempo da ordem daria um gráfico bonito e falso:
        mostraria produção em horas em que nada saiu da linha.
        """
        self._produzir(produzida=Decimal('1000'))
        self._produzir(produzida=Decimal('500'))

        por_hora = TempoRealService.hoje(self.filial)['por_hora']

        # As duas fecharam no mesmo minuto do teste — uma barra só.
        self.assertEqual(len(por_hora), 1)
        self.assertEqual(por_hora[0]['kg'], Decimal('1500.000'))
        self.assertEqual(por_hora[0]['altura'], 100)


class TelaTempoRealTests(TempoRealBase):
    """As telas."""

    def test_as_telas_abrem(self):
        for rota in ('polpa:tempo-real', 'polpa:meta-list'):
            with self.subTest(rota=rota):
                self.assertEqual(self.client.get(reverse(rota)).status_code, 200)

    def test_as_rotas_do_menu_nao_caem_no_placeholder(self):
        from django.urls import resolve

        from apps.polpa.views import ItemView

        for item in ('hoje', 'metas'):
            with self.subTest(item=item):
                achado = resolve(reverse('polpa:item', args=['indicadores', item]))
                self.assertIsNot(
                    getattr(achado.func, 'view_class', None), ItemView,
                )

    def test_a_tela_mostra_a_meta_e_o_atingimento(self):
        MetaProducao.objects.create(filial=self.filial, meta_kg=Decimal('10000'))
        self._produzir(planejada=Decimal('8500'), produzida=Decimal('8500'))

        resposta = self.client.get(reverse('polpa:tempo-real'))

        self.assertContains(resposta, '85,0%')
        self.assertContains(resposta, 'Meta do dia')

    def test_sem_meta_a_tela_diz_onde_cadastrar(self):
        resposta = self.client.get(reverse('polpa:tempo-real'))

        self.assertContains(resposta, 'Sem meta cadastrada')

    def test_a_tela_se_atualiza_sozinha(self):
        """
        Fica num monitor sem teclado: se não recarregar sozinha, mostra o
        número da hora em que alguém abriu.
        """
        resposta = self.client.get(reverse('polpa:tempo-real'))

        self.assertContains(resposta, 'window.location.reload')

    def test_cadastrar_meta_pela_tela(self):
        self.client.post(reverse('polpa:meta-list'), {
            'data': '', 'meta_kg': '12000', 'observacao': 'Padrao de safra',
        })

        meta = MetaProducao.objects.for_filial(self.filial).first()
        self.assertIsNotNone(meta)
        self.assertTrue(meta.e_padrao)
        self.assertEqual(meta.meta_kg, Decimal('12000.000'))

    def test_meta_repetida_e_recusada_com_explicacao(self):
        """
        O banco também barra, mas o erro dele é uma página de servidor — e
        quem cadastra precisa saber que já existe uma, para editar.
        """
        MetaProducao.objects.create(filial=self.filial, meta_kg=Decimal('1000'))

        resposta = self.client.post(reverse('polpa:meta-list'), {
            'data': '', 'meta_kg': '2000',
        })

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Já existe uma meta')
        self.assertEqual(MetaProducao.objects.count(), 1)

    def test_atualizar_a_meta_padrao(self):
        meta = MetaProducao.objects.create(
            filial=self.filial, meta_kg=Decimal('9000'),
        )

        self.client.post(reverse('polpa:meta-update', args=[meta.pk]), {
            'data': '', 'meta_kg': '11000', 'observacao': '',
        })

        meta.refresh_from_db()
        self.assertEqual(meta.meta_kg, Decimal('11000.000'))
