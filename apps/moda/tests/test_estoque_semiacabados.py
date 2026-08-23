"""
Semiacabados — o estoque que está no chão de fábrica.

A ideia que a tela defende: PARAR TARDE CUSTA MAIS CARO. Uma peça encalhada
depois da costura já pagou tecido, corte e costura; a mesma peça antes do
corte não pagou quase nada. Se o valor virar "peça × preço médio", essa
diferença desaparece e a tela deixa de servir para decidir o que destravar.

Os jeitos de errar que os testes cercam:

  · somar material antes do corte — o tecido ainda é rolo, e contá-lo aqui
    somaria ao WIP algo que está na prateleira;
  · dar crédito de mão de obra por etapa EM ANDAMENTO — a peça está em cima
    da bancada, não saiu dela;
  · devolver zero dias por falta de data de apontamento, o que faria a ordem
    mais esquecida parecer a mais nova;
  · contar a mesma peça em dois baldes, e aí o total não bate com o chão.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import resolve, reverse
from django.utils import timezone

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial
from apps.moda.models import (
    EtapaOrdem, FichaTecnica, ItemPedidoProducao, MaterialFicha, Operacao,
    OperacaoRoteiro, OrdemProducao, PedidoProducao, ProdutoModa, Roteiro,
)
from apps.moda.services.estoque_semiacabado import (
    DIAS_ENCALHADO, EstoqueSemiacabadoService,
)


class SemiacabadoBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao WIP LTDA', nome_fantasia='WIP',
            cnpj='53345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Confeccao WIP LTDA',
            cnpj='53345678000272', uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Time', cpf_cnpj='12345678901',
        )

    def setUp(self):
        self.produto = ProdutoModa.objects.create(
            filial=self.filial, codigo='CAM001', nome='Camisa',
        )
        self.pedido = PedidoProducao.objects.create(
            filial=self.filial, cliente=self.cliente, numero=1,
        )

    # ── Montagem ─────────────────────────────────────────────────────────

    def _ficha(self, tecido='20', aviamento=None, produto=None):
        ficha = FichaTecnica.objects.create(
            filial=self.filial, produto=produto or self.produto,
        )
        MaterialFicha.objects.create(
            ficha=ficha, tipo=MaterialFicha.Tipo.TECIDO_PRINCIPAL,
            descricao='Malha Dry', unidade=MaterialFicha.Unidade.METRO,
            consumo=Decimal('1'), custo_unitario=Decimal(tecido),
        )
        if aviamento:
            MaterialFicha.objects.create(
                ficha=ficha, tipo=MaterialFicha.Tipo.AVIAMENTO,
                descricao='Zíper', unidade=MaterialFicha.Unidade.UNIDADE,
                consumo=Decimal('1'), custo_unitario=Decimal(aviamento),
            )
        return ficha

    def _roteiro(self, operacoes, produto=None):
        """`operacoes` = lista de (setor, minutos, custo/hora)."""
        produto = produto or self.produto
        roteiro = Roteiro.objects.create(filial=self.filial, produto=produto)
        for i, (setor, minutos, custo) in enumerate(operacoes, start=1):
            operacao = Operacao.objects.create(
                filial=self.filial, nome=f'{setor}-{produto.codigo}-{i}',
                setor=setor, tempo_padrao=Decimal(minutos),
                tipo_custo=Operacao.TipoCusto.POR_HORA, custo=Decimal(custo),
            )
            OperacaoRoteiro.objects.create(
                roteiro=roteiro, operacao=operacao, sequencia=i * 10,
            )
        return roteiro

    def _ordem(self, quantidade=100, sequencial=1, produto=None, dias_atras=1):
        item = ItemPedidoProducao.objects.create(
            pedido=self.pedido, produto=produto or self.produto,
            descricao='Camisa', quantidade=quantidade,
        )
        return OrdemProducao.objects.create(
            filial=self.filial, pedido=self.pedido, item=item,
            status=OrdemProducao.Status.EM_PRODUCAO,
            numero=f'OP-{sequencial:04d}', ano=2026, sequencial=sequencial,
            quantidade=quantidade,
            emitida_em=timezone.now() - timedelta(days=dias_atras),
        )

    # O fluxo real tem onze etapas; aqui bastam as que importam para o
    # balde, criadas na sequencia do modelo.
    SEQUENCIA = {
        EtapaOrdem.Etapa.CORTE: 4,
        EtapaOrdem.Etapa.ESTAMPA: 5,
        EtapaOrdem.Etapa.COSTURA: 6,
        EtapaOrdem.Etapa.ACABAMENTO: 7,
        EtapaOrdem.Etapa.QUALIDADE: 8,
    }

    def _etapa(self, ordem, etapa, status, produzido=0, inicio_dias=None,
               conclusao_dias=None):
        hoje = timezone.localdate()
        return EtapaOrdem.objects.create(
            ordem=ordem, etapa=etapa, sequencia=self.SEQUENCIA[etapa],
            status=status, quantidade_produzida=produzido,
            data_inicio=None if inicio_dias is None else hoje - timedelta(days=inicio_dias),
            data_conclusao=(
                None if conclusao_dias is None else hoje - timedelta(days=conclusao_dias)
            ),
        )

    def _fluxo(self, ordem, ate, atual, produzido=100, **datas):
        """
        Conclui as etapas ate' `ate` e deixa `atual` pendente.

        A ordem do fluxo importa: o balde sai da PRIMEIRA etapa nao
        encerrada, entao criar fora de sequencia daria outro balde.
        """
        for etapa, seq in self.SEQUENCIA.items():
            if seq <= self.SEQUENCIA[ate]:
                self._etapa(ordem, etapa, EtapaOrdem.Status.CONCLUIDA,
                            produzido=produzido, conclusao_dias=datas.get('conclusao'))
            elif etapa == atual:
                self._etapa(ordem, etapa, datas.get('status', EtapaOrdem.Status.PENDENTE),
                            inicio_dias=datas.get('inicio'))

    def _painel(self):
        return EstoqueSemiacabadoService.painel(self.filial)

    def _por_balde(self):
        return {l['chave']: l for l in self._painel()['linhas']}


class CustoAcumuladoTests(SemiacabadoBase):
    """Parar tarde custa mais caro — e o número tem de mostrar isso."""

    def test_material_entra_no_corte_e_nao_antes(self):
        """
        Antes do corte o tecido ainda é rolo: contá-lo aqui somaria ao WIP
        algo que está na prateleira, e o estoque apareceria em dobro.
        """
        self._ficha(tecido='20')
        ordem = self._ordem(quantidade=100)
        self._etapa(ordem, EtapaOrdem.Etapa.CORTE, EtapaOrdem.Status.PENDENTE)

        linha = self._por_balde()['aguardando_corte']

        self.assertEqual(linha['pecas'], 100)
        self.assertEqual(linha['valor'], Decimal('0.00'))

    def test_depois_do_corte_a_peca_carrega_o_material(self):
        self._ficha(tecido='20')
        ordem = self._ordem(quantidade=100)
        self._fluxo(ordem, ate=EtapaOrdem.Etapa.CORTE,
                    atual=EtapaOrdem.Etapa.ESTAMPA)

        linha = self._por_balde()['cortadas']

        self.assertEqual(linha['por_peca'], Decimal('20.00'))
        self.assertEqual(linha['valor'], Decimal('2000.00'))

    def test_o_aviamento_entra_junto_com_o_tecido(self):
        """
        Ninguém aponta quando um zíper é pregado. Inventar esse ponto daria
        precisão falsa, então ele entra no corte com o resto do material.
        """
        self._ficha(tecido='20', aviamento='3')
        ordem = self._ordem(quantidade=100)
        self._fluxo(ordem, ate=EtapaOrdem.Etapa.CORTE,
                    atual=EtapaOrdem.Etapa.ESTAMPA)

        self.assertEqual(self._por_balde()['cortadas']['por_peca'], Decimal('23.00'))

    def test_a_mao_de_obra_entra_setor_a_setor(self):
        """
        10 min a R$ 60/h são R$ 10 por peça em cada setor concluído. Depois
        do corte: material 20 + corte 10 = 30.
        """
        self._ficha(tecido='20')
        self._roteiro([
            (Operacao.Setor.CORTE, 10, 60),
            (Operacao.Setor.COSTURA, 10, 60),
        ])
        ordem = self._ordem(quantidade=100)
        self._fluxo(ordem, ate=EtapaOrdem.Etapa.CORTE,
                    atual=EtapaOrdem.Etapa.ESTAMPA)

        self.assertEqual(self._por_balde()['cortadas']['por_peca'], Decimal('30.00'))

    def test_a_peca_fica_mais_cara_conforme_desce_a_fabrica(self):
        """
        É a tese da tela: a mesma peça vale 30 depois do corte e 40 depois
        da costura. Uma pilha pequena no fim pesa mais que uma grande no
        começo.
        """
        self._ficha(tecido='20')
        self._roteiro([
            (Operacao.Setor.CORTE, 10, 60),
            (Operacao.Setor.COSTURA, 10, 60),
        ])
        cedo = self._ordem(quantidade=100, sequencial=1)
        tarde = self._ordem(quantidade=100, sequencial=2)
        self._fluxo(cedo, ate=EtapaOrdem.Etapa.CORTE,
                    atual=EtapaOrdem.Etapa.ESTAMPA)
        self._fluxo(tarde, ate=EtapaOrdem.Etapa.COSTURA,
                    atual=EtapaOrdem.Etapa.ACABAMENTO)

        baldes = self._por_balde()

        self.assertEqual(baldes['cortadas']['por_peca'], Decimal('30.00'))
        self.assertEqual(baldes['acabamento']['por_peca'], Decimal('40.00'))

    def test_etapa_em_andamento_nao_gera_credito(self):
        """A peça está em cima da bancada; ela não saiu dela."""
        self._ficha(tecido='20')
        self._roteiro([(Operacao.Setor.CORTE, 10, 60)])
        ordem = self._ordem(quantidade=100)
        self._etapa(ordem, EtapaOrdem.Etapa.CORTE,
                    EtapaOrdem.Status.EM_ANDAMENTO)

        linha = self._por_balde()['em_corte']

        self.assertEqual(linha['pecas'], 100)
        self.assertEqual(linha['valor'], Decimal('0.00'))

    def test_ordem_sem_ficha_conta_peca_mas_nao_valor(self):
        """
        E a tela diz isso: sem essa ressalva o total pareceria o valor
        inteiro do chão, quando é um piso.
        """
        ordem = self._ordem(quantidade=100)
        self._fluxo(ordem, ate=EtapaOrdem.Etapa.CORTE,
                    atual=EtapaOrdem.Etapa.ESTAMPA)

        painel = self._painel()

        self.assertEqual(painel['resumo']['pecas'], 100)
        self.assertEqual(painel['resumo']['valor'], Decimal('0.00'))
        self.assertEqual(painel['sem_custo'], 1)


class TempoParadaTests(SemiacabadoBase):
    """Há quanto tempo a pilha está onde está."""

    def test_conta_do_inicio_da_etapa_atual(self):
        self._ficha()
        ordem = self._ordem(quantidade=100)
        self._etapa(ordem, EtapaOrdem.Etapa.CORTE,
                    EtapaOrdem.Status.EM_ANDAMENTO, inicio_dias=30)

        self.assertEqual(self._por_balde()['em_corte']['dias'], 30)

    def test_sem_data_de_inicio_conta_da_conclusao_da_anterior(self):
        """
        É o caso mais comum: a peça saiu do corte e ninguém abriu a etapa
        seguinte. O tempo parado é desde que ela chegou ali.
        """
        self._ficha()
        ordem = self._ordem(quantidade=100)
        self._fluxo(ordem, ate=EtapaOrdem.Etapa.CORTE,
                    atual=EtapaOrdem.Etapa.ESTAMPA, conclusao=40)

        self.assertEqual(self._por_balde()['cortadas']['dias'], 40)

    def test_sem_data_nenhuma_conta_da_emissao_da_ordem(self):
        """
        Devolver zero por falta de apontamento faria a ordem mais esquecida
        parecer a mais nova — justamente a que precisa aparecer.
        """
        self._ficha()
        ordem = self._ordem(quantidade=100, dias_atras=50)
        self._etapa(ordem, EtapaOrdem.Etapa.CORTE, EtapaOrdem.Status.PENDENTE)

        self.assertEqual(self._por_balde()['aguardando_corte']['dias'], 50)

    def test_marca_o_que_passou_do_limite(self):
        self._ficha()
        ordem = self._ordem(quantidade=100)
        self._etapa(ordem, EtapaOrdem.Etapa.CORTE,
                    EtapaOrdem.Status.EM_ANDAMENTO,
                    inicio_dias=DIAS_ENCALHADO + 5)

        painel = self._painel()

        self.assertTrue(painel['linhas'][0]['encalhado'])
        self.assertEqual(painel['resumo']['encalhadas'], 1)

    def test_dentro_do_limite_nao_e_encalhe(self):
        self._ficha()
        ordem = self._ordem(quantidade=100)
        self._etapa(ordem, EtapaOrdem.Etapa.CORTE,
                    EtapaOrdem.Status.EM_ANDAMENTO, inicio_dias=3)

        painel = self._painel()

        self.assertFalse(painel['linhas'][0]['encalhado'])
        self.assertEqual(painel['resumo']['encalhadas'], 0)


class ResumoTests(SemiacabadoBase):

    def test_o_mais_caro_e_por_dinheiro_e_nao_por_peca(self):
        """
        Uma pilha de 20 peças depois da costura pode valer mais que 200
        antes do corte — e é ela que a compra e o PCP precisam ver.
        """
        self._ficha(tecido='20')
        self._roteiro([
            (Operacao.Setor.CORTE, 10, 60),
            (Operacao.Setor.COSTURA, 10, 60),
        ])
        grande = self._ordem(quantidade=200, sequencial=1)
        pequena = self._ordem(quantidade=100, sequencial=2)
        self._etapa(grande, EtapaOrdem.Etapa.CORTE, EtapaOrdem.Status.PENDENTE)
        self._fluxo(pequena, ate=EtapaOrdem.Etapa.COSTURA,
                    atual=EtapaOrdem.Etapa.ACABAMENTO)

        resumo = self._painel()['resumo']

        self.assertEqual(resumo['pecas'], 300)
        self.assertEqual(resumo['mais_caro']['chave'], 'acabamento')
        self.assertEqual(resumo['mais_caro']['valor'], Decimal('4000.00'))

    def test_a_lista_de_paradas_vem_da_mais_antiga_para_a_mais_nova(self):
        self._ficha()
        antiga = self._ordem(quantidade=10, sequencial=1)
        nova = self._ordem(quantidade=10, sequencial=2)
        self._etapa(antiga, EtapaOrdem.Etapa.CORTE,
                    EtapaOrdem.Status.EM_ANDAMENTO, inicio_dias=60)
        self._etapa(nova, EtapaOrdem.Etapa.CORTE,
                    EtapaOrdem.Status.EM_ANDAMENTO, inicio_dias=2)

        paradas = self._painel()['paradas']

        self.assertEqual([p['numero'] for p in paradas], ['OP-0001', 'OP-0002'])
        self.assertEqual(self._painel()['resumo']['mais_antiga']['dias'], 60)

    def test_balde_vazio_nao_vira_linha(self):
        """Nove baldes com zero seria uma tela de traços."""
        self._ficha()
        ordem = self._ordem(quantidade=100)
        self._etapa(ordem, EtapaOrdem.Etapa.CORTE, EtapaOrdem.Status.PENDENTE)

        self.assertEqual(
            [l['chave'] for l in self._painel()['linhas']], ['aguardando_corte'],
        )

    def test_uma_peca_aparece_num_balde_so(self):
        """
        Se ela entrar em dois, o total deixa de bater com o que existe na
        fábrica — e é o total que se compara com o chão.
        """
        self._ficha()
        ordem = self._ordem(quantidade=100)
        self._fluxo(ordem, ate=EtapaOrdem.Etapa.CORTE,
                    atual=EtapaOrdem.Etapa.ESTAMPA)

        painel = self._painel()

        self.assertEqual(sum(l['pecas'] for l in painel['linhas']), 100)
        self.assertEqual(painel['resumo']['pecas'], 100)

    def test_ordem_encerrada_saiu_do_chao(self):
        self._ficha()
        ordem = self._ordem(quantidade=100)
        self._etapa(ordem, EtapaOrdem.Etapa.CORTE, EtapaOrdem.Status.PENDENTE)
        ordem.status = OrdemProducao.Status.CONCLUIDA
        ordem.save(update_fields=['status'])

        painel = self._painel()

        self.assertEqual(painel['linhas'], [])
        self.assertEqual(painel['resumo']['pecas'], 0)

    def test_sem_ordem_nenhuma_nao_estoura(self):
        painel = self._painel()

        self.assertEqual(painel['linhas'], [])
        self.assertEqual(painel['paradas'], [])
        self.assertIsNone(painel['resumo']['mais_caro'])
        self.assertIsNone(painel['resumo']['mais_antiga'])
        self.assertEqual(painel['resumo']['valor'], Decimal('0.00'))


class TelaSemiacabadoTests(TestCase):
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
        resposta = self.client.get(reverse('moda:estoque-semiacabados'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Nada em processo agora')

    def test_a_tela_abre_com_peca_no_chao(self):
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
        )
        item = ItemPedidoProducao.objects.create(
            pedido=pedido, produto=produto, descricao='Camisa', quantidade=100,
        )
        ordem = OrdemProducao.objects.create(
            filial=self.filial, pedido=pedido, item=item,
            status=OrdemProducao.Status.EM_PRODUCAO,
            numero='OP-0001', ano=2026, sequencial=1, quantidade=100,
        )
        hoje = timezone.localdate()
        EtapaOrdem.objects.create(
            ordem=ordem, etapa=EtapaOrdem.Etapa.CORTE, sequencia=4,
            status=EtapaOrdem.Status.CONCLUIDA, quantidade_produzida=100,
            data_conclusao=hoje - timedelta(days=30),
        )
        EtapaOrdem.objects.create(
            ordem=ordem, etapa=EtapaOrdem.Etapa.ESTAMPA, sequencia=5,
            status=EtapaOrdem.Status.PENDENTE,
        )

        resposta = self.client.get(reverse('moda:estoque-semiacabados'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'OP-0001')
        self.assertContains(resposta, '2000,00')
        self.assertContains(resposta, '30 dias')

    def test_a_rota_do_menu_cai_na_tela(self):
        from apps.moda.views_estoque_semiacabado import EstoqueSemiacabadoView

        for url in (
            reverse('moda:estoque-semiacabados'),
            reverse('moda:item', args=['estoque', 'semiacabados']),
        ):
            self.assertIs(resolve(url).func.view_class, EstoqueSemiacabadoView)

    def test_o_painel_de_wip_continua_sendo_outra_tela(self):
        """
        `indicadores/wip` responde "onde o trabalho está e o que travou";
        esta responde "quanto vale e há quanto tempo está parado".
        """
        wip = reverse('moda:item', args=['indicadores', 'wip'])

        self.assertNotEqual(wip, reverse('moda:estoque-semiacabados'))
        self.assertEqual(self.client.get(wip).status_code, 200)
