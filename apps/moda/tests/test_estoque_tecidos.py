"""
Estoque de tecidos — saldo, consumo e cobertura.

A tela junta três fontes que moram em lugares diferentes, e é nas emendas
que ela pode mentir:

  · o SALDO é do estoque do ERP, por produto e filial. Ler o produto certo
    depende de um vínculo que pode não existir — e saldo desconhecido tem
    de sair como traço, nunca como zero, senão vira alarme falso diário;
  · o CONSUMO é da mesa de corte, e o corte herda o tecido do item da ordem
    quando não tem um próprio. Ler o campo cru deixaria de fora justamente
    os cortes que não pediram exceção;
  · a COBERTURA é o único número acionável da tela, e o mais fácil de
    estragar: sem consumo não existe cobertura infinita, existe "não dá
    para projetar".

Os valores são redondos: 300 m de saldo, 10 m/dia, 30 dias de cobertura.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import resolve, reverse
from django.utils import timezone

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial
from apps.estoque.models.estoque import Estoque
from apps.moda.models import (
    FichaTecnica, ItemPedidoProducao, MaterialFicha, OrdemProducao,
    PedidoProducao, ProdutoModa, RegistroCorte, Tecido,
)
from apps.moda.services.estoque_tecido import COBERTURA_CRITICA, EstoqueTecidoService
from apps.produtos.models import Produto, UnidadeMedida


class EstoqueTecidoBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao Rolo LTDA', nome_fantasia='Rolo',
            cnpj='53345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Confeccao Rolo LTDA',
            cnpj='53345678000272', uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Time', cpf_cnpj='12345678901',
        )
        # Tecido se mede em metro, e a unidade do produto de estoque tem de
        # ser a mesma: nao ha conversao no caminho.
        cls.metro = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='M', descricao='Metro',
            tipo=UnidadeMedida.Tipo.COMPRIMENTO,
        )

    def setUp(self):
        self.tecido = Tecido.objects.create(
            filial=self.filial, nome='Malha Dry', composicao='100% Poliester',
        )
        self.pedido = PedidoProducao.objects.create(
            filial=self.filial, cliente=self.cliente, numero=1,
        )

    # ── Montagem ─────────────────────────────────────────────────────────

    def _produto_estoque(self, codigo='TEC001', descricao='Malha Dry 1,60'):
        return Produto.objects.create(
            filial=self.filial, codigo=codigo, descricao=descricao,
            unidade_medida=self.metro,
        )

    def _saldo(self, produto, atual='300', reservado='0', custo='18'):
        return Estoque.objects.create(
            produto=produto, filial=self.filial,
            quantidade_atual=Decimal(atual),
            quantidade_reservada=Decimal(reservado),
            quantidade_disponivel=Decimal(atual) - Decimal(reservado),
            custo_medio=Decimal(custo),
        )

    def _produto_moda(self, codigo='CAM001', tecido=None):
        return ProdutoModa.objects.create(
            filial=self.filial, codigo=codigo, nome='Camisa',
            tecido=tecido if tecido is not None else self.tecido,
        )

    def _corte(self, consumo, numero=1, tecido=None, produto=None,
               dias_atras=1, status=RegistroCorte.Status.CORTADO):
        item = ItemPedidoProducao.objects.create(
            pedido=self.pedido, produto=produto, descricao='Camisa',
            quantidade=10, tecido=None,
        )
        ordem = OrdemProducao.objects.create(
            filial=self.filial, pedido=self.pedido, item=item,
            numero=f'OP-{numero:04d}', ano=2026, sequencial=numero, quantidade=10,
        )
        return RegistroCorte.objects.create(
            filial=self.filial, ordem=ordem, numero=numero, status=status,
            tecido=tecido, quantidade=10,
            data=timezone.localdate() - timedelta(days=dias_atras),
            consumo_real=Decimal(consumo),
        )

    def _painel(self, dias=30, busca=''):
        return EstoqueTecidoService.painel(self.filial, dias, busca)

    def _linha(self, dias=30):
        return self._painel(dias)['linhas'][0]


class VinculoTests(EstoqueTecidoBase):
    """De onde vem o saldo — e o que fazer quando não vem de lugar nenhum."""

    def test_o_vinculo_do_cadastro_traz_o_saldo(self):
        produto = self._produto_estoque()
        self._saldo(produto, atual='300', reservado='50', custo='18')
        self.tecido.produto_estoque = produto
        self.tecido.save(update_fields=['produto_estoque'])

        linha = self._linha()

        self.assertEqual(linha['origem'], 'direto')
        self.assertEqual(linha['saldo'], Decimal('300.00'))
        self.assertEqual(linha['reservado'], Decimal('50.00'))
        self.assertEqual(linha['disponivel'], Decimal('250.00'))
        self.assertEqual(linha['valor'], Decimal('5400.00'))
        self.assertFalse(linha['deduzido'])

    def test_sem_vinculo_o_saldo_e_traco_e_nao_zero(self):
        """
        Zero diria "acabou o tecido" e criaria um alarme falso por dia —
        e alarme falso diário é como o alarme de verdade passa batido.
        """
        linha = self._linha()

        self.assertFalse(linha['ligado'])
        self.assertIsNone(linha['saldo'])
        self.assertIsNone(linha['disponivel'])
        self.assertIsNone(linha['cobertura'])
        self.assertIsNone(linha['valor'])
        # E não pode ser confundido com "sei que acabou".
        self.assertFalse(linha['sem_saldo'])

    def test_saldo_zerado_e_diferente_de_saldo_desconhecido(self):
        produto = self._produto_estoque()
        self._saldo(produto, atual='0')
        self.tecido.produto_estoque = produto
        self.tecido.save(update_fields=['produto_estoque'])

        linha = self._linha()

        self.assertTrue(linha['ligado'])
        self.assertTrue(linha['sem_saldo'])
        self.assertEqual(linha['disponivel'], Decimal('0.00'))

    def test_sem_vinculo_no_cadastro_o_saldo_e_deduzido_pela_ficha(self):
        """
        Ponte provisória, para a tela já servir antes de alguém preencher o
        cadastro novo. Vem marcada, porque pode estar olhando outro produto.
        """
        produto_estoque = self._produto_estoque()
        self._saldo(produto_estoque, atual='300')
        produto = self._produto_moda()
        ficha = FichaTecnica.objects.create(filial=self.filial, produto=produto)
        MaterialFicha.objects.create(
            ficha=ficha, tipo=MaterialFicha.Tipo.TECIDO_PRINCIPAL,
            descricao='Malha Dry', unidade=MaterialFicha.Unidade.METRO,
            consumo=Decimal('1'), custo_unitario=Decimal('18'),
            produto_estoque=produto_estoque,
        )

        linha = self._linha()

        self.assertEqual(linha['origem'], 'ficha')
        self.assertTrue(linha['deduzido'])
        self.assertEqual(linha['saldo'], Decimal('300.00'))

    def test_o_vinculo_do_cadastro_vence_o_deduzido(self):
        """
        O do cadastro é do PRÓPRIO tecido; o da ficha é de um produto que
        por acaso o usa. Quando os dois existem, não há dúvida.
        """
        do_cadastro = self._produto_estoque(codigo='TEC001')
        da_ficha = self._produto_estoque(codigo='TEC002', descricao='Outro rolo')
        self._saldo(do_cadastro, atual='300')
        self._saldo(da_ficha, atual='999')
        self.tecido.produto_estoque = do_cadastro
        self.tecido.save(update_fields=['produto_estoque'])

        produto = self._produto_moda()
        ficha = FichaTecnica.objects.create(filial=self.filial, produto=produto)
        MaterialFicha.objects.create(
            ficha=ficha, tipo=MaterialFicha.Tipo.TECIDO_PRINCIPAL,
            descricao='Malha Dry', unidade=MaterialFicha.Unidade.METRO,
            consumo=Decimal('1'), custo_unitario=Decimal('18'),
            produto_estoque=da_ficha,
        )

        linha = self._linha()

        self.assertEqual(linha['origem'], 'direto')
        self.assertEqual(linha['saldo'], Decimal('300.00'))

    def test_aviamento_da_ficha_nao_serve_de_ponte(self):
        """Zíper não é tecido: deduzir dele apontaria o saldo errado."""
        produto_estoque = self._produto_estoque()
        self._saldo(produto_estoque, atual='300')
        produto = self._produto_moda()
        ficha = FichaTecnica.objects.create(filial=self.filial, produto=produto)
        MaterialFicha.objects.create(
            ficha=ficha, tipo=MaterialFicha.Tipo.AVIAMENTO,
            descricao='Zíper', unidade=MaterialFicha.Unidade.UNIDADE,
            consumo=Decimal('1'), custo_unitario=Decimal('3'),
            produto_estoque=produto_estoque,
        )

        self.assertFalse(self._linha()['ligado'])

    def test_o_saldo_e_da_filial_ativa(self):
        """
        O produto é do catálogo da empresa, mas o rolo está num lugar só.
        Somar o saldo de outra filial diria que há tecido onde não há.
        """
        outra = Filial.objects.create(
            empresa=self.empresa, razao_social='Filial 2',
            cnpj='53345678000353', uf='RN', cidade='Mossoro',
        )
        produto = self._produto_estoque()
        Estoque.objects.create(
            produto=produto, filial=outra, quantidade_atual=Decimal('999'),
            quantidade_disponivel=Decimal('999'), custo_medio=Decimal('18'),
        )
        self.tecido.produto_estoque = produto
        self.tecido.save(update_fields=['produto_estoque'])

        self.assertIsNone(self._linha()['saldo'])


class ConsumoTests(EstoqueTecidoBase):
    """Metros que saíram do rolo, medidos na mesa."""

    def setUp(self):
        super().setUp()
        self.produto_estoque = self._produto_estoque()
        self._saldo(self.produto_estoque, atual='300')
        self.tecido.produto_estoque = self.produto_estoque
        self.tecido.save(update_fields=['produto_estoque'])

    def test_soma_o_consumo_real_dos_cortes(self):
        self._corte(consumo=200, numero=1, tecido=self.tecido)
        self._corte(consumo=100, numero=2, tecido=self.tecido)

        linha = self._linha(dias=30)

        self.assertEqual(linha['consumo'], Decimal('300.00'))
        self.assertEqual(linha['cortes'], 2)
        self.assertEqual(linha['consumo_dia'], Decimal('10.00'))

    def test_o_corte_que_herda_o_tecido_do_produto_tambem_conta(self):
        """
        O corte herda o tecido do item da ordem quando não tem um próprio.
        Ler o campo cru deixaria de fora justamente os cortes normais — os
        que não pediram exceção.
        """
        produto = self._produto_moda()
        self._corte(consumo=150, numero=1, tecido=None, produto=produto)

        self.assertEqual(self._linha()['consumo'], Decimal('150.00'))

    def test_corte_apenas_planejado_nao_conta(self):
        """Tecido só saiu do rolo depois de passar na mesa."""
        self._corte(consumo=200, numero=1, tecido=self.tecido,
                    status=RegistroCorte.Status.PLANEJADO)

        self.assertEqual(self._linha()['consumo'], Decimal('0.00'))

    def test_fora_da_janela_nao_conta(self):
        self._corte(consumo=300, numero=1, tecido=self.tecido, dias_atras=60)

        self.assertEqual(self._painel(dias=30)['linhas'][0]['consumo'], Decimal('0.00'))
        self.assertEqual(self._painel(dias=90)['linhas'][0]['consumo'], Decimal('300.00'))


class CoberturaTests(EstoqueTecidoBase):
    """O único número desta tela que pede ação hoje."""

    def setUp(self):
        super().setUp()
        self.produto_estoque = self._produto_estoque()
        self.tecido.produto_estoque = self.produto_estoque
        self.tecido.save(update_fields=['produto_estoque'])

    def test_cobertura_e_disponivel_dividido_pelo_consumo_diario(self):
        """300 m disponíveis a 10 m/dia são 30 dias."""
        self._saldo(self.produto_estoque, atual='300')
        self._corte(consumo=300, numero=1, tecido=self.tecido)

        self.assertEqual(self._linha(dias=30)['cobertura'], 30)

    def test_metro_parado_nao_e_o_numero_que_importa(self):
        """
        Os dois tecidos têm 150 m. Um dura 30 dias, o outro 3 — e é a
        cobertura que diz qual vai parar a linha.
        """
        folgado = self._produto_estoque(codigo='TEC002', descricao='Piquet')
        outro = Tecido.objects.create(
            filial=self.filial, nome='Piquet', produto_estoque=folgado,
        )
        self._saldo(self.produto_estoque, atual='150')
        self._saldo(folgado, atual='150')
        self._corte(consumo=1500, numero=1, tecido=self.tecido)
        self._corte(consumo=150, numero=2, tecido=outro)

        linhas = {l['nome']: l for l in self._painel(dias=30)['linhas']}

        self.assertEqual(linhas['Malha Dry']['cobertura'], 3)
        self.assertEqual(linhas['Piquet']['cobertura'], 30)

    def test_sem_consumo_nao_ha_cobertura_infinita(self):
        """
        Tecido parado não "dura para sempre" — ele só não está sendo usado.
        Fingir cobertura infinita o esconderia da tela.
        """
        self._saldo(self.produto_estoque, atual='300')

        self.assertIsNone(self._linha()['cobertura'])

    def test_a_reserva_entra_na_conta(self):
        """
        A cobertura é sobre o DISPONÍVEL: metro já reservado para outra
        ordem não vai cobrir a próxima.
        """
        self._saldo(self.produto_estoque, atual='300', reservado='150')
        self._corte(consumo=300, numero=1, tecido=self.tecido)

        self.assertEqual(self._linha(dias=30)['cobertura'], 15)

    def test_a_fila_e_pela_cobertura_e_o_sem_vinculo_vai_para_o_fim(self):
        """
        Tecido sem vínculo não é urgência, é cadastro faltando — misturá-lo
        com o que está acabando de verdade atrapalha a leitura.
        """
        folgado = self._produto_estoque(codigo='TEC002', descricao='Piquet')
        piquet = Tecido.objects.create(
            filial=self.filial, nome='Piquet', produto_estoque=folgado,
        )
        Tecido.objects.create(filial=self.filial, nome='Sem vinculo')
        self._saldo(self.produto_estoque, atual='30')
        self._saldo(folgado, atual='300')
        self._corte(consumo=300, numero=1, tecido=self.tecido)
        self._corte(consumo=300, numero=2, tecido=piquet)

        nomes = [l['nome'] for l in self._painel(dias=30)['linhas']]

        self.assertEqual(nomes, ['Malha Dry', 'Piquet', 'Sem vinculo'])


class ResumoTests(EstoqueTecidoBase):

    def test_conta_os_criticos_e_aponta_o_primeiro_a_acabar(self):
        apertado = self._produto_estoque(codigo='TEC001')
        folgado = self._produto_estoque(codigo='TEC002', descricao='Piquet')
        piquet = Tecido.objects.create(
            filial=self.filial, nome='Piquet', produto_estoque=folgado,
        )
        self.tecido.produto_estoque = apertado
        self.tecido.save(update_fields=['produto_estoque'])
        self._saldo(apertado, atual='30', custo='18')
        self._saldo(folgado, atual='300', custo='10')
        self._corte(consumo=300, numero=1, tecido=self.tecido)
        self._corte(consumo=300, numero=2, tecido=piquet)

        resumo = self._painel(dias=30)['resumo']

        self.assertEqual(resumo['tecidos'], 2)
        self.assertEqual(resumo['criticos'], 1)
        self.assertEqual(resumo['pior']['nome'], 'Malha Dry')
        self.assertLess(resumo['pior']['cobertura'], COBERTURA_CRITICA)
        # 30 × 18 + 300 × 10 = 3540
        self.assertEqual(resumo['valor'], Decimal('3540.00'))
        self.assertEqual(resumo['consumo'], Decimal('600.00'))

    def test_conta_quantos_estao_sem_vinculo(self):
        Tecido.objects.create(filial=self.filial, nome='Piquet')

        resumo = self._painel()['resumo']

        self.assertEqual(resumo['sem_vinculo'], 2)
        self.assertEqual(resumo['valor'], Decimal('0.00'))
        self.assertIsNone(resumo['pior'])

    def test_a_busca_filtra_pelo_nome(self):
        Tecido.objects.create(filial=self.filial, nome='Piquet')

        self.assertEqual(
            [l['nome'] for l in self._painel(busca='piqu')['linhas']], ['Piquet'],
        )

    def test_tecido_inativo_nao_aparece(self):
        self.tecido.ativo = False
        self.tecido.save(update_fields=['ativo'])

        self.assertEqual(self._painel()['linhas'], [])

    def test_sem_tecido_nenhum_nao_estoura(self):
        self.tecido.delete()

        painel = self._painel()

        self.assertEqual(painel['linhas'], [])
        self.assertIsNone(painel['resumo']['pior'])
        self.assertEqual(painel['resumo']['valor'], Decimal('0.00'))


class TelaEstoqueTecidoTests(TestCase):
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
        resposta = self.client.get(reverse('moda:estoque-tecidos'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Nenhum tecido cadastrado')

    def test_a_tela_abre_com_saldo_e_cobertura(self):
        metro = UnidadeMedida.objects.create(
            empresa=self.empresa, sigla='M', descricao='Metro',
            tipo=UnidadeMedida.Tipo.COMPRIMENTO,
        )
        produto = Produto.objects.create(
            filial=self.filial, codigo='TEC001', descricao='Malha Dry 1,60',
            unidade_medida=metro,
        )
        Estoque.objects.create(
            produto=produto, filial=self.filial,
            quantidade_atual=Decimal('300'), quantidade_reservada=Decimal('0'),
            quantidade_disponivel=Decimal('300'), custo_medio=Decimal('18'),
        )
        Tecido.objects.create(
            filial=self.filial, nome='Malha Dry', composicao='100% Poliester',
            gramatura=140, produto_estoque=produto,
        )

        resposta = self.client.get(reverse('moda:estoque-tecidos'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Malha Dry')
        self.assertContains(resposta, '300,00 m')

    def test_a_tela_avisa_quando_falta_vinculo(self):
        Tecido.objects.create(filial=self.filial, nome='Malha Dry')

        resposta = self.client.get(reverse('moda:estoque-tecidos'))

        self.assertContains(resposta, 'sem ligação com um produto de estoque')

    def test_periodo_invalido_cai_no_padrao_em_vez_de_estourar(self):
        resposta = self.client.get(
            reverse('moda:estoque-tecidos'), {'dias': 'trinta'},
        )

        self.assertEqual(resposta.status_code, 200)

    def test_a_rota_do_menu_cai_na_tela(self):
        from apps.moda.views_estoque_tecido import EstoqueTecidoView

        for url in (
            reverse('moda:estoque-tecidos'),
            reverse('moda:item', args=['estoque', 'tecidos']),
        ):
            self.assertIs(resolve(url).func.view_class, EstoqueTecidoView)
