"""
O túnel de congelamento: quem está dentro, há quanto tempo, e o que saiu.

O QUE ESTES TESTES CERCAM:

  · A PASSAGEM PELO TÚNEL JÁ ERA REGISTRADA. É a etapa de congelamento da
    ordem — hora de entrada, hora de saída, operador, equipamento,
    quantidade e temperatura. Um modelo próprio ao lado daria dois
    registros do mesmo evento, e no dia em que discordassem ninguém saberia
    qual mostrar à fiscalização. A tela vira a consulta de lado, e só;

  · O TEMPO ALVO VEM DA RECEITA. Bloco de 10 kg e picolé no mesmo túnel não
    levam o mesmo tempo. Sem tempo declarado a carga aparece como CADASTRO
    FALTANDO, nunca como "em dia" — inventar um alvo cobraria a fábrica por
    uma regra que ninguém combinou;

  · QUEM NÃO ENTROU NÃO PODE SAIR. Apontar a saída de uma carga que nunca
    entrou gravaria entrada e saída no mesmo instante: uma passagem de zero
    minuto, que é justamente o registro que faria o histórico parecer
    perfeito;

  · SEM MEDIDA NÃO É "SAIU CERTO". Temperatura ausente é medição que
    faltou, e a tela diz isso.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import DomainError
from apps.polpa.models import (
    ApontamentoEtapa, Camara, EtapaReceita, FichaProduto, OrdemPolpa, Recurso,
)
from apps.polpa.models.processo import Etapa
from apps.polpa.services import (
    CatalogoService, FrioService, OrdemPolpaService, ReceitaService,
    TunelService,
)
from apps.producao.models import ItemFichaTecnica
from apps.produtos.models import UnidadeMedida, UnidadeMedidaFilial

T = FichaProduto.Tipo
S = OrdemPolpa.Situacao
SIT = ApontamentoEtapa.Situacao


class TunelBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Polpas Tunel LTDA', nome_fantasia='Tunel',
            cnpj='43345678000191', segmento='polpa_frutas',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='43345678000272',
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
            email='chefe@tunel.local', nome='Chefe', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        self.acabado = self._item(T.POLPA, 'Polpa de manga 1 kg', 'PM1')
        self.manga = self._item(T.FRUTA, 'Manga in natura', 'MANGA')

    def _item(self, tipo, descricao, codigo):
        return CatalogoService.salvar(self.filial, {
            'tipo': tipo, 'descricao': descricao, 'codigo': codigo,
            'unidade_medida': self.unidade, 'validade_dias': 180,
        }).produto

    def _receita(self, minutos=180, faixa=(None, Decimal('-18'))):
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
            receita=receita, ordem=1, nome='Despolpa',
            etapa=Etapa.DESPOLPAMENTO,
        )
        EtapaReceita.objects.create(
            receita=receita, ordem=2, nome='Congelamento',
            etapa=Etapa.CONGELAMENTO, tempo_minutos=minutos,
            temperatura_min=faixa[0], temperatura_max=faixa[1],
        )
        ReceitaService.ativar(receita)
        return receita

    def _op(self, receita=None):
        op = OrdemPolpaService.criar(
            self.filial, receita or self._receita(),
            {'quantidade_planejada': Decimal('1000')}, self.usuario,
        )
        OrdemPolpaService.mover(op, S.LIBERADA, self.usuario)
        OrdemPolpaService.mover(op, S.EM_PRODUCAO, self.usuario)
        return op

    def _congelamento(self, op):
        return op.etapas_processo.get(etapa=Etapa.CONGELAMENTO)


class AlvoTests(TunelBase):
    """O tempo e a faixa vêm da receita — ou não vêm."""

    def test_o_alvo_sai_da_receita(self):
        op = self._op(self._receita(minutos=240))

        alvo = TunelService.alvo(op)

        self.assertEqual(alvo['minutos'], 240)

    def test_receita_sem_tempo_nao_inventa_alvo(self):
        """
        Um número fixo no código transformaria metade das cargas em alarme
        falso — e cobraria a fábrica por uma regra que ninguém combinou.
        """
        op = self._op(self._receita(minutos=None))

        alvo = TunelService.alvo(op)

        self.assertIsNone(alvo['minutos'])

    def test_carga_sem_alvo_nao_aparece_como_em_dia(self):
        op = self._op(self._receita(minutos=None))
        TunelService.entrar(self._congelamento(op), {}, self.usuario)

        linha = TunelService.dentro(self.filial)[0]

        self.assertTrue(linha['sem_alvo'])
        self.assertFalse(linha['no_prazo'])
        self.assertIsNone(linha['excedido'])


class DentroTests(TunelBase):
    """O relógio de cada carga."""

    def test_a_carga_entra_e_aparece_dentro(self):
        op = self._op()

        TunelService.entrar(self._congelamento(op), {
            'quantidade_entrada': Decimal('900'),
        }, self.usuario)

        dentro = TunelService.dentro(self.filial)
        self.assertEqual(len(dentro), 1)
        self.assertEqual(dentro[0]['ordem'], op)
        self.assertEqual(
            dentro[0]['etapa'].situacao, SIT.EM_ANDAMENTO,
        )

    def test_o_tempo_conta_desde_a_entrada(self):
        """
        O relógio corre no servidor, a partir da hora de entrada — nunca de
        um campo gravado, que envelheceria em silêncio.
        """
        op = self._op(self._receita(minutos=180))
        etapa = self._congelamento(op)
        TunelService.entrar(etapa, {}, self.usuario)
        etapa.iniciada_em = timezone.now() - timedelta(minutes=60)
        etapa.save(update_fields=['iniciada_em'])

        linha = TunelService.dentro(self.filial)[0]

        self.assertEqual(linha['minutos'], 60)
        self.assertEqual(linha['restante'], 120)
        self.assertTrue(linha['no_prazo'])

    def test_a_carga_que_passou_do_tempo_e_marcada(self):
        op = self._op(self._receita(minutos=180))
        etapa = self._congelamento(op)
        TunelService.entrar(etapa, {}, self.usuario)
        etapa.iniciada_em = timezone.now() - timedelta(minutes=200)
        etapa.save(update_fields=['iniciada_em'])

        linha = TunelService.dentro(self.filial)[0]

        self.assertEqual(linha['excedido'], 20)
        self.assertFalse(linha['no_prazo'])
        self.assertEqual(TunelService.resumo(self.filial)['passaram_do_tempo'], 1)

    def test_a_fila_traz_a_ordem_aberta_que_ainda_nao_congelou(self):
        op = self._op()

        esperando = TunelService.esperando(self.filial)

        self.assertEqual([l['ordem'] for l in esperando], [op])

    def test_quem_entrou_sai_da_fila(self):
        op = self._op()
        TunelService.entrar(self._congelamento(op), {}, self.usuario)

        self.assertEqual(TunelService.esperando(self.filial), [])


class MovimentoTests(TunelBase):
    """Pôr e tirar do túnel."""

    def test_quem_nao_entrou_nao_pode_sair(self):
        """
        Sem esta trava, a saída de uma carga que nunca entrou gravaria
        entrada e saída no mesmo instante: uma passagem de zero minuto, que
        faria o histórico do túnel parecer perfeito.
        """
        op = self._op()

        with self.assertRaises(DomainError):
            TunelService.sair(self._congelamento(op), {
                'temperatura': Decimal('-18'),
            }, self.usuario)

    def test_nao_se_entra_duas_vezes(self):
        op = self._op()
        etapa = self._congelamento(op)
        TunelService.entrar(etapa, {}, self.usuario)

        with self.assertRaises(DomainError):
            TunelService.entrar(etapa, {}, self.usuario)

    def test_a_saida_grava_tempo_e_temperatura(self):
        op = self._op()
        etapa = self._congelamento(op)
        TunelService.entrar(etapa, {'quantidade_entrada': Decimal('900')}, self.usuario)
        etapa.iniciada_em = timezone.now() - timedelta(minutes=190)
        etapa.save(update_fields=['iniciada_em'])

        TunelService.sair(etapa, {
            'quantidade_saida': Decimal('895'),
            'temperatura': Decimal('-19'),
        }, self.usuario)

        etapa.refresh_from_db()
        self.assertEqual(etapa.situacao, SIT.CONCLUIDA)
        self.assertEqual(etapa.duracao_minutos, 190)
        self.assertEqual(etapa.temperatura, Decimal('-19.00'))
        self.assertEqual(etapa.quantidade_saida, Decimal('895.000'))

    def test_a_entrada_nao_grava_temperatura(self):
        """
        O campo de temperatura da etapa é o que a fiscalização lê como "a
        que temperatura este lote congelou". Gravá-lo na entrada faria a
        saída sobrescrevê-lo — e o número que prova o congelamento é o da
        saída.
        """
        op = self._op()
        etapa = self._congelamento(op)

        TunelService.entrar(etapa, {'temperatura': Decimal('5')}, self.usuario)

        etapa.refresh_from_db()
        self.assertIsNone(etapa.temperatura)

    def test_a_saida_fora_da_faixa_e_marcada(self):
        op = self._op(self._receita(faixa=(None, Decimal('-18'))))
        etapa = self._congelamento(op)
        TunelService.entrar(etapa, {}, self.usuario)
        TunelService.sair(etapa, {'temperatura': Decimal('-8')}, self.usuario)

        linha = TunelService.saidas(self.filial)[0]

        self.assertTrue(linha['temperatura_fora'])
        self.assertEqual(
            TunelService.resumo(self.filial)['fora_da_faixa_hoje'], 1,
        )

    def test_sem_temperatura_medida_nao_vira_alarme(self):
        """`None` é medição que faltou, e a tela diz isso — não é desvio."""
        op = self._op()
        etapa = self._congelamento(op)
        TunelService.entrar(etapa, {}, self.usuario)
        TunelService.sair(etapa, {}, self.usuario)

        linha = TunelService.saidas(self.filial)[0]

        self.assertFalse(linha['temperatura_fora'])
        self.assertIsNone(linha['etapa'].temperatura)


class ResumoTests(TunelBase):
    """Os números do topo."""

    def test_sem_saida_nao_ha_tempo_medio(self):
        """Zero se leria como "congelou instantaneamente"."""
        self._op()

        self.assertIsNone(TunelService.resumo(self.filial)['tempo_medio'])

    def test_o_tempo_medio_sai_das_saidas_de_hoje(self):
        op = self._op()
        etapa = self._congelamento(op)
        TunelService.entrar(etapa, {}, self.usuario)
        etapa.iniciada_em = timezone.now() - timedelta(minutes=120)
        etapa.save(update_fields=['iniciada_em'])
        TunelService.sair(etapa, {'temperatura': Decimal('-19')}, self.usuario)

        resumo = TunelService.resumo(self.filial)

        self.assertEqual(resumo['saidas_hoje'], 1)
        self.assertEqual(resumo['tempo_medio'], 120)

    def test_o_tunel_aparece_com_a_ultima_temperatura(self):
        camara = Camara.objects.create(
            filial=self.filial, nome='Túnel 1', tipo=Camara.Tipo.TUNEL,
            temperatura_min=Decimal('-35'), temperatura_max=Decimal('-25'),
        )
        FrioService.registrar_leitura(camara, Decimal('-30'), self.usuario)

        tuneis = TunelService.tuneis(self.filial)

        self.assertEqual(len(tuneis), 1)
        self.assertEqual(tuneis[0]['leitura'].temperatura, Decimal('-30.00'))
        self.assertFalse(tuneis[0]['fora_da_faixa'])

    def test_camara_comum_nao_aparece_como_tunel(self):
        Camara.objects.create(
            filial=self.filial, nome='Câmara 1',
            tipo=Camara.Tipo.CONGELADOS,
        )

        self.assertEqual(TunelService.tuneis(self.filial), [])


class TelaTests(TunelBase):
    """A tela e as duas ações."""

    def test_a_tela_abre(self):
        op = self._op()
        TunelService.entrar(self._congelamento(op), {}, self.usuario)

        resposta = self.client.get(reverse('polpa:tunel'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, op.numero)

    def test_a_tela_nao_e_o_placeholder_em_construcao(self):
        resposta = self.client.get(reverse('polpa:item', args=['frio', 'tunel']))

        self.assertEqual(resposta.status_code, 200)
        self.assertNotContains(resposta, 'Tela em construção')

    def test_poe_no_tunel_pela_tela(self):
        op = self._op()
        etapa = self._congelamento(op)
        recurso = Recurso.objects.create(
            filial=self.filial, nome='Túnel A', tipo=Recurso.Tipo.MAQUINA,
        )

        self.client.post(reverse('polpa:tunel'), {
            'acao': 'entrar', 'etapa': etapa.pk,
            'quantidade_entrada': '900', 'equipamento': recurso.pk,
        })

        etapa.refresh_from_db()
        self.assertEqual(etapa.situacao, SIT.EM_ANDAMENTO)
        self.assertEqual(etapa.equipamento, recurso)
        self.assertEqual(etapa.quantidade_entrada, Decimal('900.000'))

    def test_tira_do_tunel_pela_tela_com_virgula(self):
        """A fábrica digita -18,5 — e o ponto decimal é detalhe de teclado."""
        op = self._op()
        etapa = self._congelamento(op)
        TunelService.entrar(etapa, {}, self.usuario)

        self.client.post(reverse('polpa:tunel'), {
            'acao': 'sair', 'etapa': etapa.pk,
            'quantidade_saida': '895', 'temperatura': '-18,5',
        })

        etapa.refresh_from_db()
        self.assertEqual(etapa.situacao, SIT.CONCLUIDA)
        self.assertEqual(etapa.temperatura, Decimal('-18.50'))

    def test_a_tela_recusa_saida_de_quem_nao_entrou(self):
        op = self._op()
        etapa = self._congelamento(op)

        resposta = self.client.post(reverse('polpa:tunel'), {
            'acao': 'sair', 'etapa': etapa.pk, 'temperatura': '-18',
        }, follow=True)

        etapa.refresh_from_db()
        self.assertEqual(etapa.situacao, SIT.PENDENTE)
        self.assertContains(resposta, 'registre a entrada primeiro')
