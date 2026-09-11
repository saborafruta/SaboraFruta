"""
Consumo de tecido pela grade: metros pelo peso de cada tamanho, em vez do
consumo fixo por peça digitado na ficha.

"Malha PV a camisa P - 145g, M - 176g..." é uma tabela do TECIDO + TIPO DE
PEÇA + GRADE (`PesoTecidoGrade`), não do produto: o mesmo peso vale para
qualquer item de OP que peça essa malha, tipo de peça e grade, com ou sem
produto de catálogo ligado. Estes testes cercam a conta (peso ×
gramatura/largura do tecido = metros) e os três lugares que passam a
usá-la: o painel de necessidade, a reserva automática ao começar a
produção, e a sugestão de planejado no corte.

SEMPRE COM FALLBACK. Sem malha ligada, sem tipo de peça, sem grade no
item, ou sem peso cadastrado para algum tamanho pedido, a conta velha
(consumo fixo × quantidade) continua valendo -- não é opcional, é o
comportamento de qualquer cliente que nunca cadastrou peso nenhum.
"""
from decimal import Decimal

from django.test import TestCase

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import Estoque
from apps.moda.models import (
    FichaTecnica, Grade, ItemCorte, ItemGrade, ItemGradePedido,
    ItemPedidoProducao, MaterialFicha, OrdemProducao, PedidoProducao,
    PesoTecidoGrade, ProdutoModa, RegistroCorte, Tamanho, Tecido,
)
from apps.moda.services.consumo_tecido import consumo_tecido_principal, tecido_da_malha
from apps.moda.services.necessidade import NecessidadeService
from apps.produtos.models import Produto, UnidadeMedida


class ConsumoTecidoPesoBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao Peso LTDA', nome_fantasia='Peso',
            cnpj='63345678000191', segmento='moda_confeccao',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='63345678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Cliente Peso', cpf_cnpj='12345678901',
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='M', descricao='Metro',
        )
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='peso@teste.local', nome='Fulano', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )

        # Malha PV: 200 g/m², rolo de 1,50 m de largura.
        cls.tecido_estoque = Produto.objects.create(
            filial=cls.filial, codigo='TEC001', descricao='Malha PV',
            unidade_medida=cls.unidade,
        )
        cls.tecido = Tecido.objects.create(
            filial=cls.filial, nome='PV', gramatura=200, largura_cm=Decimal('150'),
            produto_estoque=cls.tecido_estoque,
        )

        cls.grade = Grade.objects.create(filial=cls.filial, nome='Adulto')
        cls.p = Tamanho.objects.create(filial=cls.filial, sigla='P', ordem=10)
        cls.m = Tamanho.objects.create(filial=cls.filial, sigla='M', ordem=20)
        cls.g = Tamanho.objects.create(filial=cls.filial, sigla='G', ordem=30)
        ItemGrade.objects.create(grade=cls.grade, tamanho=cls.p, ordem=10)
        ItemGrade.objects.create(grade=cls.grade, tamanho=cls.m, ordem=20)
        ItemGrade.objects.create(grade=cls.grade, tamanho=cls.g, ordem=30)

        # Peso cadastrado pra "Camisa" em PV, grade Adulto -- SEM produto,
        # SEM ficha técnica. 145 g no P, 176 g no M, 193 g no G, do
        # exemplo do usuário.
        PesoTecidoGrade.objects.create(
            filial=cls.filial, tecido=cls.tecido, tipo_peca='Camisa',
            grade=cls.grade, tamanho=cls.p, peso_g=Decimal('145'),
        )
        PesoTecidoGrade.objects.create(
            filial=cls.filial, tecido=cls.tecido, tipo_peca='Camisa',
            grade=cls.grade, tamanho=cls.m, peso_g=Decimal('176'),
        )
        PesoTecidoGrade.objects.create(
            filial=cls.filial, tecido=cls.tecido, tipo_peca='Camisa',
            grade=cls.grade, tamanho=cls.g, peso_g=Decimal('193'),
        )

        # Produto e ficha só entram nos testes que precisam da reserva
        # real (o material tem que apontar pra um produto de estoque) --
        # o cálculo do peso em si não depende de nenhum dos dois.
        cls.produto = ProdutoModa.objects.create(
            filial=cls.filial, codigo='CAM001', nome='Camisa',
        )
        cls.ficha = FichaTecnica.objects.create(filial=cls.filial, produto=cls.produto)
        cls.material = MaterialFicha.objects.create(
            ficha=cls.ficha, tipo=MaterialFicha.Tipo.TECIDO_PRINCIPAL,
            descricao='Malha PV', consumo=Decimal('1.5'),
            produto_estoque=cls.tecido_estoque,
        )

    def _item(self, quantidades, numero=1, malha='PV', tipo_peca='Camisa',
              grade=True, produto=None):
        """Um item de OP com grade P/M/G, malha e tipo de peça nas
        observações, como a OP 2.0 grava -- com ou sem produto ligado."""
        total = sum(quantidades.values())
        pedido = PedidoProducao.objects.create(
            filial=self.filial, cliente=self.cliente, numero=numero,
        )
        observacoes = (
            f'Estrutura da peça:\nTipo de peça: {tipo_peca}\nMalha: {malha}\n'
            if tipo_peca else ''
        )
        item = ItemPedidoProducao.objects.create(
            pedido=pedido, produto=produto, descricao='Camisa',
            quantidade=total, valor_unitario=Decimal('50'),
            observacoes=observacoes,
            grade_tamanho=self.grade if grade else None,
        )
        for tamanho, qtd in quantidades.items():
            ItemGradePedido.objects.create(item=item, tamanho=tamanho, quantidade=qtd)
        return item

    def _ordem(self, quantidades, numero=1, malha='PV', tipo_peca='Camisa',
               grade=True, produto=None):
        item = self._item(
            quantidades, numero=numero, malha=malha, tipo_peca=tipo_peca,
            grade=grade, produto=produto if produto is not None else self.produto,
        )
        return OrdemProducao.objects.create(
            filial=self.filial, pedido=item.pedido, item=item,
            numero=f'OP-{numero:04d}', ano=2026, sequencial=numero,
            quantidade=item.quantidade,
        )


