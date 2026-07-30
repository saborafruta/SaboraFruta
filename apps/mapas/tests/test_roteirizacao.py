"""
Roteirizacao (secao 4): distancia, tempo, tracado e lista de paradas.

O risco silencioso deste modulo e a ordem das coordenadas: OSRM e ORS recebem
lon,lat, enquanto Leaflet e o resto do sistema usam lat,lng. Inverter nao
levanta erro -- devolve uma rota plausivel no lugar errado do mundo. Por isso
ha teste dedicado para a conversao nos dois sentidos.
"""
from unittest.mock import patch

from django.test import TestCase

from apps.mapas.services.roteirizacao import (
    MAX_PARADAS, ORSRoteirizador, OSRMRoteirizador, Rota, RoteirizacaoService,
    RoteirizadorBase, _para_lonlat, construir_roteirizador,
)


class ConversaoCoordenadasTests(TestCase):
    def test_inverte_para_lon_lat(self):
        """Natal fica em lat -5.79, lng -35.21; OSRM espera o inverso."""
        self.assertEqual(_para_lonlat([(-5.79, -35.21)]), '-35.21,-5.79')

    def test_varios_pontos_separados_por_ponto_e_virgula(self):
        self.assertEqual(
            _para_lonlat([(-5.79, -35.21), (-5.80, -35.25)]),
            '-35.21,-5.79;-35.25,-5.8',
        )


class FormatacaoTests(TestCase):
    def test_distancia_em_km(self):
        self.assertEqual(Rota(distancia_m=12345, geometria=[[0, 0]]).distancia_km, 12.35)

    def test_duracao_em_minutos(self):
        self.assertEqual(Rota(duracao_s=1800).duracao_texto, '30 min')

    def test_duracao_em_horas(self):
        self.assertEqual(Rota(duracao_s=5400).duracao_texto, '1h30')

    def test_rota_com_erro_nao_esta_ok(self):
        self.assertFalse(Rota(erro='falhou').ok)

    def test_rota_sem_geometria_nao_esta_ok(self):
        self.assertFalse(Rota(distancia_m=100).ok)


class OSRMTests(TestCase):
    RESPOSTA = {
        'code': 'Ok',
        'routes': [{
            'distance': 5000.0,
            'duration': 600.0,
            # GeoJSON vem em lon,lat
            'geometry': {'coordinates': [[-35.21, -5.79], [-35.25, -5.80]]},
        }],
    }

    def _mock(self, payload, status=200):
        alvo = patch('apps.mapas.services.roteirizacao.requests.get')
        mock = alvo.start()
        self.addCleanup(alvo.stop)
        mock.return_value.status_code = status
        mock.return_value.json.return_value = payload
        mock.return_value.raise_for_status.return_value = None
        return mock

    def test_converte_a_geometria_de_volta_para_lat_lng(self):
        """O que sai do servico tem de estar pronto para o L.polyline."""
        self._mock(self.RESPOSTA)
        rota = OSRMRoteirizador().rota([(-5.79, -35.21), (-5.80, -35.25)])

        self.assertEqual(rota.geometria, [[-5.79, -35.21], [-5.80, -35.25]])
        self.assertEqual(rota.distancia_m, 5000.0)
        self.assertEqual(rota.duracao_s, 600.0)

    def test_manda_as_coordenadas_em_lon_lat_na_url(self):
        mock = self._mock(self.RESPOSTA)
        OSRMRoteirizador().rota([(-5.79, -35.21)])

        url = mock.call_args[0][0]
        self.assertIn('-35.21,-5.79', url)

    def test_code_diferente_de_ok_vira_erro(self):
        self._mock({'code': 'NoRoute', 'message': 'sem rota'})
        rota = OSRMRoteirizador().rota([(-5.79, -35.21), (-5.80, -35.25)])

        self.assertFalse(rota.ok)
        self.assertEqual(rota.erro, 'sem rota')

    def test_instancia_publica_nao_libera_uso_comercial(self):
        self.assertFalse(OSRMRoteirizador().permite_uso_comercial)

    def test_instancia_propria_libera(self):
        proprio = OSRMRoteirizador(base_url='https://osrm.minhaempresa.com')
        self.assertTrue(proprio.permite_uso_comercial)


class ProviderTests(TestCase):
    def test_padrao_e_osrm(self):
        self.assertIsInstance(construir_roteirizador(), OSRMRoteirizador)

    def test_ors_sem_chave_cai_para_osrm(self):
        with self.settings(MAPAS_ROTA_PROVIDER='openrouteservice', MAPAS_ROTA_API_KEY=''):
            self.assertIsInstance(construir_roteirizador(), OSRMRoteirizador)

    def test_ors_com_chave(self):
        with self.settings(MAPAS_ROTA_PROVIDER='openrouteservice', MAPAS_ROTA_API_KEY='k'):
            provider = construir_roteirizador()
        self.assertIsInstance(provider, ORSRoteirizador)
        self.assertTrue(provider.permite_uso_comercial)


