"""
O processo produtivo: as dezoito etapas e o apontamento de cada uma.

O QUE ESTES TESTES CERCAM:

  · PLANO E FATO SÃO COISAS DIFERENTES. A receita diz o que fazer (vale
    para todo produto); o apontamento diz o que aconteceu (vale para uma
    ordem). Misturar os dois faz a receita ser reescrita a cada produção ou
    o apontamento virar campo de texto — e some a fórmula ou some a conta;

  · AS ETAPAS NASCEM COM A ORDEM. Criadas sob demanda, "não iniciada"
    ficaria indistinguível de "não existe";

  · A PERDA VEM DA DIFERENÇA entre entrada e saída, e é `None` — não zero —
    enquanto ninguém pesou. Zero é "não perdeu nada", e confundir os dois
    faz o relatório de perdas mentir para menos;

  · CAMPO VAZIO NÃO APAGA o que já estava: quem aponta a saída depois da
    entrada manda o formulário com um dos dois em branco.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import DomainError
from apps.polpa.models import (
    ApontamentoEtapa, EtapaReceita, FichaProduto, OrdemPolpa, Recurso,
)
from apps.polpa.models.processo import OPCIONAIS, PADRAO, SEQUENCIA, Etapa
from apps.polpa.services import (
    CatalogoService, OrdemPolpaService, ProcessoService, ReceitaService,
)
from apps.producao.models import ItemFichaTecnica
from apps.produtos.models import UnidadeMedida, UnidadeMedidaFilial

T = FichaProduto.Tipo
SIT = ApontamentoEtapa.Situacao


class ProcessoBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Polpas Processo LTDA', nome_fantasia='Processo',
            cnpj='33345678000191', segmento='polpa_frutas',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='33345678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='KG', descricao='Quilograma',
            tipo=UnidadeMedida.Tipo.PESO,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='chefe@processo.local', nome='Chefe', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.operador = Usuario.objects.create_user(
            email='joao@processo.local', nome='Joao', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        self.acabado = self._item(T.POLPA, 'Polpa de manga 100 g', 'PM100')
        self.manga = self._item(T.FRUTA, 'Manga in natura', 'MANGA')

    def _item(self, tipo, descricao, codigo):
        ficha = CatalogoService.salvar(self.filial, {
            'tipo': tipo, 'descricao': descricao, 'codigo': codigo,
            'unidade_medida': self.unidade, 'validade_dias': 180,
        })
        return ficha.produto

    def _receita(self, etapas=None):
        receita = ReceitaService.criar(self.filial, self.acabado, {
            'descricao': 'Polpa de manga', 'versao': '1.0',
            'quantidade_produzida': Decimal('1000'),
            'rendimento_esperado': Decimal('60'),
        })
        ItemFichaTecnica.objects.create(
            ficha=receita.ficha, materia_prima=self.manga,
            quantidade=Decimal('100'), perda_prevista=Decimal('0'),
        )
        for i, (nome, canonica) in enumerate(etapas or [('Despolpa', '')], start=1):
            EtapaReceita.objects.create(
                receita=receita, ordem=i, nome=nome, etapa=canonica,
            )
        ReceitaService.ativar(receita)
        return receita

    def _op(self, receita=None):
        return OrdemPolpaService.criar(
            self.filial, receita or self._receita(),
            {'quantidade_planejada': Decimal('1000')}, self.usuario,
        )


class MontagemTests(ProcessoBase):
    """Quais etapas cada ordem tem."""

    def test_as_etapas_nascem_com_a_ordem(self):
        """
        Sob demanda, "não iniciada" ficaria indistinguível de "não existe" —
        que é justamente o que quem acompanha a produção quer saber.
        """
        op = self._op()

        self.assertEqual(op.etapas_processo.count(), len(PADRAO))
        self.assertTrue(
            all(e.situacao == SIT.PENDENTE for e in op.etapas_processo.all())
        )

    def test_a_lista_padrao_deixa_de_fora_as_opcionais(self):
        """
        Descascamento, corte e formulação nem toda fábrica faz. Criar as
        dezoito para todo produto encheria a tela de linha que ninguém vai
        apontar — e etapa vazia por padrão faz parar de olhar a lista.
        """
        op = self._op()

        etapas = set(op.etapas_processo.values_list('etapa', flat=True))
        for opcional in OPCIONAIS:
            self.assertNotIn(opcional.value, etapas)

    def test_a_receita_decide_as_etapas_quando_declara(self):
        receita = self._receita([
            ('Recepcao', Etapa.RECEPCAO),
            ('Despolpa', Etapa.DESPOLPAMENTO),
            ('Envase', Etapa.ENVASE),
        ])

        op = self._op(receita)

        etapas = list(op.etapas_processo.values_list('etapa', flat=True))
        self.assertEqual(
            etapas,
            [Etapa.RECEPCAO, Etapa.DESPOLPAMENTO, Etapa.ENVASE],
        )

    def test_a_ordem_das_etapas_e_a_do_processo(self):
        """Sanitizar depois de despolpar não sanitiza."""
        receita = self._receita([
            ('Envase', Etapa.ENVASE),
            ('Lavagem', Etapa.LAVAGEM),
        ])

        op = self._op(receita)

        etapas = list(op.etapas_processo.values_list('etapa', flat=True))
        self.assertEqual(etapas, [Etapa.LAVAGEM, Etapa.ENVASE])

    def test_etapa_livre_da_receita_nao_vira_apontamento(self):
        """
        A receita pode ter etapa fora do vocabulário — ela continua valendo
        como instrução, mas apontar o que não está na lista comum faz o
        rendimento por etapa deixar de somar.
        """
        receita = self._receita([('Descanso do tanque', '')])

        op = self._op(receita)

        # Sem etapa canônica declarada, cai na lista padrão.
        self.assertEqual(op.etapas_processo.count(), len(PADRAO))

    def test_preparar_duas_vezes_nao_duplica(self):
        op = self._op()
        antes = op.etapas_processo.count()

        ProcessoService.preparar(op)

        self.assertEqual(op.etapas_processo.count(), antes)

    def test_as_dezoito_estao_todas_no_vocabulario(self):
        self.assertEqual(len(SEQUENCIA), 18)


class ApontamentoTests(ProcessoBase):
    """O que aconteceu em cada etapa."""

    def _etapa(self, op, qual=Etapa.DESPOLPAMENTO):
        return op.etapas_processo.get(etapa=qual)

    def test_apontar_grava_quantidades_e_conclui(self):
        op = self._op()
        etapa = self._etapa(op)

        ProcessoService.apontar(etapa, {
            'quantidade_entrada': Decimal('1000'),
            'quantidade_saida': Decimal('600'),
            'motivo_perda': 'Casca e caroco',
        }, self.usuario)

        etapa.refresh_from_db()
        self.assertEqual(etapa.situacao, SIT.CONCLUIDA)
        self.assertEqual(etapa.perda, Decimal('400'))
        self.assertEqual(etapa.perda_percentual, Decimal('40.00'))
        self.assertEqual(etapa.rendimento, Decimal('60.00'))
        self.assertIsNotNone(etapa.concluida_em)

    def test_o_operador_e_gravado(self):
        """Sem esse campo, "quem fez" vira memória."""
        op = self._op()
        etapa = self._etapa(op)

        ProcessoService.apontar(
            etapa, {'operador': self.operador}, self.usuario,
        )

        etapa.refresh_from_db()
        self.assertEqual(etapa.operador, self.operador)

    def test_sem_operador_informado_fica_quem_apontou(self):
        op = self._op()
        etapa = self._etapa(op)

        ProcessoService.apontar(etapa, {}, self.usuario)

        etapa.refresh_from_db()
        self.assertEqual(etapa.operador, self.usuario)

    def test_perda_e_nula_enquanto_ninguem_pesou(self):
        """
        Zero é "não perdeu nada" e é diferente de "ninguém mediu" —
        confundir os dois faz o relatório mentir para menos.
        """
        op = self._op()
        etapa = self._etapa(op)

        self.assertIsNone(etapa.perda)
        self.assertIsNone(etapa.perda_percentual)

    def test_saida_maior_que_entrada_nao_gera_perda_negativa(self):
        op = self._op()
        etapa = self._etapa(op)

        ProcessoService.apontar(etapa, {
            'quantidade_entrada': Decimal('100'),
            'quantidade_saida': Decimal('120'),
        }, self.usuario)

        self.assertEqual(etapa.perda, Decimal('0'))

    def test_em_andamento_marca_o_inicio_e_nao_conclui(self):
        op = self._op()
        etapa = self._etapa(op)

        ProcessoService.apontar(
            etapa, {'situacao': SIT.EM_ANDAMENTO}, self.usuario,
        )

        etapa.refresh_from_db()
        self.assertEqual(etapa.situacao, SIT.EM_ANDAMENTO)
        self.assertIsNotNone(etapa.iniciada_em)
        self.assertIsNone(etapa.concluida_em)

    def test_etapa_pode_ser_marcada_como_nao_aplicavel(self):
        """Descascamento de acerola não existe — e pular é diferente de omitir."""
        op = self._op()
        etapa = self._etapa(op, Etapa.LAVAGEM)

        ProcessoService.apontar(etapa, {'situacao': SIT.PULADA}, self.usuario)

        etapa.refresh_from_db()
        self.assertEqual(etapa.situacao, SIT.PULADA)

    def test_ordem_encerrada_nao_recebe_apontamento(self):
        op = self._op()
        OrdemPolpaService.mover(
            op, OrdemPolpa.Situacao.CANCELADA, self.usuario, {'motivo': 'Engano'},
        )
        etapa = self._etapa(op)

        with self.assertRaises(DomainError):
            ProcessoService.apontar(etapa, {}, self.usuario)

    def test_a_temperatura_e_cobrada_onde_importa(self):
        """
        Congelamento sem temperatura é o registro que a fiscalização pede e
        não encontra. Em "seleção" seria burocracia.
        """
        op = self._op()

        congelamento = self._etapa(op, Etapa.CONGELAMENTO)
        selecao = self._etapa(op, Etapa.SELECAO)

        self.assertTrue(congelamento.exige_temperatura)
        self.assertFalse(selecao.exige_temperatura)


class ResumoTests(ProcessoBase):
    """Onde a fruta se perde."""

    def test_o_rendimento_vai_da_primeira_entrada_a_ultima_saida(self):
        op = self._op()
        recepcao = op.etapas_processo.get(etapa=Etapa.RECEPCAO)
        despolpa = op.etapas_processo.get(etapa=Etapa.DESPOLPAMENTO)
        envase = op.etapas_processo.get(etapa=Etapa.ENVASE)

        ProcessoService.apontar(recepcao, {
            'quantidade_entrada': Decimal('1000'),
            'quantidade_saida': Decimal('980'),
        }, self.usuario)
        ProcessoService.apontar(despolpa, {
            'quantidade_entrada': Decimal('980'),
            'quantidade_saida': Decimal('620'),
        }, self.usuario)
        ProcessoService.apontar(envase, {
            'quantidade_entrada': Decimal('620'),
            'quantidade_saida': Decimal('600'),
        }, self.usuario)

        resumo = ProcessoService.resumo(op)

        self.assertEqual(resumo['entrada'], Decimal('1000'))
        self.assertEqual(resumo['saida'], Decimal('600'))
        self.assertEqual(resumo['rendimento'], Decimal('60.00'))
        self.assertEqual(resumo['perda_total'], Decimal('400'))

    def test_as_maiores_perdas_vem_em_ordem(self):
        """
        É a informação que faz alguém ir olhar a despolpadeira — o total
        sozinho não faz ninguém sair da cadeira.
        """
        op = self._op()
        ProcessoService.apontar(
            op.etapas_processo.get(etapa=Etapa.DESPOLPAMENTO),
            {'quantidade_entrada': Decimal('1000'), 'quantidade_saida': Decimal('620')},
            self.usuario,
        )
        ProcessoService.apontar(
            op.etapas_processo.get(etapa=Etapa.SELECAO),
            {'quantidade_entrada': Decimal('1050'), 'quantidade_saida': Decimal('1000')},
            self.usuario,
        )

        resumo = ProcessoService.resumo(op)

        maiores = resumo['maiores_perdas']
        self.assertEqual(maiores[0]['etapa'].etapa, Etapa.DESPOLPAMENTO)
        self.assertEqual(maiores[0]['perda'], Decimal('380'))

    def test_a_proxima_etapa_pendente_e_apontada(self):
        op = self._op()

        resumo = ProcessoService.resumo(op)

        self.assertIsNotNone(resumo['proxima'])
        self.assertEqual(resumo['proxima'].etapa, PADRAO[0])

    def test_pendencias_listam_o_que_falta_sem_travar(self):
        """
        A fábrica trabalha com apontamento atrasado o tempo todo. Travar o
        encerramento faria a pessoa apontar qualquer coisa para fechar.
        """
        op = self._op()

        faltando = ProcessoService.pendencias(op)

        self.assertEqual(len(faltando), len(PADRAO))


class TelasProcessoTests(ProcessoBase):
    """As telas do chão de fábrica."""

    def test_a_fila_e_o_processo_abrem(self):
        op = self._op()

        for url in (
            reverse('polpa:processo-fila'),
            reverse('polpa:processo-apontamento'),
            reverse('polpa:processo-ordem', args=[op.pk]),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_as_rotas_do_menu_nao_caem_no_placeholder(self):
        from django.urls import resolve

        from apps.polpa.views import ItemView

        for item in ('etapas', 'apontamento'):
            with self.subTest(item=item):
                achado = resolve(reverse('polpa:item', args=['producao', item]))
                self.assertIsNot(
                    getattr(achado.func, 'view_class', None), ItemView,
                )

    def test_apontar_pela_tela(self):
        op = self._op()
        etapa = op.etapas_processo.get(etapa=Etapa.DESPOLPAMENTO)
        recurso = Recurso.objects.create(
            filial=self.filial, nome='Despolpadeira 1', tipo=Recurso.Tipo.MAQUINA,
        )

        self.client.post(reverse('polpa:etapa-apontar', args=[etapa.pk]), {
            'quantidade_entrada': '1000', 'quantidade_saida': '620',
            'temperatura': '18', 'equipamento': recurso.pk,
            'operador': self.operador.pk, 'motivo_perda': 'Casca e caroco',
            'situacao': SIT.CONCLUIDA,
        })

        etapa.refresh_from_db()
        self.assertEqual(etapa.quantidade_saida, Decimal('620'))
        self.assertEqual(etapa.equipamento, recurso)
        self.assertEqual(etapa.operador, self.operador)
        self.assertEqual(etapa.perda, Decimal('380'))

    def test_campo_vazio_nao_apaga_o_que_ja_estava(self):
        """
        Quem aponta a saída depois da entrada manda o formulário com um dos
        dois em branco — e limpar o outro apagaria a medição de meia hora
        antes.
        """
        op = self._op()
        etapa = op.etapas_processo.get(etapa=Etapa.DESPOLPAMENTO)
        self.client.post(reverse('polpa:etapa-apontar', args=[etapa.pk]), {
            'quantidade_entrada': '1000', 'situacao': SIT.EM_ANDAMENTO,
        })

        self.client.post(reverse('polpa:etapa-apontar', args=[etapa.pk]), {
            'quantidade_saida': '620', 'situacao': SIT.CONCLUIDA,
        })

        etapa.refresh_from_db()
        self.assertEqual(etapa.quantidade_entrada, Decimal('1000'))
        self.assertEqual(etapa.quantidade_saida, Decimal('620'))

    def test_a_tela_da_ordem_leva_ao_processo(self):
        op = self._op()

        resposta = self.client.get(reverse('polpa:ordem-detail', args=[op.pk]))

        self.assertContains(resposta, reverse('polpa:processo-ordem', args=[op.pk]))