class ConsumoTecidoPrincipalTests(ConsumoTecidoPesoBase):
    """`consumo_tecido_principal` isolado, sem produto nenhum envolvido."""

    def test_calcula_metros_pelo_peso_da_grade(self):
        # 3 P (145g) + 2 M (176g) + 1 G (193g) = 980 g
        # 980 / 200 g/m2 / 1,5 m de largura = 3,2666... m
        item = self._item({self.p: 3, self.m: 2, self.g: 1}, produto=None)
        metros = consumo_tecido_principal(
            self.filial.pk, item, self.tecido,
            {self.p.pk: 3, self.m.pk: 2, self.g.pk: 1},
        )
        self.assertEqual(metros, Decimal('3.2667'))

    def test_funciona_sem_produto_de_catalogo(self):
        item = self._item({self.p: 1}, produto=None)
        self.assertIsNone(item.produto_id)
        metros = consumo_tecido_principal(
            self.filial.pk, item, self.tecido, {self.p.pk: 1},
        )
        self.assertEqual(metros, Decimal('0.4833'))

    def test_sem_gramatura_devolve_none(self):
        tecido_sem_dado = Tecido.objects.create(filial=self.filial, nome='SEM GRAMATURA')
        item = self._item({self.p: 1}, produto=None)
        metros = consumo_tecido_principal(
            self.filial.pk, item, tecido_sem_dado, {self.p.pk: 1},
        )
        self.assertIsNone(metros)

    def test_sem_grade_no_item_devolve_none(self):
        item = self._item({self.p: 1}, produto=None, grade=False)
        metros = consumo_tecido_principal(
            self.filial.pk, item, self.tecido, {self.p.pk: 1},
        )
        self.assertIsNone(metros)

    def test_sem_tipo_de_peca_nas_observacoes_devolve_none(self):
        item = self._item({self.p: 1}, produto=None, tipo_peca='')
        metros = consumo_tecido_principal(
            self.filial.pk, item, self.tecido, {self.p.pk: 1},
        )
        self.assertIsNone(metros)

    def test_tipo_de_peca_sem_peso_cadastrado_devolve_none(self):
        item = self._item({self.p: 1}, produto=None, tipo_peca='Bermuda')
        metros = consumo_tecido_principal(
            self.filial.pk, item, self.tecido, {self.p.pk: 1},
        )
        self.assertIsNone(metros)

    def test_sem_peso_cadastrado_para_tamanho_pedido_devolve_none(self):
        gg = Tamanho.objects.create(filial=self.filial, sigla='GG', ordem=40)
        item = self._item({self.p: 1, gg: 1}, produto=None)
        metros = consumo_tecido_principal(
            self.filial.pk, item, self.tecido, {self.p.pk: 1, gg.pk: 1},
        )
        self.assertIsNone(metros)

    def test_grade_errada_nao_usa_peso_de_outra_grade(self):
        outra_grade = Grade.objects.create(filial=self.filial, nome='Oversized')
        ItemGrade.objects.create(grade=outra_grade, tamanho=self.p, ordem=10)
        pedido = PedidoProducao.objects.create(filial=self.filial, cliente=self.cliente, numero=99)
        item = ItemPedidoProducao.objects.create(
            pedido=pedido, produto=None, descricao='Camisa', quantidade=1,
            valor_unitario=Decimal('50'),
            observacoes='Estrutura da peça:\nTipo de peça: Camisa\nMalha: PV\n',
            grade_tamanho=outra_grade,
        )
        metros = consumo_tecido_principal(
            self.filial.pk, item, self.tecido, {self.p.pk: 1},
        )
        self.assertIsNone(metros)

    def test_tamanho_com_quantidade_zero_nao_exige_peso(self):
        gg = Tamanho.objects.create(filial=self.filial, sigla='GG', ordem=40)
        item = self._item({self.p: 3, gg: 0}, produto=None)
        metros = consumo_tecido_principal(
            self.filial.pk, item, self.tecido, {self.p.pk: 3, gg.pk: 0},
        )
        self.assertIsNotNone(metros)


