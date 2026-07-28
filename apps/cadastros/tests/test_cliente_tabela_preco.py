from django.test import TestCase

from apps.cadastros.forms import ClienteForm
from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial
from apps.produtos.models import TabelaPreco, TabelaPrecoFilial


class ClienteTabelaPrecoFormTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(
            razao_social='Empresa tabela LTDA',
            nome_fantasia='Empresa tabela',
            cnpj='12345678000190',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        self.filial = Filial.objects.create(
            empresa=self.empresa,
            razao_social='Filial tabela',
            nome_fantasia='Filial tabela',
            cnpj='12345678000191',
            uf='RN',
        )
        self.tabela = TabelaPreco.objects.create(
            filial=self.filial,
            descricao='Atacado',
            tipo=TabelaPreco.Tipo.ATACADO,
        )
        TabelaPrecoFilial.objects.create(
            tabela=self.tabela,
            filial=self.filial,
        )

    def test_exibe_padrao_e_tabelas_ativas_da_filial(self):
        form = ClienteForm(filial=self.filial)

        self.assertFalse(form.fields['tabela_preco'].required)
        self.assertEqual(
            list(form.fields['tabela_preco'].queryset),
            [self.tabela],
        )

    def test_salva_tabela_escolhida_no_cliente(self):
        form = ClienteForm(
            data={
                'tipo_pessoa': 'J',
                'tipo': 'atacado',
                'razao_social': 'Cliente com tabela',
                'tabela_preco': str(self.tabela.pk),
                'limite_credito': '0',
                'prazo_pagamento_dias': '0',
                'pais': 'Brasil',
                'codigo_pais_bacen': '1058',
            },
            filial=self.filial,
        )

        self.assertTrue(form.is_valid(), form.errors)
        cliente = form.save(commit=False)
        cliente.filial = self.filial
        cliente.save()
        self.assertEqual(
            Cliente.objects.get(pk=cliente.pk).tabela_preco,
            self.tabela,
        )
