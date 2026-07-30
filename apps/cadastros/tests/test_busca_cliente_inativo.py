"""
Busca de cliente inativo na tela de Clientes.

Cenario reportado: um cliente existia na base (a tela de edicao abria por
/cadastros/clientes/37/), mas nao era encontrado por NENHUM termo de busca --
nem pela razao social, nem pelo nome fantasia. Causa: a lista filtra
`ativo=True` por padrao e a view de edicao nao filtra, entao o cadastro abria
enquanto a busca dizia "nenhum resultado" -- indistinguivel de o cliente nao
existir.
"""
from django.test import TestCase, override_settings
from django.urls import reverse


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class BuscaClienteInativoTests(TestCase):
    def setUp(self):
        from apps.cadastros.models import Cliente, ClienteFilial
        from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario

        self.empresa = Empresa.objects.create(
            razao_social='T', cnpj='11222333000181',
            regime_tributario='simples', codigo_regime_tributario=1,
        )
        self.filial = Filial.objects.create(
            empresa=self.empresa, razao_social='M', nome_fantasia='M',
            cnpj='11222333000181', uf='RN', is_matriz=True,
        )
        perfil = PerfilAcesso.objects.create(
            empresa=self.empresa, nome='Adm', is_admin=True,
        )
        usuario = Usuario.objects.create_user(
            email='a@t.local', nome='A', password='x',
            empresa=self.empresa, perfil=perfil, filial=self.filial,
        )
        self.client.force_login(usuario)
        sessao = self.client.session
        sessao['filial_ativa_id'] = self.filial.pk
        sessao.save()

        def criar(razao, fantasia, cnpj, ativo):
            cli = Cliente.objects.create(
                filial=self.filial, razao_social=razao, nome_fantasia=fantasia,
                cpf_cnpj=cnpj, ativo=ativo,
            )
            # A lista usa `for_filial`, que filtra pelo vinculo ClienteFilial.
            ClienteFilial.objects.create(cliente=cli, filial=self.filial, ativo=True)
            return cli

        self.inativo = criar(
            'MADRUGA BELCHIOR E FARAJ ALIMENTOS LTDA', 'PETISQUERIA NATAL',
            '43822027000172', ativo=False,
        )
        self.ativo = criar(
            'FC RESTAURANTE E PETISCARIA', 'FC RESTAURANTE E PETISCARIA',
            '11111111000111', ativo=True,
        )

    def _buscar(self, termo, **extra):
        return self.client.get(reverse('cadastros:cliente-list'), {'q': termo, **extra})

    def _nomes(self, resp):
        return [c.razao_social for c in resp.context['clientes']]

    def test_inativo_nao_aparece_por_padrao(self):
        """Comportamento atual, mantido: a lista e de clientes ativos."""
        resp = self._buscar('madruga')
        self.assertNotIn('MADRUGA BELCHIOR E FARAJ ALIMENTOS LTDA', self._nomes(resp))

    def test_avisa_que_existe_inativo_casando_com_a_busca(self):
        """O que faltava: a tela dizer que o cliente existe, mas esta inativo."""
        resp = self._buscar('madruga')
        self.assertEqual(resp.context['inativos_na_busca'], 1)

    def test_aviso_tambem_pelo_nome_fantasia(self):
        resp = self._buscar('petis')
        # 'petis' casa com os dois; o ativo aparece e o inativo e sinalizado.
        self.assertIn('FC RESTAURANTE E PETISCARIA', self._nomes(resp))
        self.assertEqual(resp.context['inativos_na_busca'], 1)

    def test_mostrar_inativos_traz_o_cliente(self):
        resp = self._buscar('madruga', inativos='1')
        self.assertIn('MADRUGA BELCHIOR E FARAJ ALIMENTOS LTDA', self._nomes(resp))
        # Com os inativos ja visiveis, nao ha o que avisar.
        self.assertEqual(resp.context['inativos_na_busca'], 0)

    def test_sem_busca_nao_conta_inativos(self):
        """O aviso e sobre a busca; sem termo nao faz sentido."""
        resp = self.client.get(reverse('cadastros:cliente-list'))
        self.assertEqual(resp.context['inativos_na_busca'], 0)

    def test_busca_sem_inativo_correspondente_nao_avisa(self):
        resp = self._buscar('inexistente-xyz')
        self.assertEqual(resp.context['inativos_na_busca'], 0)
