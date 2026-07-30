"""
Receita realizada: Doação/Permuta não são faturamento.

`VendaPDV.valor_total` é o valor da venda, não a receita. Formas com
`movimenta_caixa=False` dão baixa no estoque mas não trazem dinheiro. Os
painéis somavam `valor_total` direto e inflavam o faturamento.
"""
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.financeiro.services.receita import (
    ajuste_por_cliente, ajuste_por_grupo, ajuste_total,
    formas_nao_contabilizadas, receita_total,
)


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class BaseReceita(TestCase):
    def setUp(self):
        from apps.cadastros.models import Cliente
        from apps.core.models import Empresa, Filial
        from apps.financeiro.models import FormaPagamento

        self.empresa = Empresa.objects.create(
            razao_social='T', cnpj='11222333000181',
            regime_tributario='simples', codigo_regime_tributario=1,
        )
        self.filial = Filial.objects.create(
            empresa=self.empresa, razao_social='M', nome_fantasia='M',
            cnpj='11222333000181', uf='RN', is_matriz=True,
        )
        self.dinheiro = FormaPagamento.objects.create(
            empresa=self.empresa, filial=self.filial, descricao='DINHEIRO',
            tipo='dinheiro', movimenta_caixa=True,
        )
        self.permuta = FormaPagamento.objects.create(
            empresa=self.empresa, filial=self.filial, descricao='PERMUTA',
            tipo='outros', movimenta_caixa=False,
        )
        self.doacao = FormaPagamento.objects.create(
            empresa=self.empresa, filial=self.filial, descricao='DOACAO',
            tipo='outros', movimenta_caixa=False,
        )
        self.cliente = Cliente.objects.create(
            filial=self.filial, razao_social='CLI', cpf_cnpj='12345678901', ativo=True,
        )
        self.usuario = self._operador()

    def _operador(self):
        """VendaPDV exige usuário (quem operou o caixa)."""
        from apps.core.models import PerfilAcesso, Usuario

        perfil = PerfilAcesso.objects.create(
            empresa=self.empresa, nome='Operador', is_admin=True,
        )
        return Usuario.objects.create_user(
            email='caixa@teste.local', nome='Caixa', password='x',
            empresa=self.empresa, perfil=perfil, filial=self.filial,
        )

    def _venda(self, total, pagamentos, cliente=None, numero=1):
        """Venda finalizada com `pagamentos` = [(forma, valor, troco)]."""
        from apps.pdv.models import PagamentoVendaPDV, VendaPDV

        venda = VendaPDV.objects.create(
            filial=self.filial, numero_venda=numero, status='finalizada',
            cliente=cliente, valor_total=Decimal(str(total)),
            data_venda=timezone.now(), usuario=self.usuario,
        )
        for forma, valor, troco in pagamentos:
            PagamentoVendaPDV.objects.create(
                venda_pdv=venda, forma_pagamento=forma,
                valor=Decimal(str(valor)), troco=Decimal(str(troco)),
            )
        return venda

    def _vendas(self):
        from apps.pdv.models import VendaPDV

        return VendaPDV.objects.filter(filial=self.filial, status='finalizada')


