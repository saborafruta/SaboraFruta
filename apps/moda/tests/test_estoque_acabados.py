"""
Acabados — a peça pronta que ainda não saiu.

É o estoque mais caro da fábrica: cada peça aqui já pagou tecido, aviamento
e todas as bancadas, e só vira dinheiro quando chega ao cliente.

O que os testes cercam:

  · a DEFINIÇÃO DE PRONTA tem de ser a mesma do botão que abre a expedição.
    Se divergirem, a tela lista ordem que o botão recusa, e quem tentar
    despachar bate numa parede sem entender por quê;
  · a ordem JÁ ENTREGUE não pode voltar para a prateleira. Ela não tem
    expedição *viva*, e uma busca ingênua por "sem documento" a traria de
    volta como se estivesse esperando;
  · a régua é o PRAZO DO PEDIDO, não a idade: pronta há dois dias com o
    prazo vencido é pior que pronta há vinte com prazo em três semanas;
  · despachado não conta como prateleira — a carga saiu do galpão.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import resolve, reverse
from django.utils import timezone

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial
from apps.moda.models import (
    EtapaOrdem, Expedicao, FichaTecnica, ItemPedidoProducao, MaterialFicha,
    Operacao, OperacaoRoteiro, OrdemProducao, PedidoProducao, ProdutoModa,
    Roteiro,
)
from apps.moda.services.estoque_acabado import EstoqueAcabadoService

S = Expedicao.Status


class AcabadoBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao Pronta LTDA', nome_fantasia='Pronta',
            cnpj='53345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Confeccao Pronta LTDA',
            cnpj='53345678000272', uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Time do Bairro',
            cpf_cnpj='12345678901',
        )

    def setUp(self):
        self.produto = ProdutoModa.objects.create(
            filial=self.filial, codigo='CAM001', nome='Camisa',
        )

    # ── Montagem ─────────────────────────────────────────────────────────

    def _ficha(self, custo='20'):
        ficha = FichaTecnica.objects.create(
            filial=self.filial, produto=self.produto,
        )
        MaterialFicha.objects.create(
            ficha=ficha, tipo=MaterialFicha.Tipo.TECIDO_PRINCIPAL,
            descricao='Malha Dry', unidade=MaterialFicha.Unidade.METRO,
            consumo=Decimal('1'), custo_unitario=Decimal(custo),
        )
        return ficha

    def _roteiro(self, minutos=10, custo_hora=60):
        roteiro = Roteiro.objects.create(filial=self.filial, produto=self.produto)
        operacao = Operacao.objects.create(
            filial=self.filial, nome='Costura', setor=Operacao.Setor.COSTURA,
            tempo_padrao=Decimal(minutos),
            tipo_custo=Operacao.TipoCusto.POR_HORA, custo=Decimal(custo_hora),
        )
        OperacaoRoteiro.objects.create(
            roteiro=roteiro, operacao=operacao, sequencia=10,
        )
        return roteiro

    def _pedido(self, numero=1, prazo_dias=None):
        return PedidoProducao.objects.create(
            filial=self.filial, cliente=self.cliente, numero=numero,
            data_prevista_entrega=(
                None if prazo_dias is None
                else timezone.localdate() + timedelta(days=prazo_dias)
            ),
        )

    def _ordem(self, pedido=None, quantidade=100, sequencial=1,
               status=OrdemProducao.Status.EM_PRODUCAO, emitida_dias=1):
        pedido = pedido or self._pedido(numero=sequencial)
        item = ItemPedidoProducao.objects.create(
            pedido=pedido, produto=self.produto, descricao='Camisa',
            quantidade=quantidade,
        )
        return OrdemProducao.objects.create(
            filial=self.filial, pedido=pedido, item=item, status=status,
            numero=f'OP-{sequencial:04d}', ano=2026, sequencial=sequencial,
            quantidade=quantidade,
            emitida_em=timezone.now() - timedelta(days=emitida_dias),
        )

    def _terminar(self, ordem, produzido=100, dias_atras=1,
                  status=EtapaOrdem.Status.CONCLUIDA, com_qualidade=True):
        """Fecha o fluxo produtivo, com ou sem a etapa de qualidade."""
        hoje = timezone.localdate()
        EtapaOrdem.objects.create(
            ordem=ordem, etapa=EtapaOrdem.Etapa.COSTURA, sequencia=6,
            status=EtapaOrdem.Status.CONCLUIDA, quantidade_produzida=produzido,
            data_conclusao=hoje - timedelta(days=dias_atras),
        )
        if com_qualidade:
            EtapaOrdem.objects.create(
                ordem=ordem, etapa=EtapaOrdem.Etapa.QUALIDADE, sequencia=8,
                status=status, quantidade_produzida=produzido if status == EtapaOrdem.Status.CONCLUIDA else 0,
                data_conclusao=(
                    hoje - timedelta(days=dias_atras)
                    if status == EtapaOrdem.Status.CONCLUIDA else None
                ),
            )

    def _expedicao(self, ordem, status=S.PRODUCAO_CONCLUIDA, numero=1):
        return Expedicao.objects.create(
            filial=self.filial, ordem=ordem, numero=numero, status=status,
        )

    def _painel(self):
        return EstoqueAcabadoService.painel(self.filial)


class ProntaTests(AcabadoBase):
    """A definição de pronta, e ela tem de bater com a do botão."""

    def test_qualidade_concluida_deixa_a_ordem_pronta(self):
        self._ficha()
        ordem = self._ordem()
        self._terminar(ordem)

        linhas = self._painel()['linhas']

        self.assertEqual(len(linhas), 1)
        self.assertEqual(linhas[0]['chave'], 'sem_expedicao')

    def test_qualidade_pulada_tambem_conta_como_pronta(self):
        """
        Nem toda peça passa por inspeção, e "pulada" diz que a etapa não se
        aplica àquela ordem. O botão de expedição aceita — a tela também.
        """
        self._ficha()
        ordem = self._ordem()
        self._terminar(ordem, status=EtapaOrdem.Status.PULADA)

        self.assertEqual(len(self._painel()['linhas']), 1)

    def test_qualidade_em_andamento_nao_esta_pronta(self):
        """A peça ainda está sendo inspecionada; não é prateleira."""
        self._ficha()
        ordem = self._ordem()
        self._terminar(ordem, status=EtapaOrdem.Status.EM_ANDAMENTO)

        self.assertEqual(self._painel()['linhas'], [])

    def test_sem_etapa_de_qualidade_vale_o_fluxo_inteiro_encerrado(self):
        self._ficha()
        ordem = self._ordem()
        self._terminar(ordem, com_qualidade=False)

        self.assertEqual(len(self._painel()['linhas']), 1)

    def test_ordem_cancelada_nao_e_estoque(self):
        self._ficha()
        ordem = self._ordem(status=OrdemProducao.Status.CANCELADA)
        self._terminar(ordem)

        self.assertEqual(self._painel()['linhas'], [])

    def test_a_mesma_regra_do_botao_de_abrir_expedicao(self):
        """
        Se as duas divergirem, a tela lista ordem que o serviço recusa e
        quem tentar despachar bate numa parede sem entender por quê.
        """
        from apps.moda.services import ExpedicaoService

        self._ficha()
        ordem = self._ordem()
        self._terminar(ordem)

        self.assertEqual(len(self._painel()['linhas']), 1)
        # Não levanta: o serviço concorda que esta ordem está pronta.
        ExpedicaoService.criar(self.filial, ordem)


class SaidaTests(AcabadoBase):
    """Quem já saiu, e quem só parece que saiu."""

    def test_a_expedicao_aberta_diz_onde_a_caixa_esta(self):
        self._ficha()
        ordem = self._ordem()
        self._terminar(ordem)
        self._expedicao(ordem, status=S.EMBALAGEM)

        linha = self._painel()['linhas'][0]

        self.assertEqual(linha['chave'], S.EMBALAGEM.value)
        self.assertEqual(linha['onde'], 'Embalagem')

    def test_despachado_fica_fora_do_total_e_num_cartao_proprio(self):
        """
        A carga saiu do galpão. Somá-la ao "pronto esperando" diria que há
        peça na prateleira que não está mais lá.
        """
        self._ficha()
        ordem = self._ordem()
        self._terminar(ordem)
        self._expedicao(ordem, status=S.DESPACHO)

        painel = self._painel()

        self.assertEqual(painel['linhas'], [])
        self.assertEqual(painel['resumo']['pecas'], 0)
        self.assertEqual(painel['resumo']['a_caminho'], 1)
        self.assertEqual(painel['resumo']['pecas_a_caminho'], 100)

    def test_entregue_saiu_do_estoque(self):
        self._ficha()
        ordem = self._ordem()
        self._terminar(ordem)
        self._expedicao(ordem, status=S.ENTREGA)

        painel = self._painel()

        self.assertEqual(painel['linhas'], [])
        self.assertEqual(painel['resumo']['a_caminho'], 0)

    def test_entregue_nao_volta_como_sem_documento(self):
        """
        A armadilha: a ordem entregue não tem expedição VIVA, e uma busca
        ingênua por "sem documento" a traria de volta para a prateleira.
        """
        self._ficha()
        ordem = self._ordem()
        self._terminar(ordem)
        self._expedicao(ordem, status=S.ENTREGA)

        painel = self._painel()

        self.assertEqual(painel['resumo']['sem_documento'], 0)
        self.assertEqual(painel['resumo']['pecas'], 0)

    def test_expedicao_cancelada_devolve_a_ordem_para_a_fila(self):
        """
        Documento cancelado não vale: a peça continua pronta e sem
        expedição aberta, que é exatamente o buraco que a tela mostra.
        """
        self._ficha()
        ordem = self._ordem()
        self._terminar(ordem)
        self._expedicao(ordem, status=S.CANCELADA)

        painel = self._painel()

        self.assertEqual(painel['resumo']['sem_documento'], 1)

    def test_sem_expedicao_e_o_achado_da_tela(self):
        self._ficha()
        ordem = self._ordem(quantidade=100)
        self._terminar(ordem, produzido=100)

        resumo = self._painel()['resumo']

        self.assertEqual(resumo['sem_documento'], 1)
        self.assertEqual(resumo['pecas_sem_documento'], 100)


class PrazoTests(AcabadoBase):
    """A régua é o prazo do pedido, não a idade da pilha."""

    def test_pronta_com_prazo_vencido_e_atrasada(self):
        self._ficha()
        pedido = self._pedido(prazo_dias=-5)
        ordem = self._ordem(pedido=pedido)
        self._terminar(ordem)

        linha = self._painel()['linhas'][0]

        self.assertTrue(linha['atrasada'])
        self.assertEqual(linha['dias_atraso'], 5)

    def test_prazo_no_futuro_nao_e_atraso(self):
        self._ficha()
        pedido = self._pedido(prazo_dias=20)
        ordem = self._ordem(pedido=pedido)
        self._terminar(ordem, dias_atras=30)

        linha = self._painel()['linhas'][0]

        self.assertFalse(linha['atrasada'])
        self.assertIsNone(linha['dias_atraso'])
        self.assertEqual(linha['dias_pronta'], 30)

    def test_pedido_sem_prazo_nao_inventa_atraso(self):
        self._ficha()
        ordem = self._ordem(pedido=self._pedido(prazo_dias=None))
        self._terminar(ordem, dias_atras=90)

        linha = self._painel()['linhas'][0]

        self.assertIsNone(linha['prazo'])
        self.assertFalse(linha['atrasada'])

    def test_a_atrasada_vem_antes_da_mais_antiga(self):
        """
        Pronta há dois dias com prazo vencido é pior que pronta há vinte
        com prazo em três semanas — e a expedição pega a de cima primeiro.
        """
        self._ficha()
        atrasada = self._ordem(
            pedido=self._pedido(numero=1, prazo_dias=-3),
            sequencial=1,
        )
        antiga = self._ordem(
            pedido=self._pedido(numero=2, prazo_dias=30),
            sequencial=2,
        )
        self._terminar(atrasada, dias_atras=2)
        self._terminar(antiga, dias_atras=60)

        linhas = self._painel()['linhas']

        self.assertEqual([l['numero'] for l in linhas], ['OP-0001', 'OP-0002'])

    def test_o_pior_e_o_de_maior_atraso_e_nao_o_de_maior_valor(self):
        """
        O relógio do cliente não muda com o tamanho do pedido: dez peças
        atrasadas há um mês incomodam mais que mil atrasadas há um dia.
        """
        self._ficha(custo='20')
        pequena = self._ordem(
            pedido=self._pedido(numero=1, prazo_dias=-40), quantidade=10,
            sequencial=1,
        )
        grande = self._ordem(
            pedido=self._pedido(numero=2, prazo_dias=-1), quantidade=1000,
            sequencial=2,
        )
        self._terminar(pequena, produzido=10)
        self._terminar(grande, produzido=1000)

        resumo = self._painel()['resumo']

        self.assertEqual(resumo['atrasadas'], 2)
        self.assertEqual(resumo['pior']['numero'], 'OP-0001')


class ValorTests(AcabadoBase):
    """Custo cheio: a peça passou por tudo, então carrega tudo."""

    def test_soma_ficha_e_roteiro_inteiros(self):
        """20 de material + 10 de costura = 30 por peça, em 100 peças."""
        self._ficha(custo='20')
        self._roteiro(minutos=10, custo_hora=60)
        ordem = self._ordem(quantidade=100)
        self._terminar(ordem, produzido=100)

        linha = self._painel()['linhas'][0]

        self.assertEqual(linha['unitario'], Decimal('30.00'))
        self.assertEqual(linha['valor'], Decimal('3000.00'))

    def test_as_pecas_sao_as_que_a_ultima_bancada_entregou(self):
        """
        Entre a emissão e o fim morreu o que morreu; contar a quantidade
        emitida colocaria na prateleira peça que virou refugo.
        """
        self._ficha(custo='20')
        ordem = self._ordem(quantidade=100)
        self._terminar(ordem, produzido=93)

        linha = self._painel()['linhas'][0]

        self.assertEqual(linha['pecas'], 93)
        self.assertEqual(linha['valor'], Decimal('1860.00'))

    def test_ordem_sem_ficha_conta_peca_mas_nao_valor(self):
        ordem = self._ordem(quantidade=100)
        self._terminar(ordem, produzido=100)

        painel = self._painel()

        self.assertEqual(painel['resumo']['pecas'], 100)
        self.assertEqual(painel['resumo']['valor'], Decimal('0.00'))
        self.assertEqual(painel['resumo']['sem_custo'], 1)


class ResumoTests(AcabadoBase):

    def test_agrupa_por_posicao_da_fila(self):
        self._ficha(custo='20')
        sem_doc = self._ordem(sequencial=1, quantidade=10)
        embalando = self._ordem(sequencial=2, quantidade=20)
        self._terminar(sem_doc, produzido=10)
        self._terminar(embalando, produzido=20)
        self._expedicao(embalando, status=S.EMBALAGEM, numero=1)

        por_fila = {f['chave']: f for f in self._painel()['por_fila']}

        self.assertEqual(por_fila['sem_expedicao']['pecas'], 10)
        self.assertEqual(por_fila[S.EMBALAGEM.value]['pecas'], 20)
        self.assertEqual(por_fila[S.EMBALAGEM.value]['valor'], Decimal('400.00'))

    def test_posicao_vazia_nao_vira_linha(self):
        self._ficha()
        ordem = self._ordem()
        self._terminar(ordem)

        self.assertEqual(
            [f['chave'] for f in self._painel()['por_fila']], ['sem_expedicao'],
        )

    def test_sem_nada_pronto_nao_estoura(self):
        painel = self._painel()

        self.assertEqual(painel['linhas'], [])
        self.assertEqual(painel['por_fila'], [])
        self.assertIsNone(painel['resumo']['pior'])
        self.assertEqual(painel['resumo']['valor'], Decimal('0.00'))


class TelaAcabadoTests(TestCase):
    """A tela renderizando de verdade."""

    @classmethod
    def setUpTestData(cls):
        from apps.core.models import PerfilAcesso, Usuario

        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao Tela LTDA', nome_fantasia='Tela',
            cnpj='53345678000191', segmento='moda_confeccao',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Confeccao Tela LTDA',
            cnpj='53345678000272', uf='RN', cidade='Natal', is_matriz=True,
        )
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='chefe@teste.local', nome='Chefe', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )

    def setUp(self):
        self.client.force_login(self.usuario)

    def test_a_tela_abre_sem_dado_nenhum(self):
        resposta = self.client.get(reverse('moda:estoque-acabados'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Nada pronto esperando saída')

    def test_a_tela_abre_com_ordem_atrasada(self):
        cliente = Cliente.objects.create(
            filial=self.filial, razao_social='Time', cpf_cnpj='12345678901',
        )
        produto = ProdutoModa.objects.create(
            filial=self.filial, codigo='CAM001', nome='Camisa',
        )
        ficha = FichaTecnica.objects.create(filial=self.filial, produto=produto)
        MaterialFicha.objects.create(
            ficha=ficha, tipo=MaterialFicha.Tipo.TECIDO_PRINCIPAL,
            descricao='Malha Dry', unidade=MaterialFicha.Unidade.METRO,
            consumo=Decimal('1'), custo_unitario=Decimal('20'),
        )
        pedido = PedidoProducao.objects.create(
            filial=self.filial, cliente=cliente, numero=1,
            data_prevista_entrega=timezone.localdate() - timedelta(days=7),
        )
        item = ItemPedidoProducao.objects.create(
            pedido=pedido, produto=produto, descricao='Camisa', quantidade=100,
        )
        ordem = OrdemProducao.objects.create(
            filial=self.filial, pedido=pedido, item=item,
            status=OrdemProducao.Status.EM_PRODUCAO,
            numero='OP-0001', ano=2026, sequencial=1, quantidade=100,
        )
        EtapaOrdem.objects.create(
            ordem=ordem, etapa=EtapaOrdem.Etapa.QUALIDADE, sequencia=8,
            status=EtapaOrdem.Status.CONCLUIDA, quantidade_produzida=100,
            data_conclusao=timezone.localdate() - timedelta(days=10),
        )

        resposta = self.client.get(reverse('moda:estoque-acabados'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'OP-0001')
        self.assertContains(resposta, '2000,00')
        self.assertContains(resposta, 'prontas sem expedição aberta')

    def test_a_rota_do_menu_cai_na_tela(self):
        from apps.moda.views_estoque_acabado import EstoqueAcabadoView

        for url in (
            reverse('moda:estoque-acabados'),
            reverse('moda:item', args=['estoque', 'acabados']),
        ):
            self.assertIs(resolve(url).func.view_class, EstoqueAcabadoView)

    def test_a_fila_de_expedicao_continua_sendo_outra_tela(self):
        fila = reverse('moda:expedicao-list')

        self.assertNotEqual(fila, reverse('moda:estoque-acabados'))
        self.assertEqual(self.client.get(fila).status_code, 200)
