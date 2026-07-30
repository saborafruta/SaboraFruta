"""
Testes do módulo de Mapas.

Os testes de proximidade exigem Postgres com `cube`/`earthdistance` (as
funções são do banco, não do Python) e são pulados em outro backend.
"""
from unittest.mock import patch

from django.test import TestCase
from django.db import connection

from apps.mapas import constants as c
from apps.mapas.serializers import formatar_distancia
from apps.mapas.services.geocoder import (
    GeocodificacaoService, NominatimGeocoder, Resultado, _Throttle,
)


class CoordenadaMixinTests(TestCase):
    """Comportamento do mixin, sem tocar no banco."""

    def _cliente(self, **kw):
        from apps.cadastros.models import Cliente

        dados = dict(
            endereco='Av Capitao Mor Gouveia', numero='3005',
            bairro='Lagoa Nova', cidade='Natal', uf='RN', cep='59063400',
        )
        dados.update(kw)
        return Cliente(**dados)

    def test_endereco_montado_em_uma_linha(self):
        esperado = ('Av Capitao Mor Gouveia, 3005, Lagoa Nova, '
                    'Natal, RN, 59063-400, Brasil')
        self.assertEqual(self._cliente().endereco_para_geocodificar(), esperado)

    def test_endereco_vazio_nao_gera_hash(self):
        vazio = self._cliente(endereco='', numero='', bairro='', cidade='', uf='', cep='')
        self.assertEqual(vazio.endereco_para_geocodificar(), '')
        self.assertEqual(vazio.hash_endereco_atual(), '')
        self.assertFalse(vazio.geo_desatualizado)

    def test_hash_muda_quando_endereco_muda(self):
        a = self._cliente()
        b = self._cliente(numero='3007')
        self.assertNotEqual(a.hash_endereco_atual(), b.hash_endereco_atual())

    def test_desatualizado_quando_falta_coordenada(self):
        self.assertTrue(self._cliente().geo_desatualizado)

    def test_atualizado_quando_hash_confere(self):
        cli = self._cliente(latitude=-5.8, longitude=-35.2)
        cli.geo_endereco_hash = cli.hash_endereco_atual()
        self.assertFalse(cli.geo_desatualizado)

    def test_fixado_nunca_e_reprocessado(self):
        """Coordenada ajustada à mão não deve voltar para a fila."""
        cli = self._cliente(geo_fixado=True)
        self.assertFalse(cli.geo_desatualizado)


class ConstantesTests(TestCase):
    def test_bbox_do_brasil_aceita_natal_e_rejeita_exterior(self):
        self.assertTrue(c.dentro_do_brasil(-5.79, -35.21))       # Natal/RN
        self.assertFalse(c.dentro_do_brasil(-29.85, 31.02))      # Durban/ZA
        self.assertFalse(c.dentro_do_brasil(48.85, 2.35))        # Paris

    def test_formatar_distancia(self):
        self.assertEqual(formatar_distancia(320), '320 m')
        self.assertEqual(formatar_distancia(780), '780 m')
        self.assertEqual(formatar_distancia(1234), '1,2 km')


class GeocoderTests(TestCase):
    """Cache, validação e tolerância a falha — sem chamar a rede."""

    def setUp(self):
        # Rede bloqueada: um teste que escape do mock e chame o Nominatim
        # público seria lento, instável e abusaria da política de uso deles.
        rede = patch('apps.mapas.services.geocoder.requests.get',
                     side_effect=AssertionError('teste tentou acessar a rede'))
        rede.start()
        self.addCleanup(rede.stop)

    def _servico(self, resultado):
        """
        Serviço com o provider mockado.

        O patch é iniciado com start()/addCleanup e não com `with`: retornar
        de dentro de um bloco `with` desfaz o mock antes do teste usá-lo — e
        aí a chamada vai para a rede de verdade.
        """
        geocoder = NominatimGeocoder()
        patcher = patch.object(geocoder, 'geocodificar', return_value=resultado)
        chamada = patcher.start()
        self.addCleanup(patcher.stop)
        # Throttle zerado: não faz sentido dormir 1,1s em teste.
        servico = GeocodificacaoService(geocoder=geocoder, throttle=_Throttle(0))
        return servico, chamada

    def test_resultado_valido_e_gravado_no_cache(self):
        from apps.mapas.models import CacheGeocodificacao

        servico, chamada = self._servico(Resultado(-5.79, -35.21, 'exata'))
        res = servico.resolver('Natal, RN, Brasil', 'h1')

        self.assertTrue(res.ok)
        cache = CacheGeocodificacao.objects.get(pk='h1')
        self.assertTrue(cache.encontrado)
        self.assertEqual(cache.latitude, -5.79)

    def test_segunda_consulta_usa_cache_e_nao_chama_provider(self):
        """O ponto do cache: economizar a quota do provider."""
        servico, chamada = self._servico(Resultado(-5.79, -35.21, 'exata'))
        servico.resolver('Natal, RN, Brasil', 'h2')
        servico.resolver('Natal, RN, Brasil', 'h2')
        self.assertEqual(chamada.call_count, 1)

    def test_coordenada_fora_do_brasil_e_descartada(self):
        """'Natal' resolve para a África do Sul em provider sem filtro."""
        servico, _ = self._servico(Resultado(-29.85, 31.02, 'cidade'))
        res = servico.resolver('Natal', 'h3')
        self.assertFalse(res.ok)
        self.assertIn('fora do Brasil', res.erro)

    def test_falha_de_rede_nao_propaga_excecao(self):
        import requests

        geocoder = NominatimGeocoder()
        with patch.object(geocoder, 'geocodificar',
                          side_effect=requests.Timeout('estourou')):
            servico = GeocodificacaoService(geocoder=geocoder, throttle=_Throttle(0))
            res = servico.resolver('Natal, RN', 'h4')
        self.assertFalse(res.ok)
        self.assertIn('falha no provider', res.erro)

    def test_desiste_apos_max_tentativas(self):
        from apps.mapas.models import CacheGeocodificacao

        CacheGeocodificacao.objects.create(
            pk='h5', endereco_consultado='Rua Inexistente', encontrado=False,
            tentativas=c.GEOCODER_MAX_TENTATIVAS, erro='nao encontrado',
        )
        servico, chamada = self._servico(Resultado(-5.79, -35.21, 'exata'))
        res = servico.resolver('Rua Inexistente', 'h5')
        self.assertFalse(res.ok)
        chamada.assert_not_called()

    def test_nominatim_proprio_libera_uso_comercial(self):
        """A instância pública é restrita; a própria, não."""
        self.assertFalse(NominatimGeocoder().permite_uso_comercial)
        self.assertTrue(
            NominatimGeocoder(base_url='https://geo.minhaempresa.com').permite_uso_comercial
        )


