"""
Perdas e rendimento: planejado contra real, e o alerta que chegava a ninguém.

Os quatro números da especificação já eram calculados: entraram 1.000 kg de
manga, saíram 850, perdeu 150, rendeu 85%. O que faltava era o outro lado da
comparação e a consequência.

`Receita.rendimento_esperado` existia (manga ≈ 60%) e nada o confrontava com o
real. E não havia limite configurável em lugar nenhum: o único que existia era
uma constante de 80% no serviço genérico, que escrevia `logger.warning` — log
é onde ninguém olha.

O que os testes cercam:

  · O REAL É PESO, o do lote é unidade. São perguntas diferentes — "quanto a
    fruta rendeu" e "a ordem entregou o que prometeu" — e compará-las entre si
    não significa nada;
  · O PISO VEM DA RECEITA, em pontos abaixo do esperado. Manga não rende como
    acerola, e um número fixo no código faria metade dos produtos alertar
    sempre e a outra metade nunca;
  · SEM ESPERADO NÃO HÁ VEREDITO. `dentro` é nulo, e não verdadeiro: mostrar
    verde ali seria afirmar uma aprovação que ninguém deu;
  · O ALERTA VAI PARA O SINO, com os quatro números na mensagem — quem lê
    decide dali se para a linha;
  · FALHA NO AVISO NÃO IMPEDE FECHAR A BATIDA. Ela já aconteceu.
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.core.models import (
    Empresa, Filial, Notificacao, PerfilAcesso, Usuario,
)
from apps.estoque.models import Estoque, LoteProduto
from apps.polpa.models import Etapa, EtapaReceita, FichaProduto, OrdemPolpa
from apps.polpa.services import CatalogoService, OrdemPolpaService, ReceitaService
from apps.polpa.services.processo import ProcessoService
from apps.producao.models import ItemFichaTecnica
from apps.produtos.models import UnidadeMedida, UnidadeMedidaFilial

T = FichaProduto.Tipo
S = OrdemPolpa.Situacao
ZERO = Decimal('0')
TIPO_ALERTA = Notificacao.Tipo.POLPA_RENDIMENTO_BAIXO


class RendimentoBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Polpas Rendimento LTDA', nome_fantasia='Rendimento',
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
            email='chefe@rend.local', nome='Chefe', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )

    def setUp(self):
        self.acabado = self._item(
            T.POLPA, 'Polpa de manga 100 g', validade_dias=180,
            peso_liquido=Decimal('0.100'),
        )
        self.manga = self._item(T.FRUTA, 'Manga in natura', custo=Decimal('1.50'))
        self.pote = self._item(T.POTE, 'Pote 100 g', custo=Decimal('0.20'))

    def _item(self, tipo, descricao, custo=Decimal('0'), **extras):
        dados = {
            'tipo': tipo, 'descricao': descricao, 'codigo': descricao[:10],
            'unidade_medida': self.unidade, 'preco_custo': custo,
        }
        dados.update(extras)
        return CatalogoService.salvar(self.filial, dados).produto

    def _receita(self, esperado='60', tolerado='5'):
        receita = ReceitaService.criar(self.filial, self.acabado, {
            'descricao': 'Polpa de manga 100 g', 'versao': '1.0',
            'quantidade_produzida': Decimal('1000'),
            'rendimento_esperado': Decimal(esperado) if esperado else None,
        })
        if tolerado is not None:
            receita.desvio_tolerado = Decimal(tolerado)
            receita.save(update_fields=['desvio_tolerado'])
        ItemFichaTecnica.objects.create(
            ficha=receita.ficha, materia_prima=self.manga,
            quantidade=Decimal('1200'), perda_prevista=ZERO,
        )
        ItemFichaTecnica.objects.create(
            ficha=receita.ficha, materia_prima=self.pote,
            quantidade=Decimal('10000'), perda_prevista=ZERO,
        )
        EtapaReceita.objects.create(receita=receita, ordem=1, nome='Despolpa')
        ReceitaService.ativar(receita)
        return receita

    def _estoque(self, produto, quantidade):
        estoque, _ = Estoque.objects.get_or_create(
            produto=produto, filial=self.filial,
        )
        estoque.quantidade_atual = Decimal(quantidade)
        estoque.quantidade_reservada = ZERO
        estoque.atualizar_disponivel()
        estoque.save()
        LoteProduto.objects.create(
            filial=self.filial, produto=produto,
            numero_lote=f'L-{produto.pk}', quantidade_inicial=Decimal(quantidade),
            quantidade_atual=Decimal(quantidade), custo_unitario=produto.preco_custo,
            data_validade=timezone.localdate() + timedelta(days=365),
            status=LoteProduto.Status.ATIVO,
        )

    def _op(self, receita, quantidade='1000'):
        self._estoque(self.manga, '50000')
        self._estoque(self.pote, '500000')
        return OrdemPolpaService.criar(
            self.filial, receita,
            {'quantidade_planejada': Decimal(quantidade)}, self.usuario,
        )

    def _pesar(self, op, entrada='1000', saida='850'):
        """
        O exemplo da especificação: 1.000 kg de manga entram, 850 saem.

        A entrada é da PRIMEIRA etapa e a saída da ÚLTIMA — é assim que o
        resumo lê, porque as perdas se compõem pelo caminho.
        """
        etapas = list(op.etapas_processo.all())
        ProcessoService.apontar(etapas[0], {
            'quantidade_entrada': entrada, 'quantidade_saida': entrada,
        }, self.usuario)
        ProcessoService.apontar(etapas[-1], {
            'quantidade_entrada': saida, 'quantidade_saida': saida,
        }, self.usuario)

    def _produzir(self, op, quantidade):
        """A batida inteira: libera, produz e conclui."""
        OrdemPolpaService.mover(op, S.LIBERADA, self.usuario)
        OrdemPolpaService.mover(op, S.EM_PRODUCAO, self.usuario)
        return OrdemPolpaService.concluir(
            op, self.usuario, quantidade=Decimal(quantidade),
        )

    def _alertas(self):
        return Notificacao.objects.filter(filial=self.filial, tipo=TIPO_ALERTA)


class OsQuatroNumerosTests(RendimentoBase):

    def test_o_exemplo_da_especificacao(self):
        """1.000 kg entram, 850 saem, 150 de perda, 85% de rendimento."""
        receita = self._receita()
        op = self._op(receita)
        self._pesar(op, entrada='1000', saida='850')

        resumo = ProcessoService.resumo(op)

        self.assertEqual(resumo['entrada'], Decimal('1000.000'))
        self.assertEqual(resumo['saida'], Decimal('850.000'))
        self.assertEqual(resumo['perda_total'], Decimal('150.000'))
        self.assertEqual(resumo['rendimento'], Decimal('85.00'))

    def test_o_rendimento_do_processo_nao_e_o_do_lote(self):
        """
        Um é peso (quanto da fruta virou produto), o outro é unidade (a ordem
        entregou o que prometeu). Compará-los entre si não significa nada.
        """
        receita = self._receita()
        op = self._op(receita, quantidade='1000')
        self._pesar(op, entrada='1000', saida='850')

        resumo = ProcessoService.resumo(op)

        self.assertEqual(resumo['rendimento'], Decimal('85.00'))
        # O do lote só existe depois de produzida — e conta outra coisa.
        self.assertIsNone(op.rendimento_lote)


class PlanejadoContraRealTests(RendimentoBase):

    def test_o_resumo_traz_o_esperado_ao_lado_do_real(self):
        receita = self._receita(esperado='60')
        op = self._op(receita)
        self._pesar(op, entrada='1000', saida='850')

        resumo = ProcessoService.resumo(op)

        self.assertEqual(resumo['rendimento_esperado'], Decimal('60.00'))
        self.assertEqual(resumo['rendimento_desvio'], Decimal('25.00'))

    def test_desvio_negativo_quando_rende_menos(self):
        receita = self._receita(esperado='60')
        op = self._op(receita)
        self._pesar(op, entrada='1000', saida='500')

        resumo = ProcessoService.resumo(op)

        self.assertEqual(resumo['rendimento'], Decimal('50.00'))
        self.assertEqual(resumo['rendimento_desvio'], Decimal('-10.00'))

    def test_o_piso_vem_da_receita(self):
        """Esperado 60 com tolerância 5 alerta abaixo de 55."""
        receita = self._receita(esperado='60', tolerado='5')

        self.assertEqual(receita.rendimento_minimo, Decimal('55.00'))

    def test_dentro_do_piso_nao_alerta(self):
        receita = self._receita(esperado='60', tolerado='5')
        op = self._op(receita)
        self._pesar(op, entrada='1000', saida='560')  # 56%

        resumo = ProcessoService.resumo(op)

        self.assertTrue(resumo['rendimento_dentro'])
        self.assertFalse(resumo['rendimento_abaixo'])

    def test_abaixo_do_piso_marca(self):
        receita = self._receita(esperado='60', tolerado='5')
        op = self._op(receita)
        self._pesar(op, entrada='1000', saida='540')  # 54%

        resumo = ProcessoService.resumo(op)

        self.assertFalse(resumo['rendimento_dentro'])
        self.assertTrue(resumo['rendimento_abaixo'])

    def test_tolerancia_maior_aceita_mais(self):
        """A tolerância é o que a fábrica ajusta, e ela muda o veredito."""
        receita = self._receita(esperado='60', tolerado='10')
        op = self._op(receita)
        self._pesar(op, entrada='1000', saida='540')  # 54%, piso 50

        self.assertTrue(ProcessoService.resumo(op)['rendimento_dentro'])

    def test_sem_esperado_nao_ha_veredito(self):
        """
        Receita sem rendimento esperado não está "dentro do limite" — ela não
        tem limite, e verde ali seria uma aprovação que ninguém deu.

        `ReceitaService.ativar` já recusa receita sem esperado — regra certa,
        e é dela que vem o cenário alcançável: alguém limpa o campo DEPOIS de
        a receita estar ativa, com ordens já rodando.
        """
        receita = self._receita(esperado='60')
        op = self._op(receita)
        receita.rendimento_esperado = None
        receita.save(update_fields=['rendimento_esperado'])
        self._pesar(op, entrada='1000', saida='850')

        resumo = ProcessoService.resumo(op)

        self.assertIsNone(resumo['rendimento_dentro'])
        self.assertIsNone(resumo['rendimento_desvio'])
        self.assertFalse(resumo['rendimento_abaixo'])

    def test_sem_pesagem_nao_ha_veredito(self):
        receita = self._receita()
        op = self._op(receita)

        resumo = ProcessoService.resumo(op)

        self.assertIsNone(resumo['rendimento'])
        self.assertIsNone(resumo['rendimento_dentro'])


class AlertaNoSinoTests(RendimentoBase):

    def test_rendimento_abaixo_do_piso_toca_o_sino(self):
        """
        O aviso existia e ia para o log, onde ninguém olha. Um indicador que a
        fábrica cobra todo dia precisa ir para onde a fábrica olha.
        """
        receita = self._receita(esperado='60', tolerado='5')
        op = self._op(receita)
        self._pesar(op, entrada='1000', saida='540')

        self._produzir(op, '540')

        alerta = self._alertas().get()
        self.assertTrue(alerta.ativa)
        self.assertIn('54', alerta.titulo)

    def test_a_mensagem_leva_os_quatro_numeros(self):
        """Quem lê o sino decide dali se para a linha."""
        receita = self._receita(esperado='60', tolerado='5')
        op = self._op(receita)
        self._pesar(op, entrada='1000', saida='540')

        self._produzir(op, '540')

        mensagem = self._alertas().get().mensagem
        self.assertIn('1000', mensagem)
        self.assertIn('540', mensagem)
        self.assertIn('460', mensagem)   # a perda
        self.assertIn('60', mensagem)    # o esperado

    def test_dentro_do_piso_nao_toca(self):
        receita = self._receita(esperado='60', tolerado='5')
        op = self._op(receita)
        self._pesar(op, entrada='1000', saida='600')

        self._produzir(op, '600')

        self.assertEqual(self._alertas().count(), 0)

    def test_o_alerta_leva_a_ordem(self):
        receita = self._receita(esperado='60', tolerado='5')
        op = self._op(receita)
        self._pesar(op, entrada='1000', saida='540')

        self._produzir(op, '540')

        self.assertIn(str(op.pk), self._alertas().get().url)

    def test_falha_no_aviso_nao_impede_fechar_a_batida(self):
        """A batida já aconteceu — o que se perde ao travar é o registro."""
        receita = self._receita(esperado='60', tolerado='5')
        op = self._op(receita)
        self._pesar(op, entrada='1000', saida='540')

        with patch.object(
            ProcessoService, 'resumo', side_effect=RuntimeError('banco fora'),
        ):
            self._produzir(op, '540')

        op.refresh_from_db()
        self.assertEqual(op.situacao, S.PRODUZIDA)


class TelaTests(RendimentoBase):
    """A tela dizia verde para qualquer rendimento."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.usuario)

    def _abrir(self, op):
        from django.urls import reverse
        return self.client.get(reverse('polpa:processo-ordem', args=[op.pk]))

    def test_abaixo_do_piso_a_tela_marca_em_vermelho(self):
        receita = self._receita(esperado='60', tolerado='5')
        op = self._op(receita)
        self._pesar(op, entrada='1000', saida='300')  # 30%

        resposta = self._abrir(op)

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'abaixo do piso de 55')
        self.assertContains(resposta, '#dc2626')

    def test_dentro_do_piso_nao_marca(self):
        receita = self._receita(esperado='60', tolerado='5')
        op = self._op(receita)
        self._pesar(op, entrada='1000', saida='600')

        self.assertNotContains(self._abrir(op), 'abaixo do piso')

    def test_a_tela_mostra_o_desvio(self):
        receita = self._receita(esperado='60', tolerado='5')
        op = self._op(receita)
        self._pesar(op, entrada='1000', saida='850')

        # Sem o separador decimal na asserção: o projeto é pt-BR e a
        # localização troca o ponto pela vírgula na renderização.
        self.assertContains(self._abrir(op), '+25')

    def test_nada_de_tag_vaza_para_a_tela(self):
        receita = self._receita()
        op = self._op(receita)
        self._pesar(op)

        corpo = self._abrir(op).content.decode()

        for marca in ('{%', '%}', '{#', '#}', 'endcomment'):
            self.assertNotIn(marca, corpo, f'tag {marca} vazou para a tela')
