"""
Peso bruto da carga do MDF-e.

Antes, o calculo usava so `Produto.peso_bruto`: qualquer produto sem esse
campo zerava a carga inteira e bloqueava a emissao, mesmo quando o peso era
dedutivel do proprio cadastro (item vendido em KG tem a quantidade como peso).

Peso em MDF-e e declaracao fiscal, entao a regra so deriva de dado JA
cadastrado -- nunca estima.
"""
from decimal import Decimal

from django.test import TestCase

from apps.estoque.services.peso_carga import calcular_peso_bruto, peso_unitario_kg


class BasePeso(TestCase):
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

    def _unidade(self, sigla, tipo='unidade', fator=1):
        from apps.produtos.models import UnidadeMedida

        u, _ = UnidadeMedida.objects.get_or_create(
            empresa=self.empresa, sigla=sigla,
            defaults={'descricao': sigla, 'tipo': tipo,
                      'fator_conversao_base': Decimal(str(fator))},
        )
        return u

    def _produto(self, nome, *, unidade='UN', tipo_unidade='unidade', fator=1,
                 peso_bruto=None, peso_liquido=None, unidade_peso='kg'):
        from apps.produtos.models import Produto

        return Produto.objects.create(
            filial=self.filial, descricao=nome, codigo=nome[:6],
            unidade_medida=self._unidade(unidade, tipo_unidade, fator),
            preco_venda=Decimal('10'), ativo=True,
            peso_bruto=peso_bruto, peso_liquido=peso_liquido,
            unidade_peso=unidade_peso,
        )


class PesoUnitarioTests(BasePeso):
    def test_usa_peso_bruto_quando_existe(self):
        p = self._produto('A', peso_bruto=Decimal('1.2'), peso_liquido=Decimal('1'))
        self.assertEqual(peso_unitario_kg(p), (Decimal('1.2'), 'peso_bruto'))

    def test_cai_para_peso_liquido(self):
        """Subestima pela embalagem, mas e melhor que zerar a carga."""
        p = self._produto('B', peso_liquido=Decimal('0.9'))
        self.assertEqual(peso_unitario_kg(p), (Decimal('0.9'), 'peso_liquido'))

    def test_produto_vendido_em_kg_usa_a_propria_unidade(self):
        """O caso dos '1 KG POLPA': a quantidade ja e o peso."""
        p = self._produto('POLPA', unidade='KG', tipo_unidade='peso', fator=1)
        peso, origem = peso_unitario_kg(p)
        self.assertEqual(origem, 'unidade_peso')
        self.assertEqual(peso, Decimal('1'))

    def test_unidade_em_grama_converte_pelo_fator(self):
        p = self._produto('G', unidade='G', tipo_unidade='peso', fator=Decimal('0.001'))
        peso, origem = peso_unitario_kg(p)
        self.assertEqual(origem, 'unidade_peso')
        self.assertEqual(peso, Decimal('0.001'))

    def test_peso_em_gramas_e_convertido_para_kg(self):
        p = self._produto('MG', peso_bruto=Decimal('250'), unidade_peso='g')
        peso, origem = peso_unitario_kg(p)
        self.assertEqual(origem, 'peso_bruto')
        self.assertEqual(peso, Decimal('0.250'))

    def test_sem_nenhuma_fonte_fica_ausente(self):
        """Nao inventa peso: o produto e reportado como pendente."""
        p = self._produto('SEMPESO')
        self.assertEqual(peso_unitario_kg(p), (Decimal('0'), 'ausente'))

    def test_peso_zerado_nao_conta_como_fonte(self):
        p = self._produto('ZERO', peso_bruto=Decimal('0'), peso_liquido=Decimal('0'))
        self.assertEqual(peso_unitario_kg(p)[1], 'ausente')


class CalcularPesoBrutoTests(BasePeso):
    def test_soma_multiplicando_pela_quantidade(self):
        a = self._produto('A', peso_bruto=Decimal('1.5'))
        b = self._produto('B', peso_bruto=Decimal('0.5'))
        r = calcular_peso_bruto([(a, 2), (b, 10)])

        self.assertEqual(r['peso_kg'], Decimal('8.000'))
        self.assertEqual(r['pendentes'], [])

    def test_carga_de_polpas_em_kg_agora_emite(self):
        """O cenario reportado: 13 produtos em KG somavam 0 e bloqueavam."""
        polpas = [
            self._produto(f'POLPA {i}', unidade='KG', tipo_unidade='peso')
            for i in range(13)
        ]
        r = calcular_peso_bruto([(p, 5) for p in polpas])

        self.assertEqual(r['pendentes'], [])
        self.assertEqual(r['peso_kg'], Decimal('65.000'))  # 13 x 5 kg

    def test_reporta_apenas_os_realmente_sem_peso(self):
        ok = self._produto('OK', peso_bruto=Decimal('1'))
        ruim = self._produto('SEMPESO')
        r = calcular_peso_bruto([(ok, 3), (ruim, 4)])

        self.assertEqual(r['pendentes'], ['SEMPESO'])
        # O que tem peso continua somando, para o usuario ver o parcial.
        self.assertEqual(r['peso_kg'], Decimal('3.000'))

    def test_origens_permitem_auditar_de_onde_veio_cada_peso(self):
        a = self._produto('A', peso_bruto=Decimal('1'))
        b = self._produto('B', peso_liquido=Decimal('2'))
        r = calcular_peso_bruto([(a, 1), (b, 1)])

        self.assertEqual(r['origens'], {'A': 'peso_bruto', 'B': 'peso_liquido'})

    def test_carga_vazia(self):
        r = calcular_peso_bruto([])
        self.assertEqual(r['peso_kg'], Decimal('0.000'))
        self.assertEqual(r['pendentes'], [])

    def test_quantidade_decimal(self):
        p = self._produto('KG', unidade='KG', tipo_unidade='peso')
        r = calcular_peso_bruto([(p, Decimal('2.5'))])
        self.assertEqual(r['peso_kg'], Decimal('2.500'))
