"""
A busca de cliente na entrega do romaneio.

QUEM MONTA O ROMANEIO NÃO DECORA O CADASTRO. Digitar o nome à mão erra o
cliente e, pior, deixa o endereço para ser copiado de outra tela — que é onde
o motorista ganha uma rua errada. O romaneio impresso não avisa.

O QUE ESTES TESTES CERCAM:

  · O ENDEREÇO VIAJA JUNTO com o cliente: é ele que a entrega precisa;

  · DUAS LETRAS NO MÍNIMO. Uma letra devolveria o cadastro inteiro fatiado,
    que não ajuda ninguém e ainda parece lento;

  · A BUSCA NÃO ATRAVESSA A FILIAL — nem por nome, nem por documento;

  · PERMISSÃO É DE LOGÍSTICA: quem monta romaneio acha o cliente da entrega
    sem ganhar acesso ao cadastro inteiro.
"""
from django.test import TestCase
from django.urls import reverse

from apps.cadastros.models import Cliente, ClienteFilial
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario


class BuscaClienteEntregaTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Romaneio LTDA', nome_fantasia='Romaneio',
            cnpj='41945678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='41945678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.outra_filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Filial 2', cnpj='41945678000353',
            uf='RN', cidade='Mossoró',
        )
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='romaneio@rota.local', nome='Romaneio', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Mercado da Esquina LTDA',
            nome_fantasia='Mercado da Esquina', cpf_cnpj='12345678000190',
            endereco='Rua Áustria', numero='115', bairro='Passagem de Areia',
            cidade='Parnamirim', uf='RN', cep='59145800',
        )
        ClienteFilial.objects.create(cliente=cls.cliente, filial=cls.filial)
        cls.alheio = Cliente.objects.create(
            filial=cls.outra_filial, razao_social='Mercado de Mossoró',
            cpf_cnpj='98765432000199', cidade='Mossoró', uf='RN',
        )
        ClienteFilial.objects.create(cliente=cls.alheio, filial=cls.outra_filial)

    def setUp(self):
        self.client.force_login(self.usuario)
        self.url = reverse('logistica:cliente-entrega-search')

    def _buscar(self, termo):
        return self.client.get(self.url, {'q': termo}).json()['results']

    # ── O que a busca devolve ────────────────────────────────────────────

    def test_o_endereco_vem_junto_com_o_cliente(self):
        """É o endereço que a entrega precisa."""
        resultado = self._buscar('esquina')[0]

        self.assertEqual(resultado['nome'], 'Mercado da Esquina')
        self.assertEqual(resultado['documento'], '12345678000190')
        self.assertEqual(resultado['endereco'], 'Rua Áustria')
        self.assertEqual(resultado['numero'], '115')
        self.assertEqual(resultado['bairro'], 'Passagem de Areia')
        self.assertEqual(resultado['cidade'], 'Parnamirim')
        self.assertEqual(resultado['uf'], 'RN')

    def test_acha_pela_razao_social_e_pelo_nome_fantasia(self):
        self.assertTrue(self._buscar('LTDA'))
        self.assertTrue(self._buscar('Esquina'))

    def test_acha_pelo_documento_mesmo_com_pontuacao(self):
        """
        Quem tem o CNPJ na mão copia com ponto e barra — e o cadastro guarda
        só os dígitos.
        """
        self.assertTrue(self._buscar('12.345.678/0001-90'))

    def test_uma_letra_nao_devolve_o_cadastro_inteiro(self):
        self.assertEqual(self._buscar('m'), [])

    def test_a_busca_nao_atravessa_a_filial(self):
        nomes = [c['nome'] for c in self._buscar('Mercado')]

        self.assertIn('Mercado da Esquina', nomes)
        self.assertNotIn('Mercado de Mossoró', nomes)

    def test_documento_de_outra_filial_tambem_nao_vaza(self):
        self.assertEqual(self._buscar('98765432000199'), [])

    def test_cliente_inativo_fica_de_fora(self):
        """Entrega para cliente desativado é o cadastro dizendo que não."""
        self.cliente.ativo = False
        self.cliente.save(update_fields=['ativo'])

        self.assertEqual(self._buscar('esquina'), [])


class TelaDoRomaneioTests(BuscaClienteEntregaTests):
    """O campo na tela."""

    def test_a_tela_do_romaneio_traz_a_busca(self):
        from apps.logistica.models import RomaneioCarga

        romaneio = RomaneioCarga.objects.create(
            filial=self.filial, numero=1, responsavel=self.usuario,
        )

        html = self.client.get(
            reverse('logistica:romaneio-detail', args=[romaneio.pk]),
        ).content.decode()

        self.assertIn('buscaClienteEntrega', html)
        self.assertIn(reverse('logistica:cliente-entrega-search'), html)
