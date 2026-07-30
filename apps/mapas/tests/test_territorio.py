"""Territórios (§11): polígono, ponto-em-polígono, atribuição e indicadores."""
from django.test import TestCase


class PoligonoTests(TestCase):
    """Geometria pura — sem banco."""

    def _praca(self, pontos=None):
        from apps.cadastros.models import Praca

        praca = Praca(nome='Zona Sul')
        if pontos is not None:
            praca.definir_poligono(pontos)
        return praca

    #: quadrado de 0,1 grau em torno de Natal
    QUADRADO = [[-5.80, -35.25], [-5.80, -35.15], [-5.90, -35.15], [-5.90, -35.25]]

    def test_bbox_e_derivada_do_poligono(self):
        praca = self._praca(self.QUADRADO)
        self.assertEqual(praca.bbox_sul, -5.90)
        self.assertEqual(praca.bbox_norte, -5.80)
        self.assertEqual(praca.bbox_oeste, -35.25)
        self.assertEqual(praca.bbox_leste, -35.15)

    def test_ponto_dentro_e_fora(self):
        praca = self._praca(self.QUADRADO)
        self.assertTrue(praca.contem_ponto(-5.85, -35.20))    # centro
        self.assertFalse(praca.contem_ponto(-5.85, -35.30))   # a oeste
        self.assertFalse(praca.contem_ponto(-5.70, -35.20))   # ao norte

    def test_poligono_com_menos_de_tres_pontos_e_invalido(self):
        praca = self._praca([[-5.80, -35.25], [-5.90, -35.15]])
        self.assertFalse(praca.tem_poligono)
        self.assertIsNone(praca.poligono)
        self.assertIsNone(praca.bbox_sul)

    def test_pontos_invalidos_sao_descartados(self):
        """Payload do front pode vir com lixo; não deve explodir."""
        praca = self._praca(self.QUADRADO + [['x', 'y'], None, [1]])
        self.assertTrue(praca.tem_poligono)
        self.assertEqual(len(praca.poligono), 4)

    def test_sem_poligono_nao_contem_nada(self):
        self.assertFalse(self._praca().contem_ponto(-5.85, -35.20))

    def test_redefinir_com_lista_vazia_limpa_bbox(self):
        praca = self._praca(self.QUADRADO)
        praca.definir_poligono([])
        self.assertFalse(praca.tem_poligono)
        self.assertIsNone(praca.bbox_norte)

    def test_poligono_concavo_em_L(self):
        """Ray casting tem de acertar concavidade — bbox sozinha erraria."""
        forma_l = [
            [-5.80, -35.25], [-5.80, -35.20], [-5.85, -35.20],
            [-5.85, -35.15], [-5.90, -35.15], [-5.90, -35.25],
        ]
        praca = self._praca(forma_l)
        self.assertTrue(praca.contem_ponto(-5.88, -35.17))   # no pé do L
        # Dentro da bbox, mas fora do L (o "vão" do L).
        self.assertFalse(praca.contem_ponto(-5.81, -35.17))


class AtribuicaoTests(TestCase):
    """Atribuição de clientes ao território, com banco."""

    def _cenario(self):
        from apps.cadastros.models import Cliente, Praca
        from apps.core.models import Empresa, Filial

        empresa = Empresa.objects.create(
            razao_social='T', cnpj='11222333000181',
            regime_tributario='simples', codigo_regime_tributario=1,
        )
        filial = Filial.objects.create(
            empresa=empresa, razao_social='Matriz', nome_fantasia='Matriz',
            cnpj='11222333000181', uf='RN', is_matriz=True,
        )
        praca = Praca.objects.create(filial=filial, nome='Zona Sul')
        praca.definir_poligono(PoligonoTests.QUADRADO)
        praca.save()

        def cli(nome, lat, lng):
            return Cliente.objects.create(
                filial=filial, razao_social=nome, cpf_cnpj=nome[:11],
                cidade='Natal', uf='RN', latitude=lat, longitude=lng, ativo=True,
            )

        cli('DENTRO', -5.85, -35.20)
        cli('FORA', -5.85, -35.40)
        cli('SEMGEO', None, None)
        return filial, praca

    def test_atribui_somente_quem_esta_dentro(self):
        from apps.mapas.models import ClienteTerritorio
        from apps.mapas.services import TerritorioService

        _, praca = self._cenario()
        total = TerritorioService.recalcular_praca(praca)

        self.assertEqual(total, 1)
        nomes = list(
            ClienteTerritorio.objects.filter(praca=praca)
            .values_list('cliente__razao_social', flat=True)
        )
        self.assertEqual(nomes, ['DENTRO'])

    def test_recalculo_e_idempotente(self):
        """Rodar duas vezes não duplica (a constraint unique protege)."""
        from apps.mapas.models import ClienteTerritorio
        from apps.mapas.services import TerritorioService

        _, praca = self._cenario()
        TerritorioService.recalcular_praca(praca)
        TerritorioService.recalcular_praca(praca)
        self.assertEqual(ClienteTerritorio.objects.filter(praca=praca).count(), 1)

    def test_poligono_removido_limpa_atribuicao(self):
        from apps.mapas.models import ClienteTerritorio
        from apps.mapas.services import TerritorioService

        _, praca = self._cenario()
        TerritorioService.recalcular_praca(praca)

        praca.definir_poligono(None)
        praca.save()
        self.assertEqual(TerritorioService.recalcular_praca(praca), 0)
        self.assertFalse(ClienteTerritorio.objects.filter(praca=praca).exists())

    def test_territorio_do_ponto(self):
        from apps.mapas.services import TerritorioService

        filial, praca = self._cenario()
        achada = TerritorioService.territorio_do_ponto(filial, -5.85, -35.20)
        self.assertEqual(achada, praca)
        self.assertIsNone(TerritorioService.territorio_do_ponto(filial, -5.85, -35.40))

    def test_indicadores_sem_venda(self):
        from apps.mapas.services import TerritorioService

        _, praca = self._cenario()
        TerritorioService.recalcular_praca(praca)
        ind = TerritorioService.indicadores(praca)

        self.assertEqual(ind['clientes'], 1)
        self.assertEqual(ind['pedidos'], 0)
        self.assertEqual(float(ind['faturamento']), 0.0)
        self.assertIsNone(ind['realizado_pct'])  # meta zerada

    def test_indicadores_meta_x_realizado(self):
        from decimal import Decimal

        from apps.mapas.services import TerritorioService

        _, praca = self._cenario()
        praca.meta_mensal = Decimal('1000.00')
        praca.save()
        TerritorioService.recalcular_praca(praca)

        ind = TerritorioService.indicadores(praca)
        self.assertEqual(float(ind['meta']), 1000.0)
        self.assertEqual(ind['realizado_pct'], 0.0)