class _StubRoteirizador(RoteirizadorBase):
    nome = 'stub'
    permite_uso_comercial = True

    def __init__(self):
        self.pontos_recebidos = None

    def rota(self, pontos):
        self.pontos_recebidos = list(pontos)
        return Rota(
            distancia_m=1000, duracao_s=120,
            geometria=[[p[0], p[1]] for p in pontos],
        )


class RoteirizacaoServiceTests(TestCase):
    def setUp(self):
        from apps.core.models import Empresa, Filial

        self.empresa = Empresa.objects.create(
            razao_social='T', cnpj='11222333000181',
            regime_tributario='simples', codigo_regime_tributario=1,
        )
        self.filial = Filial.objects.create(
            empresa=self.empresa, razao_social='M', nome_fantasia='Matriz',
            cnpj='11222333000181', uf='RN', is_matriz=True,
            latitude=-5.75, longitude=-35.20,
        )
        self.stub = _StubRoteirizador()
        self.servico = RoteirizacaoService(roteirizador=self.stub)

    def _cliente(self, nome, lat, lng, cpf):
        from apps.cadastros.models import Cliente

        return Cliente.objects.create(
            filial=self.filial, razao_social=nome, cpf_cnpj=cpf,
            cidade='Natal', uf='RN', latitude=lat, longitude=lng, ativo=True,
        )

    def test_rota_respeita_a_ordem_escolhida(self):
        """Reordenar e a secao 5; aqui a ordem e a que o usuario montou."""
        a = self._cliente('A', -5.79, -35.21, '1')
        b = self._cliente('B', -5.80, -35.22, '2')
        c = self._cliente('C', -5.81, -35.23, '3')

        rota = self.servico.rota_de_clientes(
            filial=self.filial, cliente_ids=[c.pk, a.pk, b.pk],
        )
        nomes = [p.nome for p in rota.paradas if p.cliente_id]
        self.assertEqual(nomes, ['C', 'A', 'B'])

    def test_parte_da_filial_quando_ela_tem_coordenada(self):
        a = self._cliente('A', -5.79, -35.21, '1')
        rota = self.servico.rota_de_clientes(filial=self.filial, cliente_ids=[a.pk])

        self.assertEqual(rota.paradas[0].nome, 'Matriz (saida)')
        self.assertIsNone(rota.paradas[0].cliente_id)
        self.assertEqual(self.stub.pontos_recebidos[0], (-5.75, -35.20))

    def test_sem_partir_da_filial(self):
        a = self._cliente('A', -5.79, -35.21, '1')
        b = self._cliente('B', -5.80, -35.22, '2')
        rota = self.servico.rota_de_clientes(
            filial=self.filial, cliente_ids=[a.pk, b.pk], partir_da_filial=False,
        )
        self.assertEqual(rota.paradas[0].nome, 'A')

    def test_cliente_sem_coordenada_e_ignorado(self):
        a = self._cliente('A', -5.79, -35.21, '1')
        sem = self._cliente('SEM', None, None, '2')
        b = self._cliente('B', -5.80, -35.22, '3')

        rota = self.servico.rota_de_clientes(
            filial=self.filial, cliente_ids=[a.pk, sem.pk, b.pk],
        )
        self.assertNotIn('SEM', [p.nome for p in rota.paradas])

    def test_lista_vazia_e_erro(self):
        rota = self.servico.rota_de_clientes(filial=self.filial, cliente_ids=[])
        self.assertFalse(rota.ok)

    def test_acima_do_teto_de_paradas_e_recusado(self):
        rota = self.servico.rota_de_clientes(
            filial=self.filial, cliente_ids=list(range(MAX_PARADAS + 1)),
        )
        self.assertFalse(rota.ok)
        self.assertIn('Maximo', rota.erro)

    def test_cliente_de_outra_empresa_nao_entra_na_rota(self):
        """Isolamento entre inquilinos."""
        from apps.cadastros.models import Cliente
        from apps.core.models import Empresa, Filial

        outra = Empresa.objects.create(
            razao_social='X', cnpj='99888777000166',
            regime_tributario='simples', codigo_regime_tributario=1,
        )
        filial_b = Filial.objects.create(
            empresa=outra, razao_social='B', nome_fantasia='B',
            cnpj='99888777000166', uf='RN', is_matriz=True,
        )
        alheio = Cliente.objects.create(
            filial=filial_b, razao_social='ALHEIO', cpf_cnpj='9',
            cidade='Natal', uf='RN', latitude=-5.9, longitude=-35.3, ativo=True,
        )
        a = self._cliente('A', -5.79, -35.21, '1')

        rota = self.servico.rota_de_clientes(
            filial=self.filial, cliente_ids=[a.pk, alheio.pk],
        )
        self.assertNotIn('ALHEIO', [p.nome for p in rota.paradas])

    def test_falha_de_rede_vira_erro_tratado(self):
        import requests

        a = self._cliente('A', -5.79, -35.21, '1')
        with patch.object(_StubRoteirizador, 'rota',
                          side_effect=requests.Timeout('estourou')):
            rota = self.servico.rota_de_clientes(filial=self.filial, cliente_ids=[a.pk])

        self.assertFalse(rota.ok)
        self.assertIn('Falha no servico de rotas', rota.erro)