class AjusteTotalTests(BaseReceita):
    def test_venda_so_em_dinheiro_nao_tem_ajuste(self):
        self._venda(100, [(self.dinheiro, 100, 0)], numero=1)
        self.assertEqual(ajuste_total(self._vendas()), Decimal('0'))
        self.assertEqual(receita_total(self._vendas()), Decimal('100'))

    def test_venda_so_em_permuta_nao_gera_receita(self):
        self._venda(250, [(self.permuta, 250, 0)], numero=2)
        self.assertEqual(ajuste_total(self._vendas()), Decimal('250'))
        self.assertEqual(receita_total(self._vendas()), Decimal('0'))

    def test_doacao_tambem_e_descontada(self):
        self._venda(33, [(self.doacao, 33, 0)], numero=3)
        self.assertEqual(receita_total(self._vendas()), Decimal('0'))

    def test_venda_mista_desconta_apenas_a_parte_nao_contabilizada(self):
        """
        O caso mais delicado: metade em dinheiro, metade em permuta.

        A receita é só a parte que entrou de verdade — nem o total (inflado)
        nem zero (que jogaria fora dinheiro real).
        """
        self._venda(100, [(self.dinheiro, 60, 0), (self.permuta, 40, 0)], numero=4)
        self.assertEqual(ajuste_total(self._vendas()), Decimal('40'))
        self.assertEqual(receita_total(self._vendas()), Decimal('60'))

    def test_troco_e_descontado_do_ajuste(self):
        """
        Ajuste usa (valor − troco), igual ao fechamento de caixa: o troco
        devolvido nunca fez parte do pagamento.
        """
        self._venda(100, [(self.permuta, 130, 30)], numero=5)
        self.assertEqual(ajuste_total(self._vendas()), Decimal('100'))
        self.assertEqual(receita_total(self._vendas()), Decimal('0'))

    def test_varias_vendas_somam(self):
        self._venda(100, [(self.dinheiro, 100, 0)], numero=6)
        self._venda(200, [(self.permuta, 200, 0)], numero=7)
        self._venda(50, [(self.dinheiro, 20, 0), (self.doacao, 30, 0)], numero=8)

        self.assertEqual(ajuste_total(self._vendas()), Decimal('230'))
        self.assertEqual(receita_total(self._vendas()), Decimal('120'))

    def test_receita_nunca_fica_negativa(self):
        """Dado inconsistente não deve virar faturamento negativo no painel."""
        self._venda(10, [(self.permuta, 999, 0)], numero=9)
        self.assertEqual(receita_total(self._vendas()), Decimal('0'))

    def test_sem_vendas_devolve_zero(self):
        self.assertEqual(ajuste_total(self._vendas()), Decimal('0'))
        self.assertEqual(receita_total(self._vendas()), Decimal('0'))


class AjustePorGrupoTests(BaseReceita):
    def test_agrupa_por_ano_e_mes(self):
        agora = timezone.localtime()
        self._venda(100, [(self.permuta, 100, 0)], numero=10)

        ajustes = ajuste_por_grupo(
            self._vendas(),
            'venda_pdv__data_venda__year', 'venda_pdv__data_venda__month',
        )
        self.assertEqual(ajustes.get((agora.year, agora.month)), Decimal('100'))

    def test_chave_de_um_campo_vem_como_tupla(self):
        """Contrato do helper — o chamador não precisa de dois caminhos."""
        self._venda(80, [(self.permuta, 80, 0)], numero=11)
        ajustes = ajuste_por_grupo(self._vendas(), 'venda_pdv__filial_id')
        self.assertEqual(ajustes, {(self.filial.pk,): Decimal('80')})

    def test_sem_campos_e_erro_de_programacao(self):
        with self.assertRaises(ValueError):
            ajuste_por_grupo(self._vendas())

    def test_ajuste_por_cliente(self):
        self._venda(70, [(self.permuta, 70, 0)], cliente=self.cliente, numero=12)
        self.assertEqual(
            ajuste_por_cliente(self._vendas()), {self.cliente.pk: Decimal('70')},
        )

    def test_venda_sem_permuta_nao_aparece_no_agrupamento(self):
        self._venda(90, [(self.dinheiro, 90, 0)], numero=13)
        self.assertEqual(ajuste_por_grupo(self._vendas(), 'venda_pdv__filial_id'), {})


class FormasNaoContabilizadasTests(BaseReceita):
    def test_lista_apenas_as_que_nao_movimentam_caixa(self):
        formas = formas_nao_contabilizadas(self.empresa.pk)
        self.assertEqual(formas, {'PERMUTA', 'DOACAO'})

    def test_nao_vaza_de_outra_empresa(self):
        from apps.core.models import Empresa
        from apps.financeiro.models import FormaPagamento

        outra = Empresa.objects.create(
            razao_social='X', cnpj='99888777000166',
            regime_tributario='simples', codigo_regime_tributario=1,
        )
        FormaPagamento.objects.create(
            empresa=outra, descricao='CORTESIA', tipo='outros', movimenta_caixa=False,
        )
        self.assertNotIn('CORTESIA', formas_nao_contabilizadas(self.empresa.pk))
