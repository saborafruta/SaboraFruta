"""
"Quando selecionar a OP quero que puxe a malha e a cor" -- a malha e a
cor que a OP pede ficam em texto dentro das observações do item (a OP
2.0 ainda não tem coluna própria pra isso), e são o que a tela
"Editar produto da OP" chama de MALHA e COR na "Estrutura da peça".

O QUE ESTES TESTES CERCAM:

  · SÓ SUGERE QUANDO O NOME BATE EXATO (sem diferenciar maiúsculas) com
    um Tecido/Cor já cadastrado -- chutar o tecido errado por um nome
    parecido pesa mais que não sugerir nada;

  · SEM ESTRUTURA GRAVADA (OP antiga, ou item sem malha/cor definida),
    devolve vazio -- o campo continua em branco do jeito que já era;

  · ESCOPADO POR FILIAL, como todo cadastro de apoio.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.moda.models import Cor, ItemPedidoProducao, OrdemProducao, PedidoProducao, Tecido
from apps.moda.services.op2_estrutura import parse_estrutura_campos


class SugestaoMaterialBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao Sugestao LTDA', nome_fantasia='Sugestao',
            cnpj='43345678000191', segmento='moda_confeccao',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Confeccao Sugestao LTDA',
            cnpj='43345678000272', uf='RN', cidade='Natal', is_matriz=True,
        )
        perfil = PerfilAcesso.objects.create(empresa=cls.empresa, nome='Admin', is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email='sugestao-material@teste.local', nome='Sugestao', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Cliente Sugestao', cpf_cnpj='98765432100',
        )

    def setUp(self):
        self.client.force_login(self.usuario)

    def _ordem(self, observacoes, numero=1, filial=None):
        filial = filial or self.filial
        pedido = PedidoProducao.objects.create(
            filial=filial, cliente=self.cliente, numero=numero,
        )
        item = ItemPedidoProducao.objects.create(
            pedido=pedido, descricao='Camisa de jogo',
            quantidade=10, valor_unitario=Decimal('50'),
            observacoes=observacoes,
        )
        return OrdemProducao.objects.create(
            filial=filial, pedido=pedido, item=item,
            numero=f'OP-{numero:04d}', ano=2026, sequencial=numero,
            quantidade=10,
        )


class ParseEstruturaCamposTests(TestCase):
    """A leitura de volta do texto que `juntar_observacoes_item` grava."""

    def test_le_malha_e_cor_do_bloco(self):
        texto = (
            'Observação livre do item.\n\n'
            'Estrutura da peça:\n'
            'Tipo de peça: Camisa\n'
            'Malha: DRY\n'
            'Cor: Royal Blue\n'
        )

        campos = parse_estrutura_campos(texto)

        self.assertEqual(campos['malha'], 'DRY')
        self.assertEqual(campos['cor'], 'Royal Blue')
        self.assertNotIn('tipo_de_peça', campos)

    def test_ignora_linhas_de_observacao_por_campo(self):
        texto = (
            'Estrutura da peça:\n'
            'Malha: DRY\n'
            'Observação de Malha: rolo mais grosso que o padrão\n'
        )

        campos = parse_estrutura_campos(texto)

        self.assertEqual(campos['malha'], 'DRY')
        self.assertNotIn('observação_de_malha', campos)

    def test_sem_marcador_devolve_vazio(self):
        self.assertEqual(parse_estrutura_campos('Só uma observação qualquer.'), {})
        self.assertEqual(parse_estrutura_campos(''), {})


class OrdemMaterialSugeridoViewTests(SugestaoMaterialBase):

    def test_sugere_tecido_e_cor_quando_nome_bate_exato(self):
        tecido = Tecido.objects.create(filial=self.filial, nome='Dry')
        cor = Cor.objects.create(filial=self.filial, nome='Royal Blue', sigla='RBL')
        ordem = self._ordem(
            'Estrutura da peça:\nTipo de peça: Camisa\nMalha: DRY\nCor: Royal Blue\n',
        )

        resposta = self.client.get(reverse('moda:corte-ordem-material'), {'ordem': ordem.pk})

        self.assertEqual(resposta.status_code, 200)
        dados = resposta.json()
        self.assertEqual(dados['tecido_id'], tecido.pk)
        self.assertEqual(dados['cor_id'], cor.pk)

    def test_sem_cadastro_correspondente_devolve_none(self):
        ordem = self._ordem(
            'Estrutura da peça:\nMalha: OXFORD PREMIUM\nCor: Verde Musgo\n',
        )

        resposta = self.client.get(reverse('moda:corte-ordem-material'), {'ordem': ordem.pk})

        dados = resposta.json()
        self.assertIsNone(dados['tecido_id'])
        self.assertIsNone(dados['cor_id'])

    def test_ordem_sem_estrutura_devolve_none(self):
        ordem = self._ordem('Só uma observação qualquer, sem estrutura.')

        resposta = self.client.get(reverse('moda:corte-ordem-material'), {'ordem': ordem.pk})

        dados = resposta.json()
        self.assertIsNone(dados['tecido_id'])
        self.assertIsNone(dados['cor_id'])

    def test_sem_ordem_na_query_devolve_none(self):
        resposta = self.client.get(reverse('moda:corte-ordem-material'))

        dados = resposta.json()
        self.assertIsNone(dados['tecido_id'])
        self.assertIsNone(dados['cor_id'])

    def test_nao_vaza_ordem_de_outra_filial(self):
        outra_filial = Filial.objects.create(
            empresa=self.empresa, razao_social='Outra', cnpj='43345678000353',
            uf='RN', cidade='Mossoró',
        )
        Tecido.objects.create(filial=outra_filial, nome='Dry')
        ordem_de_fora = self._ordem(
            'Estrutura da peça:\nMalha: DRY\n', numero=2, filial=outra_filial,
        )

        resposta = self.client.get(
            reverse('moda:corte-ordem-material'), {'ordem': ordem_de_fora.pk},
        )

        dados = resposta.json()
        self.assertIsNone(dados['tecido_id'])
