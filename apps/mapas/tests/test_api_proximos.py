"""
API de clientes proximos (secao 7) -- o contrato HTTP.

O servico ja e coberto por `test_geo.ProximidadeTests`. Aqui o objeto e a
view: parametros, validacao, ordenacao no JSON e isolamento entre empresas.
"""
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class BaseProximosApi(TestCase):
    """~0,009 grau de latitude = ~1 km, o que da distancias conferiveis."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tem_earthdistance = False
        if connection.vendor != 'postgresql':
            return
        try:
            with connection.cursor() as cur:
                cur.execute('CREATE EXTENSION IF NOT EXISTS cube')
                cur.execute('CREATE EXTENSION IF NOT EXISTS earthdistance')
                cur.execute(
                    'CREATE INDEX IF NOT EXISTS clientes_geo_gist_idx '
                    'ON clientes USING gist (ll_to_earth(latitude, longitude)) '
                    'WHERE latitude IS NOT NULL'
                )
            cls.tem_earthdistance = True
        except Exception:
            pass

    def setUp(self):
        if not self.tem_earthdistance:
            self.skipTest('requer Postgres com cube/earthdistance')
        self.filial = self._empresa('Alfa', '11222333000181')
        self._logar(self._usuario(self.filial), self.filial)

    def _empresa(self, nome, cnpj):
        from apps.core.models import Empresa, Filial

        emp = Empresa.objects.create(
            razao_social=nome, cnpj=cnpj,
            regime_tributario='simples', codigo_regime_tributario=1,
        )
        return Filial.objects.create(
            empresa=emp, razao_social=nome, nome_fantasia=nome,
            cnpj=cnpj, uf='RN', is_matriz=True,
        )

    def _usuario(self, filial):
        from apps.core.models import PerfilAcesso, Usuario

        perfil = PerfilAcesso.objects.create(
            empresa=filial.empresa, nome=f'Perfil {filial.pk}', is_admin=True,
        )
        return Usuario.objects.create_user(
            email=f'u{filial.pk}@teste.local', nome='U',
            password='senha-de-teste-123',
            empresa=filial.empresa, perfil=perfil, filial=filial,
        )

    def _logar(self, usuario, filial):
        self.client.force_login(usuario)
        sessao = self.client.session
        sessao['filial_ativa_id'] = filial.pk
        sessao.save()

    def _cliente(self, nome, lat, doc, filial=None, **extra):
        from apps.cadastros.models import Cliente

        return Cliente.objects.create(
            filial=filial or self.filial, razao_social=nome, cpf_cnpj=doc,
            cidade='Natal', uf='RN', latitude=lat, longitude=-35.210,
            ativo=True, **extra,
        )

    def _buscar(self, **params):
        params.setdefault('lat', -5.790)
        params.setdefault('lng', -35.210)
        return self.client.get(reverse('mapas:api-clientes-proximos'), params)


class RespostaTests(BaseProximosApi):
    def test_ordena_do_mais_perto_para_o_mais_longe(self):
        self._cliente('LONGE', -5.808, '1')
        self._cliente('PERTO', -5.7905, '2')
        self._cliente('MEDIO', -5.799, '3')

        dados = self._buscar(raio=5000).json()
        nomes = [c['nome'] for c in dados['clientes']]

        self.assertEqual(nomes, ['PERTO', 'MEDIO', 'LONGE'])
        distancias = [c['distancia_m'] for c in dados['clientes']]
        self.assertEqual(distancias, sorted(distancias))

    def test_traz_a_distancia_pronta_para_exibir(self):
        """A tela nao deveria ter de decidir entre metros e km."""
        self._cliente('PERTO', -5.7905, '1')       # ~55 m
        self._cliente('LONGE', -5.808, '2')        # ~2 km

        clientes = self._buscar(raio=5000).json()['clientes']

        self.assertTrue(clientes[0]['distancia_texto'].endswith(' m'))
        self.assertTrue(clientes[1]['distancia_texto'].endswith(' km'))

    def test_raio_menor_corta_os_distantes(self):
        self._cliente('PERTO', -5.7905, '1')
        self._cliente('LONGE', -5.880, '2')        # ~10 km

        dados = self._buscar(raio=1000).json()
        self.assertEqual([c['nome'] for c in dados['clientes']], ['PERTO'])
        self.assertEqual(dados['total'], 1)

    def test_devolve_o_centro_e_o_raio_usados(self):
        dados = self._buscar(raio=3000).json()
        self.assertEqual(dados['centro'], {'lat': -5.790, 'lng': -35.210})
        self.assertEqual(dados['raio_m'], 3000)

    def test_raio_acima_do_teto_e_limitado_na_resposta(self):
        """O teto existe; escondê-lo faria o usuario crer num raio maior."""
        dados = self._buscar(raio=999000).json()
        self.assertEqual(dados['raio_m'], 50000)

    def test_excluir_cliente_tira_o_ponto_central_da_lista(self):
        centro = self._cliente('CENTRO', -5.790, '1')
        self._cliente('VIZINHO', -5.7905, '2')

        dados = self._buscar(raio=3000, excluir_cliente=centro.pk).json()
        self.assertEqual([c['nome'] for c in dados['clientes']], ['VIZINHO'])

    def test_inativo_nao_aparece(self):
        self._cliente('ATIVO', -5.7905, '1')
        inativo = self._cliente('INATIVO', -5.7906, '2')
        inativo.ativo = False
        inativo.save(update_fields=['ativo'])

        nomes = [c['nome'] for c in self._buscar(raio=3000).json()['clientes']]
        self.assertEqual(nomes, ['ATIVO'])


class ValidacaoTests(BaseProximosApi):
    def test_sem_coordenada_e_400(self):
        resp = self.client.get(reverse('mapas:api-clientes-proximos'))
        self.assertEqual(resp.status_code, 400)
        self.assertIn('lat', resp.json()['erro'])

    def test_coordenada_fora_do_brasil_e_400(self):
        """Lat/lng trocados caem aqui -- e o erro certo e melhor que 0 achados."""
        resp = self._buscar(lat=48.85, lng=2.35)
        self.assertEqual(resp.status_code, 400)

    def test_coordenada_nao_numerica_e_400(self):
        self.assertEqual(self._buscar(lat='abc').status_code, 400)


class IsolamentoTests(BaseProximosApi):
    def test_cliente_de_outra_empresa_nao_vaza(self):
        outra = self._empresa('Beta', '99888777000166')
        self._cliente('MEU', -5.7905, '1')
        self._cliente('ALHEIO', -5.7906, '2', filial=outra)

        nomes = [c['nome'] for c in self._buscar(raio=3000).json()['clientes']]
        self.assertEqual(nomes, ['MEU'])

    def test_exige_autenticacao(self):
        self.client.logout()
        resp = self._buscar(raio=3000)
        self.assertIn(resp.status_code, (302, 401, 403))
