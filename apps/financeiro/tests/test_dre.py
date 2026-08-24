"""
DRE gerencial — o resultado do mês, montado a partir dos títulos.

É a tela em que um erro sai mais caro do sistema inteiro: ninguém confere um
resultado que parece plausível, e decisão de preço, de contratação e de
retirada saem daqui.

O que os testes cercam:

  · REGIME. Caixa e competência dão números diferentes para o mesmo mês, e
    misturá-los daria um número que não é nem um nem outro. Um mês pode ter
    caixa excelente e competência no vermelho;
  · MOVIMENTO SEM CATEGORIA. Um DRE que descarta em silêncio o que não foi
    classificado fecha bonito e está errado — e o erro cresce com o descuido
    do cadastro;
  · TÍTULO MORTO. Cancelado nunca foi resultado; devolvido deixou de ser;
    conta a pagar excluída foi apagada de propósito. Somar qualquer um deles
    infla os dois lados;
  · a ÁRVORE de categorias, que agrupa pela raiz — inclusive quando o
    cadastro tem um ciclo, que travaria a tela num laço infinito.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.cadastros.models import Cliente, Fornecedor
from apps.core.models import Empresa, Filial
from apps.financeiro.models import ContaPagar, ContaReceber, PlanoContas
from apps.financeiro.services.dre import CAIXA, COMPETENCIA, DREService


class DREBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao DRE LTDA', nome_fantasia='DRE',
            cnpj='53345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Confeccao DRE LTDA',
            cnpj='53345678000272', uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Time do Bairro',
            cpf_cnpj='12345678901',
        )
        cls.fornecedor = Fornecedor.objects.create(
            filial=cls.filial, razao_social='Malharia Sul',
            cpf_cnpj='53345678000353',
        )
        cls.mes = date(2026, 6, 1)
        cls.mes_anterior = date(2026, 5, 1)

    def setUp(self):
        self._n = 0

    # ── Montagem ─────────────────────────────────────────────────────────

    def _categoria(self, codigo, descricao, tipo, pai=None):
        return PlanoContas.objects.create(
            empresa=self.empresa, codigo=codigo, descricao=descricao,
            tipo=tipo, conta_pai=pai,
        )

    def _receber(self, valor, categoria=None, pago_em=None, competencia=None,
                 vencimento=None, status='pago'):
        self._n += 1
        valor = Decimal(valor)
        return ContaReceber.objects.create(
            filial=self.filial, cliente=self.cliente,
            documento_numero=f'R{self._n:04d}',
            data_emissao=vencimento or pago_em or self.mes,
            data_vencimento=vencimento or pago_em or self.mes,
            data_pagamento=pago_em, competencia=competencia,
            valor_original=valor, valor_final=valor,
            valor_pago=valor if status == 'pago' else Decimal('0'),
            valor_saldo=Decimal('0') if status == 'pago' else valor,
            status=status, plano_contas=categoria,
        )

    def _pagar(self, valor, categoria=None, pago_em=None, competencia=None,
               vencimento=None, status='pago', excluido=None):
        self._n += 1
        valor = Decimal(valor)
        return ContaPagar.objects.create(
            filial=self.filial, fornecedor=self.fornecedor,
            documento_numero=f'P{self._n:04d}',
            data_emissao=vencimento or pago_em or self.mes,
            data_vencimento=vencimento or pago_em or self.mes,
            data_pagamento=pago_em, data_competencia=competencia,
            valor_original=valor, valor_final=valor,
            valor_pago=valor if status == 'pago' else Decimal('0'),
            valor_saldo=Decimal('0') if status == 'pago' else valor,
            status=status, plano_contas=categoria, excluido_em=excluido,
        )

    def _painel(self, regime=CAIXA, mes=None):
        return DREService.painel(self.filial, mes or self.mes, regime)


class RegimeTests(DREBase):
    """Caixa e competência respondem perguntas diferentes."""

    def test_caixa_conta_o_que_foi_pago_no_mes(self):
        receita = self._categoria('1', 'Vendas', 'R')
        self._receber('10000', receita, pago_em=date(2026, 6, 10))

        resumo = self._painel(CAIXA)['resumo']

        self.assertEqual(resumo['receita'], Decimal('10000'))

    def test_caixa_ignora_o_que_ainda_nao_foi_pago(self):
        receita = self._categoria('1', 'Vendas', 'R')
        self._receber('10000', receita, vencimento=date(2026, 6, 10),
                      status='aberto')

        self.assertEqual(self._painel(CAIXA)['resumo']['receita'], Decimal('0'))

    def test_competencia_conta_o_titulo_em_aberto(self):
        """
        "Este mês deu lucro?" não espera o cliente pagar. Em competência o
        título vivo entra pela data dele.
        """
        receita = self._categoria('1', 'Vendas', 'R')
        self._receber('10000', receita, competencia=date(2026, 6, 1),
                      vencimento=date(2026, 7, 10), status='aberto')

        self.assertEqual(
            self._painel(COMPETENCIA)['resumo']['receita'], Decimal('10000'),
        )

    def test_competencia_em_branco_cai_no_vencimento(self):
        """
        Deixar o título de fora seria pior: sumiria dinheiro do relatório
        por falta de cadastro.
        """
        receita = self._categoria('1', 'Vendas', 'R')
        self._receber('7000', receita, competencia=None,
                      vencimento=date(2026, 6, 20), status='aberto')

        self.assertEqual(
            self._painel(COMPETENCIA)['resumo']['receita'], Decimal('7000'),
        )

    def test_os_dois_regimes_podem_discordar_no_mesmo_mes(self):
        """
        A premissa da tela: caixa excelente e competência no vermelho, por
        receber em junho o que se vendeu em maio.
        """
        receita = self._categoria('1', 'Vendas', 'R')
        despesa = self._categoria('2', 'Fornecedores', 'D')
        # Recebido em junho, mas foi venda de maio.
        self._receber('20000', receita, pago_em=date(2026, 6, 5),
                      competencia=date(2026, 5, 1))
        # Despesa de junho, paga em junho.
        self._pagar('9000', despesa, pago_em=date(2026, 6, 15),
                    competencia=date(2026, 6, 1))

        caixa = self._painel(CAIXA)['resumo']
        comp = self._painel(COMPETENCIA)['resumo']

        self.assertEqual(caixa['resultado'], Decimal('11000'))
        self.assertEqual(comp['receita'], Decimal('0'))
        self.assertEqual(comp['resultado'], Decimal('-9000'))

    def test_regime_invalido_cai_no_caixa(self):
        painel = DREService.painel(self.filial, self.mes, regime='qualquer')

        self.assertEqual(painel['regime'], CAIXA)


class SemCategoriaTests(DREBase):
    """A regra mais importante: o não classificado não pode sumir."""

    def test_titulo_sem_categoria_entra_no_total(self):
        receita = self._categoria('1', 'Vendas', 'R')
        self._receber('6000', receita, pago_em=date(2026, 6, 10))
        self._receber('4000', None, pago_em=date(2026, 6, 11))

        resumo = self._painel()['resumo']

        self.assertEqual(resumo['receita'], Decimal('10000'))
        self.assertEqual(resumo['sem_categoria'], Decimal('4000'))

    def test_o_sem_categoria_vira_linha_propria(self):
        self._receber('4000', None, pago_em=date(2026, 6, 11))

        bloco = next(
            b for b in self._painel()['linhas'] if b['chave'] == 'receita'
        )

        self.assertTrue(bloco['sem_categoria']['tem'])
        self.assertEqual(bloco['sem_categoria']['mes'], Decimal('4000'))

    def test_o_percentual_mede_quanto_do_relatorio_da_para_levar_a_serio(self):
        receita = self._categoria('1', 'Vendas', 'R')
        self._receber('7500', receita, pago_em=date(2026, 6, 10))
        self._receber('2500', None, pago_em=date(2026, 6, 11))

        self.assertEqual(
            self._painel()['resumo']['sem_categoria_pct'], Decimal('25.0'),
        )

    def test_tudo_classificado_nao_dispara_aviso(self):
        receita = self._categoria('1', 'Vendas', 'R')
        self._receber('10000', receita, pago_em=date(2026, 6, 10))

        self.assertEqual(self._painel()['resumo']['sem_categoria'], Decimal('0'))


class TitulosMortosTests(DREBase):
    """O que nunca foi resultado, ou deixou de ser."""

    def test_receber_cancelado_fica_de_fora(self):
        receita = self._categoria('1', 'Vendas', 'R')
        self._receber('10000', receita, pago_em=date(2026, 6, 10),
                      status='cancelado')

        self.assertEqual(self._painel()['resumo']['receita'], Decimal('0'))

    def test_receber_devolvido_fica_de_fora(self):
        """A venda existiu e depois deixou de existir."""
        receita = self._categoria('1', 'Vendas', 'R')
        self._receber('10000', receita, pago_em=date(2026, 6, 10),
                      status='devolvido')

        self.assertEqual(self._painel()['resumo']['receita'], Decimal('0'))

    def test_pagar_cancelado_fica_de_fora(self):
        despesa = self._categoria('2', 'Fornecedores', 'D')
        self._pagar('5000', despesa, pago_em=date(2026, 6, 10),
                    status='cancelado')

        self.assertEqual(self._painel()['resumo']['despesa'], Decimal('0'))

    def test_pagar_excluido_logicamente_fica_de_fora(self):
        """
        `excluido_em` é exclusão lógica: o título continua na tabela, e
        somá-lo cobraria do resultado uma despesa que alguém já apagou.
        """
        from django.utils import timezone

        despesa = self._categoria('2', 'Fornecedores', 'D')
        self._pagar('5000', despesa, pago_em=date(2026, 6, 10),
                    excluido=timezone.now())

        self.assertEqual(self._painel()['resumo']['despesa'], Decimal('0'))


class ArvoreTests(DREBase):
    """As categorias agrupadas pela raiz."""

    def test_a_filha_soma_na_raiz_e_aparece_detalhada(self):
        raiz = self._categoria('2', 'Despesas operacionais', 'D')
        agua = self._categoria('2.1', 'Água', 'D', pai=raiz)
        luz = self._categoria('2.2', 'Luz', 'D', pai=raiz)
        self._pagar('300', agua, pago_em=date(2026, 6, 5))
        self._pagar('700', luz, pago_em=date(2026, 6, 6))

        bloco = next(b for b in self._painel()['linhas'] if b['chave'] == 'despesa')
        linha = bloco['linhas'][0]

        self.assertEqual(linha['descricao'], 'Despesas operacionais')
        self.assertEqual(linha['mes'], Decimal('1000'))
        self.assertEqual(
            [(f['descricao'], f['total']) for f in linha['filhas']],
            [('Água', Decimal('300')), ('Luz', Decimal('700'))],
        )

    def test_neta_sobe_ate_a_raiz(self):
        raiz = self._categoria('2', 'Despesas', 'D')
        meio = self._categoria('2.1', 'Instalações', 'D', pai=raiz)
        neta = self._categoria('2.1.1', 'Energia', 'D', pai=meio)
        self._pagar('900', neta, pago_em=date(2026, 6, 5))

        bloco = next(b for b in self._painel()['linhas'] if b['chave'] == 'despesa')

        self.assertEqual(len(bloco['linhas']), 1)
        self.assertEqual(bloco['linhas'][0]['descricao'], 'Despesas')
        self.assertEqual(bloco['linhas'][0]['mes'], Decimal('900'))

    def test_ciclo_no_cadastro_nao_trava_a_tela(self):
        """
        `conta_pai` aponta para a própria tabela. Um ciclo (A filha de B, B
        filha de A) travaria a subida num laço infinito, e a tela nunca
        responderia.
        """
        a = self._categoria('9', 'A', 'D')
        b = self._categoria('9.1', 'B', 'D', pai=a)
        a.conta_pai = b
        a.save(update_fields=['conta_pai'])
        self._pagar('100', b, pago_em=date(2026, 6, 5))

        # Se travasse, o teste não terminaria.
        self.assertEqual(self._painel()['resumo']['despesa'], Decimal('100'))


class ComparacaoTests(DREBase):
    """Mês, mês anterior e acumulado do ano."""

    def test_compara_com_o_mes_anterior(self):
        receita = self._categoria('1', 'Vendas', 'R')
        self._receber('8000', receita, pago_em=date(2026, 5, 10))
        self._receber('10000', receita, pago_em=date(2026, 6, 10))

        bloco = next(b for b in self._painel()['linhas'] if b['chave'] == 'receita')
        linha = bloco['linhas'][0]

        self.assertEqual(linha['mes'], Decimal('10000'))
        self.assertEqual(linha['anterior'], Decimal('8000'))
        self.assertEqual(linha['variacao'], Decimal('2000'))
        self.assertEqual(linha['variacao_pct'], Decimal('25.0'))

    def test_categoria_que_sumiu_no_mes_aparece_zerada(self):
        """
        Sumi-la esconderia justamente a queda: a receita que existia mês
        passado e não existe mais é a informação que interessa.
        """
        receita = self._categoria('1', 'Vendas', 'R')
        outra = self._categoria('1.9', 'Fretes cobrados', 'R')
        self._receber('8000', receita, pago_em=date(2026, 6, 10))
        self._receber('2000', outra, pago_em=date(2026, 5, 10))

        bloco = next(b for b in self._painel()['linhas'] if b['chave'] == 'receita')
        por_nome = {l['descricao']: l for l in bloco['linhas']}

        self.assertIn('Fretes cobrados', por_nome)
        self.assertEqual(por_nome['Fretes cobrados']['mes'], Decimal('0'))
        self.assertEqual(por_nome['Fretes cobrados']['anterior'], Decimal('2000'))

    def test_o_acumulado_soma_o_ano_ate_o_mes(self):
        receita = self._categoria('1', 'Vendas', 'R')
        for mes in (1, 4, 6):
            self._receber('1000', receita, pago_em=date(2026, mes, 10))
        # Julho é depois do mês escolhido e não entra no acumulado.
        self._receber('9999', receita, pago_em=date(2026, 7, 10))

        resumo = self._painel()['resumo']

        self.assertEqual(resumo['receita_ano'], Decimal('3000'))
        self.assertEqual(resumo['receita'], Decimal('1000'))

    def test_o_resultado_e_receita_menos_despesa(self):
        receita = self._categoria('1', 'Vendas', 'R')
        despesa = self._categoria('2', 'Fornecedores', 'D')
        self._receber('10000', receita, pago_em=date(2026, 6, 10))
        self._pagar('6000', despesa, pago_em=date(2026, 6, 12))

        resumo = self._painel()['resumo']

        self.assertEqual(resumo['resultado'], Decimal('4000'))
        self.assertEqual(resumo['margem'], Decimal('40.0'))

    def test_prejuizo_sai_negativo(self):
        receita = self._categoria('1', 'Vendas', 'R')
        despesa = self._categoria('2', 'Fornecedores', 'D')
        self._receber('3000', receita, pago_em=date(2026, 6, 10))
        self._pagar('8000', despesa, pago_em=date(2026, 6, 12))

        self.assertEqual(self._painel()['resumo']['resultado'], Decimal('-5000'))

    def test_sem_receita_a_margem_e_none_e_nao_zero(self):
        despesa = self._categoria('2', 'Fornecedores', 'D')
        self._pagar('8000', despesa, pago_em=date(2026, 6, 12))

        self.assertIsNone(self._painel()['resumo']['margem'])

    def test_mes_vazio_nao_estoura(self):
        painel = self._painel()

        self.assertEqual(painel['resumo']['resultado'], Decimal('0'))
        self.assertIsNone(painel['resumo']['margem'])


class TelaDRETests(TestCase):
    """A tela renderizando de verdade."""

    @classmethod
    def setUpTestData(cls):
        from apps.core.models import PerfilAcesso, Usuario

        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao Tela LTDA', nome_fantasia='Tela',
            cnpj='53345678000191',
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
        resposta = self.client.get(reverse('financeiro:dre'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Nenhum título no período')

    def test_a_tela_abre_com_resultado(self):
        cliente = Cliente.objects.create(
            filial=self.filial, razao_social='Time', cpf_cnpj='12345678901',
        )
        categoria = PlanoContas.objects.create(
            empresa=self.empresa, codigo='1', descricao='Vendas', tipo='R',
        )
        ContaReceber.objects.create(
            filial=self.filial, cliente=cliente, documento_numero='R1',
            data_emissao=date(2026, 6, 1), data_vencimento=date(2026, 6, 10),
            data_pagamento=date(2026, 6, 10),
            valor_original=Decimal('10000'), valor_final=Decimal('10000'),
            valor_pago=Decimal('10000'), valor_saldo=Decimal('0'),
            status='pago', plano_contas=categoria,
        )

        resposta = self.client.get(reverse('financeiro:dre'), {'mes': '2026-06'})

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Vendas')
        self.assertContains(resposta, '10000,00')

    def test_a_tela_avisa_o_sem_categoria(self):
        cliente = Cliente.objects.create(
            filial=self.filial, razao_social='Time', cpf_cnpj='12345678901',
        )
        ContaReceber.objects.create(
            filial=self.filial, cliente=cliente, documento_numero='R1',
            data_emissao=date(2026, 6, 1), data_vencimento=date(2026, 6, 10),
            data_pagamento=date(2026, 6, 10),
            valor_original=Decimal('4000'), valor_final=Decimal('4000'),
            valor_pago=Decimal('4000'), valor_saldo=Decimal('0'),
            status='pago', plano_contas=None,
        )

        resposta = self.client.get(reverse('financeiro:dre'), {'mes': '2026-06'})

        self.assertContains(resposta, 'sem categoria')
        self.assertContains(resposta, 'Sem categoria')

    def test_mes_invalido_cai_no_mes_corrente_em_vez_de_estourar(self):
        """`?mes=` vem da barra de endereços e chega com qualquer coisa."""
        for texto in ('junho', '2026-13', '', 'x-y'):
            resposta = self.client.get(reverse('financeiro:dre'), {'mes': texto})

            self.assertEqual(resposta.status_code, 200)

    def test_o_regime_troca_pela_url(self):
        resposta = self.client.get(
            reverse('financeiro:dre'), {'mes': '2026-06', 'regime': 'competencia'},
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'tenha sido pago ou não')

    def test_nao_oferece_navegar_para_o_futuro(self):
        """Mês que ainda não aconteceu não tem resultado a mostrar."""
        from django.utils import timezone

        atual = timezone.localdate().strftime('%Y-%m')
        resposta = self.client.get(reverse('financeiro:dre'), {'mes': atual})

        self.assertFalse(resposta.context['tem_seguinte'])
