"""
Ordem do card "Clientes para Contato Hoje" no dashboard.

O card é ordenado por urgência (dias até a recompra), não pelo score de
prioridade: ordenar por score enterrava o cliente que vence HOJE abaixo de
outros que vencem em 2 dias, só porque tinham ticket maior — o que contraria
o próprio nome do card.
"""
from decimal import Decimal

from django.test import TestCase


class OrdemContatoHojeTests(TestCase):
    def setUp(self):
        from apps.core.models import Empresa, Filial

        self.empresa = Empresa.objects.create(
            razao_social='T', cnpj='11222333000181',
            regime_tributario='simples', codigo_regime_tributario=1,
        )
        self.filial = Filial.objects.create(
            empresa=self.empresa, razao_social='M', nome_fantasia='M',
            cnpj='11222333000181', uf='RN', is_matriz=True,
        )

    def _registro(self, nome, dias, score, status=None):
        """Cliente + registro de recompra com `dias` até a próxima compra."""
        from apps.cadastros.models import Cliente
        from apps.crm.models import RecompraCliente

        cliente = Cliente.objects.create(
            filial=self.filial, razao_social=nome, cpf_cnpj=nome[:11], ativo=True,
        )
        if status is None:
            status = (
                RecompraCliente.Status.VERMELHO if dias < 0
                else RecompraCliente.Status.AMARELO
            )
        return RecompraCliente.objects.create(
            cliente=cliente, filial=self.filial, dias_restantes=dias,
            status=status, score=score, qtd_compras=5,
            media_intervalo_dias=Decimal('7'), desvio_padrao_dias=Decimal('1'),
            valor_medio=Decimal('100'), valor_total_periodo=Decimal('500'),
            frequencia=RecompraCliente.Frequencia.SEMANAL,
            nivel_confianca=Decimal('0.8'),
        )

    def _nomes(self):
        from apps.core.views.dashboard import DashboardView

        dados = DashboardView._clientes_recompra(DashboardView(), self.filial)
        self.assertIsNone(dados['erro'], dados['erro'])
        return [r.cliente.razao_social for r in dados['itens']]

    def test_hoje_vem_antes_de_quem_vence_depois(self):
        """O caso reportado: score alto em 2 dias nao pode passar na frente."""
        self._registro('DOIS DIAS A', dias=2, score=46)
        self._registro('DOIS DIAS B', dias=2, score=40)
        self._registro('HOJE', dias=0, score=10)

        self.assertEqual(self._nomes()[0], 'HOJE')

    def test_atrasado_vem_antes_de_hoje(self):
        """Quem ja passou da data e mais urgente que quem vence hoje."""
        self._registro('HOJE', dias=0, score=90)
        self._registro('ATRASADO', dias=-3, score=10)

        self.assertEqual(self._nomes()[:2], ['ATRASADO', 'HOJE'])

    def test_mais_atrasado_primeiro(self):
        self._registro('ATRASO 1', dias=-1, score=50)
        self._registro('ATRASO 9', dias=-9, score=50)

        self.assertEqual(self._nomes()[:2], ['ATRASO 9', 'ATRASO 1'])

    def test_sequencia_completa_de_dias(self):
        for dias in (3, 1, 0, 2, -2):
            self._registro(f'D{dias}', dias=dias, score=50)

        self.assertEqual(self._nomes(), ['D-2', 'D0', 'D1', 'D2', 'D3'])

    def test_score_desempata_dentro_do_mesmo_dia(self):
        """Mesmo dia: o de maior valor primeiro."""
        self._registro('MENOR', dias=1, score=20)
        self._registro('MAIOR', dias=1, score=80)

        self.assertEqual(self._nomes()[:2], ['MAIOR', 'MENOR'])

    def test_cinza_sem_previsao_nao_entra_no_card(self):
        """Sem padrao definido nao ha o que cobrar."""
        from apps.crm.models import RecompraCliente

        self._registro('HOJE', dias=0, score=10)
        self._registro(
            'SEM PADRAO', dias=None, score=0, status=RecompraCliente.Status.CINZA,
        )
        self.assertEqual(self._nomes(), ['HOJE'])
