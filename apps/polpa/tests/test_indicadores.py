"""
O painel industrial: cinco perguntas, nenhum número novo.

O QUE ESTES TESTES CERCAM:

  · NENHUM NÚMERO NASCE NO PAINEL. Tudo já foi registrado por quem fez o
    trabalho — a ordem sabe o que produziu, o apontamento sabe onde a fruta
    se perdeu. Um painel que guarda os próprios totais é o primeiro lugar
    onde o sistema passa a discordar de si mesmo;

  · ONDE NÃO HÁ MEDIÇÃO, É `None` — NÃO ZERO. Zero é uma afirmação ("não
    rendeu nada", "não girou") e é diferente de "ninguém mediu". Basta um
    número obviamente errado para o painel perder a confiança de quem olha;

  · SÓ ORDEM PRODUZIDA CONTA como produção. Ordem aberta é intenção, e
    somá-la faria o painel prometer produto que não existe;

  · AS DUAS FONTES DE QUALIDADE CONVIVEM. Análise (Brix, pH) e inspeção de
    lote respondem por áreas diferentes — somá-las esconderia qual reprovou.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import Estoque, LoteProduto
from apps.polpa.models import (
    ApontamentoEtapa, Camara, Etapa, EtapaReceita, FichaProduto, OrdemPolpa,
    Recurso,
)
from apps.polpa.services import (
    ArmazenagemService, CatalogoService, IndicadoresService, OrdemPolpaService,
    PlanejamentoService, ProcessoService, ReceitaService,
)
from apps.producao.models import ItemFichaTecnica
from apps.produtos.models import UnidadeMedida, UnidadeMedidaFilial

T = FichaProduto.Tipo
S = OrdemPolpa.Situacao


class PainelBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Polpas Painel LTDA', nome_fantasia='Painel',
            cnpj='73345678000191', segmento='polpa_frutas',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='73345678000272',
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
            email='chefe@painel.local', nome='Chefe', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        self.acabado = self._item(
            T.POLPA, 'Polpa de manga 100 g', 'PM100',
            peso_liquido=Decimal('0.100'), validade_dias=180,
        )
        self.manga = self._item(
            T.FRUTA, 'Manga in natura', 'MANGA', preco_custo=Decimal('1.5'),
        )
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
            receita=receita, ordem=1, nome='Despolpa',
            etapa=Etapa.DESPOLPAMENTO,
        )
        ReceitaService.ativar(receita)
        return receita

    def _estoque_mp(self, quantidade=Decimal('99999')):
        estoque, _ = Estoque.objects.get_or_create(
            produto=self.manga, filial=self.filial,
        )
        # O número do lote é único por produto e filial — cada chamada
        # precisa do seu, senão a segunda produção do teste esbarra na
        # restrição do banco.
        numero = f'MP-{LoteProduto.objects.count() + 1}'
        estoque.quantidade_atual = quantidade
        estoque.quantidade_reservada = Decimal('0')
        estoque.custo_medio = Decimal('1.5')
        estoque.atualizar_disponivel()
        estoque.save()
        LoteProduto.objects.create(
            filial=self.filial, produto=self.manga, numero_lote=numero,
            quantidade_inicial=quantidade, quantidade_atual=quantidade,
            custo_unitario=Decimal('1.5'),
            data_validade=timezone.localdate() + timedelta(days=60),
            status=LoteProduto.Status.ATIVO,
        )
        return estoque

    def _produzir(self, planejada=Decimal('1000'), produzida=Decimal('970'),
                  apontar=None):
        """
        Uma batida completa. `apontar` recebe a OP em produção — o processo
        só aceita apontamento com a ordem aberta, que é a regra da seção 5.
        """
        self._estoque_mp()
        op = OrdemPolpaService.criar(
            self.filial, self.receita,
            {'quantidade_planejada': planejada}, self.usuario,
        )
        OrdemPolpaService.mover(op, S.LIBERADA, self.usuario)
        OrdemPolpaService.mover(op, S.EM_PRODUCAO, self.usuario)
        if apontar:
            apontar(op)
        OrdemPolpaService.concluir(op, self.usuario, produzida)
        op.refresh_from_db()
        return op


class ProducaoTests(PainelBase):
    """O que a fábrica produziu."""

    def test_a_ordem_produzida_entra_nos_tres_periodos(self):
        self._produzir(produzida=Decimal('970'))

        producao = IndicadoresService.producao(self.filial)

        self.assertEqual(producao['dia']['unidades'], Decimal('970'))
        self.assertEqual(producao['semana']['unidades'], Decimal('970'))
        self.assertEqual(producao['mes']['unidades'], Decimal('970'))

    def test_unidades_e_quilos_saem_juntos(self):
        """
        A fábrica fala nos dois: vende em unidade e compra fruta em quilo, e
        converter de cabeça a cada conversa é como os números param de bater
        entre as áreas.
        """
        self._produzir(produzida=Decimal('1000'))

        producao = IndicadoresService.producao(self.filial)

        # 1.000 un × 0,100 kg
        self.assertEqual(producao['dia']['kg'], Decimal('100.000'))

    def test_ordem_aberta_nao_conta_como_producao(self):
        """Ordem aberta é intenção — somá-la prometeria produto que não existe."""
        OrdemPolpaService.criar(
            self.filial, self.receita,
            {'quantidade_planejada': Decimal('5000')}, self.usuario,
        )

        producao = IndicadoresService.producao(self.filial)

        self.assertEqual(producao['dia']['unidades'], Decimal('0'))
        self.assertEqual(producao['abertas'], 1)


class EficienciaTests(PainelBase):
    """Rendimento, perda, produtividade, capacidade e tempo."""

    def test_o_rendimento_compara_produzido_com_planejado(self):
        self._produzir(planejada=Decimal('1000'), produzida=Decimal('970'))

        eficiencia = IndicadoresService.eficiencia(self.filial)

        self.assertEqual(eficiencia['rendimento'], Decimal('97.00'))

    def test_sem_ordem_encerrada_o_rendimento_e_nulo(self):
        """Zero seria "não rendeu nada" — e é diferente de "nada foi medido"."""
        eficiencia = IndicadoresService.eficiencia(self.filial)

        self.assertIsNone(eficiencia['rendimento'])
        self.assertIsNone(eficiencia['produtividade'])

    def test_a_perda_de_processo_sai_dos_apontamentos(self):
        def despolpa(op):
            ProcessoService.apontar(
                op.etapas_processo.get(etapa=Etapa.DESPOLPAMENTO),
                {
                    'quantidade_entrada': Decimal('1000'),
                    'quantidade_saida': Decimal('600'),
                },
                self.usuario,
            )

        self._produzir(apontar=despolpa)

        eficiencia = IndicadoresService.eficiencia(self.filial)

        self.assertEqual(eficiencia['perda_processo'], Decimal('400'))
        self.assertEqual(eficiencia['perda_percentual'], Decimal('40.00'))

    def test_etapa_sem_pesagem_nao_entra_na_perda(self):
        """
        Etapa apontada sem entrada e saída não diz nada sobre perda — contá-la
        como zero faria o índice mentir para menos.
        """
        def sem_balanca(op):
            ProcessoService.apontar(
                op.etapas_processo.get(etapa=Etapa.DESPOLPAMENTO),
                {'observacao': 'sem balanca'}, self.usuario,
            )

        self._produzir(apontar=sem_balanca)

        eficiencia = IndicadoresService.eficiencia(self.filial)

        self.assertIsNone(eficiencia['perda_percentual'])

    def test_sem_recurso_com_capacidade_nao_ha_percentual(self):
        """Não dá para dizer que se usa 80% do que ninguém mediu."""
        Recurso.objects.create(
            filial=self.filial, nome='Despolpadeira', capacidade_dia=None,
        )

        self.assertIsNone(IndicadoresService.eficiencia(self.filial)['capacidade'])

    def test_a_capacidade_usa_o_programado_dos_sete_dias(self):
        recurso = Recurso.objects.create(
            filial=self.filial, nome='Linha 1', capacidade_dia=Decimal('1000'),
        )
        op = OrdemPolpaService.criar(
            self.filial, self.receita,
            {'quantidade_planejada': Decimal('3500')}, self.usuario,
        )
        PlanejamentoService.programar(op, timezone.now(), recurso)

        capacidade = IndicadoresService.eficiencia(self.filial)['capacidade']

        self.assertEqual(capacidade['programado'], Decimal('3500'))
        self.assertEqual(capacidade['disponivel'], Decimal('7000'))
        self.assertEqual(capacidade['percentual'], Decimal('50.0'))


class CustoTests(PainelBase):
    """Custo por kg, por unidade, por produto e o desvio."""

    def test_o_custo_sai_do_servico_que_a_ordem_ja_usa(self):
        """
        Refazer a conta aqui daria dois custos para a mesma batida, e o
        painel discordaria da própria ordem que ele resume.
        """
        self._produzir(produzida=Decimal('1000'))

        custos = IndicadoresService.custos(self.filial)

        self.assertEqual(custos['ordens'], 1)
        self.assertGreater(custos['total_real'], Decimal('0'))
        self.assertIsNotNone(custos['por_unidade'])
        self.assertIsNotNone(custos['por_kg'])

    def test_o_custo_por_produto_agrupa_as_ordens(self):
        self._produzir(produzida=Decimal('500'))
        self._produzir(produzida=Decimal('500'))

        custos = IndicadoresService.custos(self.filial)

        self.assertEqual(len(custos['por_produto']), 1)
        self.assertEqual(custos['por_produto'][0]['ordens'], 2)

    def test_sem_producao_as_divisoes_sao_nulas(self):
        custos = IndicadoresService.custos(self.filial)

        self.assertIsNone(custos['por_unidade'])
        self.assertIsNone(custos['por_kg'])
        self.assertIsNone(custos['desvio'])


class EstoqueTests(PainelBase):
    """Matéria-prima, acabado, parado e giro."""

    def test_a_materia_prima_e_contada_com_valor(self):
        self._estoque_mp(Decimal('1000'))

        estoque = IndicadoresService.estoque(self.filial)

        self.assertEqual(estoque['materia_prima']['saldo'], Decimal('1000.000'))
        self.assertEqual(estoque['materia_prima']['valor'], Decimal('1500.00'))

    def test_o_acabado_vem_do_servico_de_armazenagem(self):
        self._produzir(produzida=Decimal('1000'))

        estoque = IndicadoresService.estoque(self.filial)

        self.assertEqual(estoque['acabado']['lotes'], 1)

    def test_o_lote_novo_ja_nasce_parado(self):
        """
        Sem saída registrada, o lote está parado desde que nasceu — e é isso
        que a lista mostra: o estoque que ninguém vê.
        """
        self._produzir(produzida=Decimal('1000'))

        parados = IndicadoresService.estoque(self.filial)['parados']

        self.assertTrue(any(
            p['lote'].produto == self.acabado for p in parados
        ))

    def test_os_dias_parados_nao_erram_por_causa_do_fuso(self):
        """
        `localdate()` é data LOCAL e `created_at` é gravado em UTC. Comparar
        os dois direto erra por um dia das 21h à meia-noite, quando a data UTC
        já virou e a local não.

        E erra para MENOS: o lote mais parado aparece mais novo do que é —
        justamente o que esta lista existe para não deixar acontecer. Este
        teste fixa o relógio nesse intervalo, que é onde o defeito mora.
        """
        from unittest.mock import patch

        self._produzir(produzida=Decimal('1000'))
        lote = LoteProduto.objects.get(produto=self.acabado)

        # 22h em São Paulo do dia 10 — em UTC já é dia 11.
        local = timezone.get_current_timezone()
        nascimento = timezone.make_aware(
            datetime(2026, 6, 10, 22, 0), local,
        )
        LoteProduto.objects.filter(pk=lote.pk).update(created_at=nascimento)

        # E "hoje", localmente, é o dia 12: o lote está parado há 2 dias.
        with patch.object(
            timezone, 'localdate', return_value=date(2026, 6, 12),
        ):
            parados = IndicadoresService.estoque(self.filial)['parados']

        linha = next(p for p in parados if p['lote'].pk == lote.pk)
        self.assertEqual(
            linha['dias'], 2,
            'a idade sai da data LOCAL dos dois lados — pela UTC daria 1',
        )

    def test_o_giro_mostra_as_duas_parcelas(self):
        self._produzir(produzida=Decimal('1000'))

        giro = IndicadoresService.estoque(self.filial)['giro']

        # A produção consumiu matéria-prima: houve saída no período.
        self.assertGreater(giro['consumo'], Decimal('0'))
        self.assertIsNotNone(giro['indice'])

    def test_sem_saida_o_giro_e_nulo(self):
        """
        "Não girou" e "não teve saída registrada" levam a decisões opostas.
        """
        self._estoque_mp(Decimal('500'))

        self.assertIsNone(IndicadoresService.estoque(self.filial)['giro']['indice'])


class QualidadeTests(PainelBase):
    """As duas fontes, e o índice de perdas."""

    def _analise(self, resultado):
        from apps.qualidade.constants.enums import TipoAnalise
        from apps.qualidade.models import AnaliseQualidade

        return AnaliseQualidade.objects.create(
            filial=self.filial, tipo_analise=TipoAnalise.choices[0][0],
            parametros={'brix': 12.5}, resultado=resultado,
            responsavel_tecnico=self.usuario, data_analise=timezone.now(),
        )

    def test_aprovadas_e_reprovadas_sao_contadas(self):
        from apps.qualidade.constants.enums import ResultadoAnalise

        self._analise(ResultadoAnalise.APROVADO)
        self._analise(ResultadoAnalise.APROVADO_COM_RESSALVA)
        self._analise(ResultadoAnalise.REPROVADO)

        qualidade = IndicadoresService.qualidade(self.filial)

        self.assertEqual(qualidade['aprovadas'], 2)
        self.assertEqual(qualidade['reprovadas'], 1)
        self.assertEqual(qualidade['taxa_aprovacao'], Decimal('66.67'))

    def test_a_inspecao_de_lote_e_contada_a_parte(self):
        """
        Somar as duas fontes esconderia qual delas reprovou — e são áreas
        diferentes que respondem por cada uma.
        """
        from apps.lotes.models import InspecaoLote

        op = self._produzir()
        InspecaoLote.objects.create(
            lote=op.lote, responsavel=self.usuario,
            data_inspecao=timezone.now(),
            resultado=InspecaoLote.Resultado.REPROVADO,
        )

        qualidade = IndicadoresService.qualidade(self.filial)

        self.assertEqual(qualidade['inspecoes'], 1)
        self.assertEqual(qualidade['inspecoes_reprovadas'], 1)
        self.assertEqual(qualidade['analises'], 0)

    def test_lote_bloqueado_aparece_na_qualidade(self):
        op = self._produzir()
        ArmazenagemService.bloquear(op.lote, 'Reprovado na analise')

        self.assertEqual(IndicadoresService.qualidade(self.filial)['bloqueados'], 1)

    def test_sem_analise_a_taxa_e_nula(self):
        self.assertIsNone(
            IndicadoresService.qualidade(self.filial)['taxa_aprovacao']
        )


class TelaPainelTests(PainelBase):
    """A tela."""

    def test_o_painel_abre(self):
        resposta = self.client.get(reverse('polpa:painel'))

        self.assertEqual(resposta.status_code, 200)
        for bloco in ('Produção', 'Eficiência', 'Custos', 'Estoque', 'Qualidade'):
            self.assertContains(resposta, bloco)

    def test_a_rota_do_menu_nao_cai_no_placeholder(self):
        from django.urls import resolve

        from apps.polpa.views import ItemView

        achado = resolve(reverse('polpa:item', args=['indicadores', 'painel']))

        self.assertIsNot(getattr(achado.func, 'view_class', None), ItemView)

    def test_a_janela_muda_com_o_filtro(self):
        resposta = self.client.get(reverse('polpa:painel') + '?dias=7')

        self.assertEqual(resposta.context['dados']['dias'], 7)

    def test_janela_invalida_volta_ao_padrao(self):
        """Número inventado na URL não pode virar uma tela de erro."""
        resposta = self.client.get(reverse('polpa:painel') + '?dias=abc')

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context['dados']['dias'], 30)

    def test_a_tela_diz_onde_nao_ha_medicao(self):
        """
        Basta um número obviamente errado para o painel perder a confiança de
        quem olha — por isso a ausência é escrita, não zerada.
        """
        resposta = self.client.get(reverse('polpa:painel'))

        self.assertContains(resposta, 'sem ordem encerrada')

    def test_o_painel_mostra_o_que_foi_produzido(self):
        self._produzir(produzida=Decimal('970'))

        resposta = self.client.get(reverse('polpa:painel'))

        self.assertContains(resposta, '970')
        self.assertContains(resposta, 'Polpa de manga 100 g')