class RotaFksTests(TestCase):
    """Rota migrada de texto para FK, com fallback."""

    def _base(self):
        from apps.core.models import Empresa, Filial

        empresa = Empresa.objects.create(
            razao_social='T', cnpj='11222333000181',
            regime_tributario='simples', codigo_regime_tributario=1,
        )
        return Filial.objects.create(
            empresa=empresa, razao_social='M', nome_fantasia='M',
            cnpj='11222333000181', uf='RN', is_matriz=True,
        )

    def test_property_prefere_fk(self):
        from apps.cadastros.models import Motorista, Rota

        filial = self._base()
        motorista = Motorista.objects.create(filial=filial, nome='Jose da Silva')
        rota = Rota.objects.create(
            filial=filial, nome='R1', motorista=motorista,
            motorista_padrao='Nome Antigo',
        )
        self.assertEqual(rota.motorista_nome, 'Jose da Silva')

    def test_property_cai_para_texto_legado(self):
        """O que a migration não conseguiu casar continua visível."""
        from apps.cadastros.models import Rota

        filial = self._base()
        rota = Rota.objects.create(
            filial=filial, nome='R2', motorista_padrao='Terceirizado Ltda',
            veiculo_padrao='ABC1234',
        )
        self.assertEqual(rota.motorista_nome, 'Terceirizado Ltda')
        self.assertEqual(rota.veiculo_placa, 'ABC1234')

    def test_sem_nada_devolve_vazio(self):
        from apps.cadastros.models import Rota

        rota = Rota(filial=self._base(), nome='R3')
        self.assertEqual(rota.motorista_nome, '')
        self.assertEqual(rota.veiculo_placa, '')


class FormEscopoTests(TestCase):
    """
    Os selects de FK têm de respeitar a filial.

    É o teste que protege contra vazamento entre inquilinos: com `exclude` no
    ModelForm, o queryset padrão de uma FK é *todos* os registros.
    """

    def _duas_empresas(self):
        from apps.cadastros.models import Motorista
        from apps.core.models import Empresa, Filial

        def montar(nome, cnpj):
            emp = Empresa.objects.create(
                razao_social=nome, cnpj=cnpj,
                regime_tributario='simples', codigo_regime_tributario=1,
            )
            fil = Filial.objects.create(
                empresa=emp, razao_social=nome, nome_fantasia=nome,
                cnpj=cnpj, uf='RN', is_matriz=True,
            )
            Motorista.objects.create(filial=fil, nome=f'Motorista {nome}')
            return fil

        return montar('AAA', '11222333000181'), montar('BBB', '99888777000166')

    def test_rota_form_nao_mostra_motorista_de_outra_empresa(self):
        from apps.cadastros.forms.rota_praca import RotaForm

        filial_a, _ = self._duas_empresas()
        form = RotaForm(filial=filial_a)
        nomes = [str(m) for m in form.fields['motorista'].queryset]

        self.assertEqual(nomes, ['Motorista AAA'])

    def test_sem_filial_o_select_fica_vazio(self):
        """Falha fechada: sem filial não se mostra nada, em vez de tudo."""
        from apps.cadastros.forms.rota_praca import RotaForm

        self._duas_empresas()
        form = RotaForm(filial=None)
        self.assertEqual(form.fields['motorista'].queryset.count(), 0)

    def test_praca_form_nao_expoe_poligono(self):
        from apps.cadastros.forms.rota_praca import PracaForm

        form = PracaForm(filial=None)
        for campo in ('poligono', 'bbox_sul', 'bbox_norte', 'bbox_oeste', 'bbox_leste'):
            self.assertNotIn(campo, form.fields)