class ProximidadeTests(TestCase):
    """Busca por raio — exige as funções do earthdistance no Postgres."""

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
                # O índice normalmente vem da migration 0002; recriar aqui é
                # idempotente e cobre banco reaproveitado com --keepdb. Tem de
                # ser antes de qualquer INSERT: o Postgres recusa CREATE INDEX
                # com eventos de gatilho pendentes na transação.
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

    def _cenario(self):
        """Empresa/filial e três clientes a distâncias conhecidas de um ponto."""
        from apps.cadastros.models import Cliente
        from apps.core.models import Empresa, Filial

        empresa = Empresa.objects.create(
            razao_social='Teste', cnpj='11222333000181',
            regime_tributario='simples', codigo_regime_tributario=1,
        )
        filial = Filial.objects.create(
            empresa=empresa, razao_social='Matriz', nome_fantasia='Matriz',
            cnpj='11222333000181', uf='RN', is_matriz=True,
        )
        # ~0,009 graus de latitude = ~1 km
        pontos = {
            'perto': (-5.790, -35.210),
            'medio': (-5.799, -35.210),
            'longe': (-5.880, -35.210),
        }
        for nome, (lat, lng) in pontos.items():
            Cliente.objects.create(
                filial=filial, razao_social=nome.upper(), cpf_cnpj=f'0000000000{nome[:1]}',
                cidade='Natal', uf='RN', latitude=lat, longitude=lng, ativo=True,
            )
        return filial

    def test_raio_filtra_e_ordena_por_distancia(self):
        from apps.mapas.services import ProximidadeService

        filial = self._cenario()
        achados = ProximidadeService.clientes_proximos(
            filial=filial, latitude=-5.790, longitude=-35.210, raio_m=3000,
        )
        nomes = [cl.razao_social for cl in achados]

        self.assertEqual(nomes, ['PERTO', 'MEDIO'])   # LONGE (~10km) fica fora
        self.assertLess(achados[0].distancia_m, achados[1].distancia_m)
        self.assertAlmostEqual(achados[0].distancia_m, 0, delta=50)

    def test_raio_maior_alcanca_todos(self):
        from apps.mapas.services import ProximidadeService

        filial = self._cenario()
        achados = ProximidadeService.clientes_proximos(
            filial=filial, latitude=-5.790, longitude=-35.210, raio_m=20000,
        )
        self.assertEqual(len(achados), 3)

    def test_sem_coordenada_nunca_aparece(self):
        from apps.cadastros.models import Cliente
        from apps.mapas.services import ProximidadeService

        filial = self._cenario()
        Cliente.objects.create(
            filial=filial, razao_social='SEM GEO', cpf_cnpj='00000000099',
            cidade='Natal', uf='RN', ativo=True,
        )
        achados = ProximidadeService.clientes_proximos(
            filial=filial, latitude=-5.790, longitude=-35.210, raio_m=50000,
        )
        self.assertNotIn('SEM GEO', [cl.razao_social for cl in achados])

    def test_excluir_cliente_da_propria_entrega(self):
        from apps.cadastros.models import Cliente
        from apps.mapas.services import ProximidadeService

        filial = self._cenario()
        alvo = Cliente.objects.get(razao_social='PERTO')
        achados = ProximidadeService.clientes_proximos(
            filial=filial, latitude=-5.790, longitude=-35.210, raio_m=3000,
            excluir_cliente_id=alvo.pk,
        )
        self.assertNotIn('PERTO', [cl.razao_social for cl in achados])

    def test_consulta_usa_indice_gist(self):
        """
        Garante que a busca por raio não degradou para seq scan.

        É o teste que protege a decisão de arquitetura: se alguém trocar o
        predicado `earth_box @> ll_to_earth` por um filtro só na distância
        anotada, a query continua CORRETA mas para de usar o índice — e isso
        só apareceria como lentidão em produção.
        """
        from apps.cadastros.models import Cliente
        from apps.mapas.managers import no_raio

        self._cenario()
        with connection.cursor() as cur:
            # Sem isso o planejador prefere seq scan em tabela minúscula.
            cur.execute('SET LOCAL enable_seqscan = off')

            qs = no_raio(Cliente.objects.all(), -5.790, -35.210, 3000)
            sql, params = qs.query.sql_with_params()
            cur.execute(f'EXPLAIN {sql}', params)
            plano = '\n'.join(linha[0] for linha in cur.fetchall())

        self.assertIn('clientes_geo_gist_idx', plano, f'plano sem indice GIST:\n{plano}')
