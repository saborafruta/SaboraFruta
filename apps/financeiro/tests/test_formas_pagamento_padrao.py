"""
Toda filial nasce podendo receber.

SEM FORMA DE PAGAMENTO O CAIXA NAO FECHA VENDA: a tela de finalizacao fica sem
botao, e o erro nao diz isso -- aparece uma lista vazia na hora de receber.
Exigir que alguem cadastre "Dinheiro" a mao antes da primeira venda e' atrito
puro, e o sintoma nao aponta para o cadastro.
"""
from django.apps import apps as registro
from django.test import TestCase

from apps.core.models import Empresa, Filial
from apps.financeiro.models import FormaPagamento
from apps.financeiro.services.formas_pagamento_padrao import (
    PADRAO, garantir_formas_padrao,
)


class FormasPadraoNaCriacaoDaFilialTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Formas LTDA', nome_fantasia='Formas',
            cnpj='63345678000191', segmento='polpa_frutas',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )

    def _filial(self, cnpj='63345678000272', cidade='Natal'):
        return Filial.objects.create(
            empresa=self.empresa, razao_social=f'Unidade {cidade}',
            cnpj=cnpj, uf='RN', cidade=cidade,
        )

    # ── Na criação ───────────────────────────────────────────────────────

    def test_filial_nova_ja_nasce_com_as_formas(self):
        filial = self._filial()

        formas = FormaPagamento.objects.filter(filial=filial)
        self.assertEqual(formas.count(), len(PADRAO))
        self.assertIn('Dinheiro', {f.descricao for f in formas})
        self.assertIn('PIX', {f.descricao for f in formas})

    def test_cada_filial_recebe_as_suas(self):
        """Forma e' da unidade: uma loja nao recebe pela forma de outra."""
        primeira = self._filial()
        segunda = self._filial(cnpj='31345678000677', cidade='Mossoro')

        self.assertEqual(FormaPagamento.objects.filter(filial=primeira).count(), len(PADRAO))
        self.assertEqual(FormaPagamento.objects.filter(filial=segunda).count(), len(PADRAO))

    def test_salvar_a_filial_de_novo_nao_duplica(self):
        filial = self._filial()
        filial.cidade = 'Parnamirim'
        filial.save(update_fields=['cidade'])

        self.assertEqual(FormaPagamento.objects.filter(filial=filial).count(), len(PADRAO))

    def test_o_cartao_de_credito_ja_vem_parcelando(self):
        filial = self._filial()

        credito = FormaPagamento.objects.get(
            filial=filial, descricao='Cartão de Crédito',
        )
        self.assertTrue(credito.gera_parcelas)
        self.assertEqual(credito.codigo_sefaz, '03')

    # ── Não pisa em cadastro feito à mão ─────────────────────────────────

    def test_filial_que_ja_tem_forma_propria_fica_como_esta(self):
        """
        Quem cuidou do cadastro foi uma pessoa, e recriar as padrao traria de
        volta a forma que ela desativou de proposito.
        """
        filial = self._filial()
        FormaPagamento.objects.filter(filial=filial).exclude(
            descricao='Dinheiro',
        ).delete()

        criadas = garantir_formas_padrao(filial)

        self.assertEqual(criadas, 0)
        self.assertEqual(FormaPagamento.objects.filter(filial=filial).count(), 1)

    # ── A migration que alcança quem já existia ──────────────────────────

    def test_a_migration_alcanca_filial_que_ficou_sem_forma(self):
        """
        A semeadura acontece na criacao; sem esta migration, so' filial nova
        sairia ganhando e as que ja' estao no banco continuariam sem nenhuma.
        """
        filial = self._filial()
        FormaPagamento.objects.filter(filial=filial).delete()

        self._rodar_migration()

        self.assertEqual(
            FormaPagamento.objects.filter(filial=filial).count(), len(PADRAO),
        )

    def test_a_migration_nao_mexe_em_quem_ja_tem(self):
        filial = self._filial()
        FormaPagamento.objects.filter(filial=filial).exclude(
            descricao='PIX',
        ).delete()

        self._rodar_migration()

        self.assertEqual(FormaPagamento.objects.filter(filial=filial).count(), 1)

    def test_rodar_a_migration_duas_vezes_nao_duplica(self):
        filial = self._filial()
        FormaPagamento.objects.filter(filial=filial).delete()

        self._rodar_migration()
        self._rodar_migration()

        self.assertEqual(
            FormaPagamento.objects.filter(filial=filial).count(), len(PADRAO),
        )

    @staticmethod
    def _rodar_migration():
        """
        Chama a funcao da migration com o registro real de modelos.

        AS MIGRATIONS NAO RODAM NA SUITE (`MIGRATION_MODULES` desliga todas, e
        o schema sai direto dos modelos), entao uma migration de DADO passaria
        sem ninguem nunca ter executado. `apps.get_model` tem a mesma
        assinatura do registro historico que ela recebe em producao.
        """
        from importlib import import_module

        modulo = import_module(
            'apps.financeiro.migrations.0055_formas_pagamento_padrao_das_filiais',
        )
        modulo.semear(registro, None)