class TecidoDaMalhaTests(ConsumoTecidoPesoBase):

    def test_acha_pelo_nome_sem_diferenciar_maiusculas(self):
        tecido = tecido_da_malha(
            self.filial, 'Estrutura da peça:\nMalha: pv\n',
        )
        self.assertEqual(tecido, self.tecido)

    def test_sem_malha_nas_observacoes_devolve_none(self):
        self.assertIsNone(tecido_da_malha(self.filial, 'Sem estrutura nenhuma.'))


class NecessidadeServiceComPesoTests(ConsumoTecidoPesoBase):

    def test_previsto_usa_metros_pelo_peso_quando_disponivel(self):
        ordem = self._ordem({self.p: 3, self.m: 2, self.g: 1})

        linhas = NecessidadeService.calcular(self.filial, [ordem])

        self.assertEqual(len(linhas), 1)
        self.assertEqual(linhas[0].previsto, Decimal('3.2667'))

    def test_sem_malha_ligada_cai_no_consumo_fixo(self):
        ordem = self._ordem({self.p: 3, self.m: 2, self.g: 1}, malha='INEXISTENTE')

        linhas = NecessidadeService.calcular(self.filial, [ordem])

        # 1,5 m/peça (consumo fixo da ficha) × 6 peças = 9 m
        self.assertEqual(linhas[0].previsto, Decimal('9'))


class ReservarDaOrdemComPesoTests(ConsumoTecidoPesoBase):

    def test_reserva_a_quantidade_calculada_pelo_peso(self):
        Estoque.objects.create(
            produto=self.tecido_estoque, filial=self.filial,
            quantidade_atual=Decimal('100'), quantidade_disponivel=Decimal('100'),
        )
        ordem = self._ordem({self.p: 3, self.m: 2, self.g: 1})

        criadas = NecessidadeService.reservar_da_ordem(ordem, self.usuario)

        self.assertEqual(len(criadas), 1)
        self.assertEqual(criadas[0].quantidade, Decimal('3.2667'))


class PlanejadoDoCorteComPesoTests(ConsumoTecidoPesoBase):

    def test_planejado_usa_metros_pelo_peso_da_grade_do_corte(self):
        ordem = self._ordem({self.p: 3, self.m: 2, self.g: 1})
        corte = RegistroCorte.objects.create(
            filial=self.filial, ordem=ordem, tecido=self.tecido, quantidade=4,
        )
        ItemCorte.objects.create(corte=corte, tamanho=self.p, quantidade=3)
        ItemCorte.objects.create(corte=corte, tamanho=self.m, quantidade=1)

        # 3 P (145g) + 1 M (176g) = 611 g / 200 / 1,5 = 2,0366...
        self.assertEqual(corte.planejado_da_ficha, Decimal('2.0367'))

    def test_sem_grade_no_corte_cai_no_consumo_fixo_da_ficha(self):
        ordem = self._ordem({self.p: 3, self.m: 2, self.g: 1})
        corte = RegistroCorte.objects.create(
            filial=self.filial, ordem=ordem, tecido=self.tecido, quantidade=4,
        )

        # Sem ItemCorte nenhum: 1,5 m/peça × 4 peças deste corte = 6 m.
        self.assertEqual(corte.planejado_da_ficha, Decimal('6.0000'))
