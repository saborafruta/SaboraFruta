"""
Indicador de perdas — refugo, retrabalho e sobra de tecido.

O TESTE MAIS IMPORTANTE DESTE ARQUIVO é o da contagem dupla.
`QualidadeService.aplicar_no_fluxo` grava `quantidade_reprovada` da inspeção
dentro de `EtapaOrdem.perda`. Somar a inspeção por cima da etapa contaria a
mesma peça duas vezes — e a fábrica apareceria com o dobro do refugo que
tem, sem ninguém conseguir provar de onde veio a diferença.

O segundo é a separação entre refugo e retrabalho. O modelo de qualidade já
diz, na primeira linha do docstring, que retrabalho não é perda: a peça
voltou para a linha e ainda pode ser vendida. Se essa fronteira cair, o
indicador passa a punir a fábrica por ter recuperado peça.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import resolve, reverse
from django.utils import timezone

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial
from apps.moda.models import (
    Encaixe, EtapaOrdem, FichaTecnica, Inspecao, ItemInspecao, ItemPedidoProducao,
    MaterialFicha, OrdemProducao, PedidoProducao, ProdutoModa, RegistroCorte, Tecido,
)
from apps.moda.services.perdas import PerdasService


class PerdasBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao Perdas LTDA', nome_fantasia='Perdas',
            cnpj='53345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Confeccao Perdas LTDA',
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
        self.ordem = self._ordem()

    def _ordem(self, quantidade=100, sequencial=1):
        item = ItemPedidoProducao.objects.create(
            pedido=self.pedido, produto=self.produto,
            descricao='Camisa', quantidade=quantidade,
        )
        return OrdemProducao.objects.create(
            filial=self.filial, pedido=self.pedido, item=item,
            numero=f'OP-{sequencial:04d}', ano=2026, sequencial=sequencial,
            quantidade=quantidade,
        )

    def _etapa(self, nome, sequencia, produzido, perda=0, dias_atras=1, ordem=None):
        return EtapaOrdem.objects.create(
            ordem=ordem or self.ordem, etapa=nome, sequencia=sequencia,
            status=EtapaOrdem.Status.CONCLUIDA,
            quantidade_produzida=produzido, perda=perda,
            data_conclusao=timezone.localdate() - timedelta(days=dias_atras),
        )

    def _painel(self, dias=30):
        return PerdasService.painel(self.filial, dias)


class RefugoTests(PerdasBase):
    """Peças que morreram, por bancada."""

    def test_conta_a_perda_de_cada_bancada(self):
        self._etapa(EtapaOrdem.Etapa.CORTE, 4, produzido=95, perda=5)
        self._etapa(EtapaOrdem.Etapa.COSTURA, 6, produzido=92, perda=3)

        refugo = self._painel()['refugo']
        linhas = {l['etapa']: l for l in refugo['linhas']}

        self.assertEqual(refugo['pecas'], 8)
        self.assertEqual(linhas['corte']['perda'], 5)
        self.assertEqual(linhas['costura']['perda'], 3)

    def test_o_percentual_e_sobre_o_que_passou_pela_bancada(self):
        """
        95 boas e 5 perdidas são 5% — a base é a bancada, não o pedido
        inteiro. Medir contra o pedido faria a perda de uma etapa parecer
        menor só porque a ordem era grande.
        """
        self._etapa(EtapaOrdem.Etapa.CORTE, 4, produzido=95, perda=5)

        linha = self._painel()['refugo']['linhas'][0]

        self.assertEqual(linha['percentual'], Decimal('5.0'))

    def test_a_pior_bancada_e_a_de_maior_percentual(self):
        """
        Quem passa mil peças e perde dez está melhor do que quem passa
        vinte e perde cinco. Ordenar por número absoluto apontaria o setor
        errado para consertar.
        """
        self._etapa(EtapaOrdem.Etapa.CORTE, 4, produzido=990, perda=10)
        self._etapa(EtapaOrdem.Etapa.COSTURA, 6, produzido=15, perda=5)

        pior = self._painel()['refugo']['pior']

        self.assertEqual(pior['etapa'], 'costura')

    def test_as_linhas_saem_na_ordem_do_fluxo(self):
        """
        Perder no acabamento é muito mais caro do que perder no corte: a
        peça já consumiu todas as bancadas anteriores. A sequência é o que
        deixa isso visível.
        """
        self._etapa(EtapaOrdem.Etapa.ACABAMENTO, 7, produzido=90, perda=2)
        self._etapa(EtapaOrdem.Etapa.CORTE, 4, produzido=95, perda=5)

        self.assertEqual(
            [l['etapa'] for l in self._painel()['refugo']['linhas']],
            ['corte', 'acabamento'],
        )

    def test_etapa_administrativa_fica_de_fora(self):
        self._etapa(EtapaOrdem.Etapa.PLANEJAMENTO, 2, produzido=100, perda=9)
        self._etapa(EtapaOrdem.Etapa.CORTE, 4, produzido=95, perda=5)

        refugo = self._painel()['refugo']

        self.assertEqual([l['etapa'] for l in refugo['linhas']], ['corte'])
        self.assertEqual(refugo['pecas'], 5)

    def test_etapa_nao_concluida_nao_conta(self):
        etapa = self._etapa(EtapaOrdem.Etapa.CORTE, 4, produzido=95, perda=5)
        etapa.status = EtapaOrdem.Status.EM_ANDAMENTO
        etapa.save(update_fields=['status'])

        self.assertEqual(self._painel()['refugo']['pecas'], 0)

    def test_fora_da_janela_nao_conta(self):
        self._etapa(EtapaOrdem.Etapa.CORTE, 4, produzido=95, perda=5, dias_atras=60)

        self.assertEqual(self._painel(dias=30)['refugo']['pecas'], 0)
        self.assertEqual(self._painel(dias=90)['refugo']['pecas'], 5)

    def test_sem_perda_nenhuma_a_pior_bancada_e_none(self):
        self._etapa(EtapaOrdem.Etapa.CORTE, 4, produzido=100)

        refugo = self._painel()['refugo']

        self.assertEqual(refugo['pecas'], 0)
        self.assertIsNone(refugo['pior'])
        self.assertEqual(refugo['percentual'], Decimal('0.0'))


class ContagemDuplaTests(PerdasBase):
    """
    A inspeção aplicada no fluxo JÁ ESTÁ dentro de `EtapaOrdem.perda`.

    Este é o erro que mais custa caro neste indicador: silencioso, plausível
    e do tamanho exato de todas as inspeções do período.
    """

    def _inspecao(self, reprovada=0, retrabalho=0, aprovada=0, inspecionada=None,
                  status=Inspecao.Status.REPROVADO, dias_atras=1):
        return Inspecao.objects.create(
            filial=self.filial, ordem=self.ordem, status=status,
            data=timezone.localdate() - timedelta(days=dias_atras),
            quantidade_inspecionada=(
                inspecionada if inspecionada is not None
                else aprovada + reprovada + retrabalho
            ),
            quantidade_aprovada=aprovada,
            quantidade_reprovada=reprovada,
            quantidade_retrabalho=retrabalho,
            motivo='defeito de costura',
        )

    def test_o_refugo_da_inspecao_nao_e_somado_de_novo(self):
        """
        A etapa de Qualidade tem 7 de perda porque a inspeção reprovou 7 —
        foi `aplicar_no_fluxo` que gravou. O total tem de ser 7, e nunca 14.
        """
        self._etapa(EtapaOrdem.Etapa.QUALIDADE, 8, produzido=93, perda=7)
        self._inspecao(aprovada=93, reprovada=7)

        self.assertEqual(self._painel()['refugo']['pecas'], 7)

    def test_o_retrabalho_vem_so_da_inspecao(self):
        """
        `aplicar_no_fluxo` não leva retrabalho para a etapa de propósito — a
        peça vai ser inspecionada de novo. Então a inspeção é a única fonte,
        e aqui não há risco de duplicar.
        """
        self._etapa(EtapaOrdem.Etapa.QUALIDADE, 8, produzido=90, perda=5)
        self._inspecao(aprovada=90, reprovada=5, retrabalho=5, status=Inspecao.Status.RETRABALHO)

        painel = self._painel()

        self.assertEqual(painel['retrabalho']['pecas'], 5)
        self.assertEqual(painel['refugo']['pecas'], 5)

    def test_retrabalho_nunca_entra_no_refugo(self):
        """
        A peça voltou para a linha e ainda vai ser vendida. Somá-la ao
        refugo puniria a fábrica justamente por ter recuperado a peça.
        """
        self._inspecao(aprovada=80, retrabalho=20, status=Inspecao.Status.RETRABALHO)

        painel = self._painel()

        self.assertEqual(painel['refugo']['pecas'], 0)
        self.assertEqual(painel['retrabalho']['pecas'], 20)

    def test_inspecao_em_andamento_nao_conta(self):
        """Números de inspeção aberta ainda vão mudar."""
        self._inspecao(retrabalho=20, status=Inspecao.Status.EM_ANDAMENTO)

        self.assertEqual(self._painel()['retrabalho']['pecas'], 0)

    def test_o_percentual_de_retrabalho_e_sobre_o_inspecionado(self):
        self._inspecao(aprovada=90, retrabalho=10, status=Inspecao.Status.RETRABALHO)

        retrabalho = self._painel()['retrabalho']

        self.assertEqual(retrabalho['percentual'], Decimal('10.0'))
        self.assertEqual(retrabalho['inspecoes'], 1)
        self.assertEqual(retrabalho['com_retrabalho'], 1)

    def test_sem_inspecao_o_percentual_e_none_e_nao_zero(self):
        self.assertIsNone(self._painel()['retrabalho']['percentual'])


class CausasTests(PerdasBase):
    """Os pontos do checklist que mais reprovaram."""

    def _inspecao_com_pontos(self, pontos, dias_atras=1):
        inspecao = Inspecao.objects.create(
            filial=self.filial, ordem=self.ordem, status=Inspecao.Status.REPROVADO,
            data=timezone.localdate() - timedelta(days=dias_atras),
            quantidade_inspecionada=10, quantidade_reprovada=10, motivo='x',
        )
        for i, (ponto, resultado) in enumerate(pontos):
            ItemInspecao.objects.create(
                inspecao=inspecao, ponto=ponto, resultado=resultado,
                ordem_exibicao=i * 10,
            )
        return inspecao

    def test_conta_so_o_nao_conforme(self):
        self._inspecao_com_pontos([
            (ItemInspecao.Ponto.COSTURA, ItemInspecao.Resultado.NAO_CONFORME),
            (ItemInspecao.Ponto.COR, ItemInspecao.Resultado.CONFORME),
            (ItemInspecao.Ponto.ESTAMPA, ItemInspecao.Resultado.NAO_APLICA),
            (ItemInspecao.Ponto.TAMANHO, ItemInspecao.Resultado.PENDENTE),
        ])

        causas = self._painel()['causas']

        self.assertEqual([c['ponto'] for c in causas], ['costura'])
        self.assertEqual(causas[0]['ocorrencias'], 1)

    def test_ordenado_por_frequencia_e_nao_pela_ordem_do_checklist(self):
        """
        Aqui a pergunta é "o que atacar primeiro", e a resposta tem de estar
        no topo da lista.
        """
        for _ in range(3):
            self._inspecao_com_pontos([
                (ItemInspecao.Ponto.COSTURA, ItemInspecao.Resultado.NAO_CONFORME),
            ])
        self._inspecao_com_pontos([
            # TAMANHO vem antes de COSTURA no checklist, e mesmo assim tem
            # de sair depois: uma ocorrência contra três.
            (ItemInspecao.Ponto.TAMANHO, ItemInspecao.Resultado.NAO_CONFORME),
        ])

        causas = self._painel()['causas']

        self.assertEqual([c['ponto'] for c in causas], ['costura', 'tamanho'])
        self.assertEqual(causas[0]['barra'], 100)

    def test_fora_da_janela_nao_conta(self):
        self._inspecao_com_pontos(
            [(ItemInspecao.Ponto.COSTURA, ItemInspecao.Resultado.NAO_CONFORME)],
            dias_atras=60,
        )

        self.assertEqual(self._painel(dias=30)['causas'], [])
        self.assertEqual(len(self._painel(dias=90)['causas']), 1)


class TecidoTests(PerdasBase):
    """Metros que não viraram peça."""

    def setUp(self):
        super().setUp()
        self.tecido = Tecido.objects.create(filial=self.filial, nome='Malha Dry')

    def _corte(self, numero, consumo, aproveitamento=Decimal('0'),
               planejado=None, quantidade=100, dias_atras=1, tecido=None,
               status=RegistroCorte.Status.CORTADO):
        return RegistroCorte.objects.create(
            filial=self.filial, ordem=self.ordem, numero=numero, status=status,
            tecido=tecido or self.tecido, quantidade=quantidade,
            data=timezone.localdate() - timedelta(days=dias_atras),
            consumo_real=Decimal(consumo),
            consumo_planejado=None if planejado is None else Decimal(planejado),
            aproveitamento=Decimal(aproveitamento),
        )

    def test_metros_perdidos_saem_do_aproveitamento(self):
        """100 m a 90% de aproveitamento são 10 m que viraram retalho."""
        self._corte(1, consumo=100, aproveitamento=90)

        tecido = self._painel()['tecido']

        self.assertEqual(tecido['metros'], Decimal('10.00'))
        self.assertEqual(tecido['aproveitamento'], Decimal('90.0'))

    def test_a_media_de_aproveitamento_e_ponderada_pelo_tecido_gasto(self):
        """
        Um corte de 200 m e outro de 2 m não pesam igual: é o de 200 que
        decide o custo do mês. A média simples de 90% e 50% daria 70%; a
        ponderada dá 89,6%, que é a verdade.
        """
        self._corte(1, consumo=200, aproveitamento=90)
        self._corte(2, consumo=2, aproveitamento=50)

        self.assertEqual(self._painel()['tecido']['aproveitamento'], Decimal('89.6'))

    def test_corte_sem_medida_fica_de_fora_da_media_e_nao_entra_como_zero(self):
        """
        Zero ali significa "ninguém mediu", não "perdeu tudo". Entrar como
        0% arrastaria a média para baixo por falta de cadastro.
        """
        self._corte(1, consumo=100, aproveitamento=90)
        self._corte(2, consumo=100, aproveitamento=0)

        tecido = self._painel()['tecido']

        self.assertEqual(tecido['aproveitamento'], Decimal('90.0'))
        self.assertEqual(tecido['sem_medida'], 1)
        self.assertEqual(tecido['metros'], Decimal('10.00'))

    def test_a_variacao_compara_gasto_com_planejado(self):
        """Perda diferente da do aproveitamento: esta se conserta na mesa."""
        self._corte(1, consumo=110, planejado=100, aproveitamento=90)

        linha = self._painel()['tecido']['linhas'][0]

        self.assertEqual(linha['variacao'], Decimal('10.00'))
        self.assertTrue(linha['estourou'])

    def test_gastar_menos_que_o_planejado_nao_e_estouro(self):
        self._corte(1, consumo=90, planejado=100, aproveitamento=90)

        linha = self._painel()['tecido']['linhas'][0]

        self.assertEqual(linha['variacao'], Decimal('-10.00'))
        self.assertFalse(linha['estourou'])

    def test_o_planejado_cai_para_a_ficha_tecnica(self):
        """
        Sem consumo planejado no corte, sai da ficha: consumo do tecido
        principal × quantidade. Só o principal — forro e linha não saem do
        rolo que está na mesa.
        """
        ficha = FichaTecnica.objects.create(filial=self.filial, produto=self.produto)
        MaterialFicha.objects.create(
            ficha=ficha, tipo=MaterialFicha.Tipo.TECIDO_PRINCIPAL,
            descricao='Malha Dry', unidade=MaterialFicha.Unidade.METRO,
            consumo=Decimal('1.5'), custo_unitario=Decimal('18.00'),
        )
        MaterialFicha.objects.create(
            ficha=ficha, tipo=MaterialFicha.Tipo.AVIAMENTO,
            descricao='Linha', unidade=MaterialFicha.Unidade.METRO,
            consumo=Decimal('50'), custo_unitario=Decimal('0.01'),
        )
        self._corte(1, consumo=160, quantidade=100, aproveitamento=90)

        linha = self._painel()['tecido']['linhas'][0]

        self.assertEqual(linha['planejado'], Decimal('150.00'))
        self.assertEqual(linha['variacao'], Decimal('10.00'))

    def test_agrupa_por_tecido(self):
        outro = Tecido.objects.create(filial=self.filial, nome='Piquet')
        self._corte(1, consumo=100, aproveitamento=95)   # 5 m perdidos
        self._corte(2, consumo=50, aproveitamento=60, tecido=outro)  # 20 m

        linhas = self._painel()['tecido']['linhas']

        # Ordenado pelos METROS PERDIDOS, e não pelo tecido mais usado: é a
        # fila de quem revisar o encaixe primeiro. Por isso o Piquet, que
        # gastou metade, aparece na frente.
        self.assertEqual([l['label'] for l in linhas], ['Piquet', 'Malha Dry'])
        self.assertEqual(linhas[0]['perda_metros'], Decimal('20.00'))
        self.assertEqual(linhas[1]['perda_metros'], Decimal('5.00'))

    def test_corte_apenas_planejado_nao_conta(self):
        """Tecido só vira perda depois de passar na mesa."""
        self._corte(1, consumo=100, aproveitamento=90,
                    status=RegistroCorte.Status.PLANEJADO)

        self.assertEqual(self._painel()['tecido']['cortes'], 0)

    def test_fora_da_janela_nao_conta(self):
        self._corte(1, consumo=100, aproveitamento=90, dias_atras=60)

        self.assertEqual(self._painel(dias=30)['tecido']['cortes'], 0)
        self.assertEqual(self._painel(dias=90)['tecido']['cortes'], 1)

    def test_o_encaixe_manda_no_aproveitamento_digitado(self):
        """
        O do encaixe é calculado da área e não depende de alguém copiar o
        número certo para o corte.
        """
        encaixe = Encaixe.objects.create(
            filial=self.filial, produto=self.produto, nome='Encaixe A',
            largura_tecido=Decimal('1.60'), comprimento=Decimal('10'),
            quantidade_pecas=20, area_util=Decimal('12'),
        )
        corte = self._corte(1, consumo=100, aproveitamento=90)
        corte.encaixe = encaixe
        corte.save(update_fields=['encaixe'])

        # 12 m² de molde em 1,60 × 10 = 16 m² de tecido -> 75%.
        self.assertEqual(self._painel()['tecido']['aproveitamento'], Decimal('75.0'))

    def test_sem_corte_nenhum_nao_estoura(self):
        tecido = self._painel()['tecido']

        self.assertEqual(tecido['linhas'], [])
        self.assertEqual(tecido['metros'], Decimal('0.00'))
        self.assertIsNone(tecido['aproveitamento'])
        self.assertIsNone(tecido['pior'])


class TelaPerdasTests(TestCase):
    """
    A tela renderizando de verdade — os testes de cima nunca abrem o
    template, e erro de template não aparece neles.
    """

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
        resposta = self.client.get(reverse('moda:indicador-perdas'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Refugo por bancada')

    def test_a_tela_abre_com_as_tres_perdas(self):
        cliente = Cliente.objects.create(
            filial=self.filial, razao_social='Time', cpf_cnpj='12345678901',
        )
        produto = ProdutoModa.objects.create(
            filial=self.filial, codigo='CAM001', nome='Camisa',
        )
        pedido = PedidoProducao.objects.create(
            filial=self.filial, cliente=cliente, numero=1,
        )
        item = ItemPedidoProducao.objects.create(
            pedido=pedido, produto=produto, descricao='Camisa', quantidade=100,
        )
        ordem = OrdemProducao.objects.create(
            filial=self.filial, pedido=pedido, item=item,
            numero='OP-0001', ano=2026, sequencial=1, quantidade=100,
        )
        EtapaOrdem.objects.create(
            ordem=ordem, etapa=EtapaOrdem.Etapa.CORTE, sequencia=4,
            status=EtapaOrdem.Status.CONCLUIDA, quantidade_produzida=95,
            perda=5, data_conclusao=timezone.localdate(),
        )
        inspecao = Inspecao.objects.create(
            filial=self.filial, ordem=ordem, status=Inspecao.Status.RETRABALHO,
            data=timezone.localdate(), quantidade_inspecionada=95,
            quantidade_aprovada=88, quantidade_retrabalho=7, motivo='costura',
        )
        ItemInspecao.objects.create(
            inspecao=inspecao, ponto=ItemInspecao.Ponto.COSTURA,
            resultado=ItemInspecao.Resultado.NAO_CONFORME,
        )
        tecido = Tecido.objects.create(filial=self.filial, nome='Malha Dry')
        RegistroCorte.objects.create(
            filial=self.filial, ordem=ordem, numero=1,
            status=RegistroCorte.Status.CORTADO, tecido=tecido, quantidade=100,
            data=timezone.localdate(), consumo_real=Decimal('100'),
            consumo_planejado=Decimal('95'), aproveitamento=Decimal('90'),
        )

        resposta = self.client.get(reverse('moda:indicador-perdas'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Costura correta')
        self.assertContains(resposta, 'Malha Dry')

    def test_periodo_invalido_cai_no_padrao_em_vez_de_estourar(self):
        resposta = self.client.get(
            reverse('moda:indicador-perdas'), {'dias': 'trinta'},
        )

        self.assertEqual(resposta.status_code, 200)

    def test_a_rota_do_menu_cai_na_tela(self):
        from apps.moda.views_perdas import PerdasIndicadorView

        for url in (
            reverse('moda:indicador-perdas'),
            reverse('moda:item', args=['indicadores', 'perdas']),
        ):
            self.assertIs(resolve(url).func.view_class, PerdasIndicadorView)
