"""
Otimizacao da ordem das entregas (secao 5).

O ganho e medido roteirizando as DUAS ordens no mesmo provider: comparar
distancia em linha reta daria um numero bonito e errado, porque o motorista
anda por rua.
"""
from unittest.mock import patch

from django.test import TestCase

from apps.mapas.services.otimizacao import (
    Otimizacao, OtimizacaoService, OtimizadorLocal, VROOMOtimizador,
    construir_otimizador, distancia_haversine_m, otimizar_local,
)
from apps.mapas.services.roteirizacao import Rota, RoteirizadorBase


class HaversineTests(TestCase):
    def test_mesmo_ponto_da_zero(self):
        self.assertEqual(distancia_haversine_m((-5.79, -35.21), (-5.79, -35.21)), 0)

    def test_um_grau_de_latitude_e_cerca_de_111km(self):
        d = distancia_haversine_m((0, 0), (1, 0))
        self.assertAlmostEqual(d / 1000, 111.19, delta=0.5)


class OtimizadorLocalTests(TestCase):
    def test_reordena_roteiro_com_zigue_zague(self):
        """
        Pontos em linha, informados fora de ordem. A ordem otimizada tem de
        percorrer menos que a original.
        """
        pontos = [
            (0.00, 0.0),   # origem
            (0.30, 0.0),   # longe
            (0.10, 0.0),   # perto
            (0.20, 0.0),   # medio
        ]
        ordem = otimizar_local(pontos, fixar_primeiro=True)

        self.assertEqual(ordem[0], 0, 'a origem tem de continuar em primeiro')
        self.assertEqual(ordem, [0, 2, 3, 1])

    def test_ordem_ja_otima_e_preservada(self):
        pontos = [(0.0, 0.0), (0.1, 0.0), (0.2, 0.0), (0.3, 0.0)]
        self.assertEqual(otimizar_local(pontos), [0, 1, 2, 3])

    def test_nao_perde_nem_duplica_paradas(self):
        pontos = [(0.0, 0.0), (0.5, 0.3), (0.1, 0.9), (0.7, 0.2), (0.2, 0.1)]
        ordem = otimizar_local(pontos)
        self.assertEqual(sorted(ordem), list(range(len(pontos))))

    def test_dois_pontos_nao_tem_o_que_otimizar(self):
        self.assertEqual(otimizar_local([(0.0, 0.0), (1.0, 1.0)]), [0, 1])

    def test_sem_fixar_primeiro_a_origem_pode_sair_do_lugar(self):
        pontos = [(0.9, 0.0), (0.0, 0.0), (0.1, 0.0)]
        ordem = otimizar_local(pontos, fixar_primeiro=False)
        self.assertEqual(sorted(ordem), [0, 1, 2])


class ProviderTests(TestCase):
    def test_sem_configuracao_usa_o_local(self):
        with self.settings(MAPAS_VROOM_URL='', MAPAS_ROTA_API_KEY=''):
            self.assertIsInstance(construir_otimizador(), OtimizadorLocal)

    def test_chave_do_ors_habilita_vroom(self):
        """No ORS, /optimization e VROOM: a chave da secao 4 ja serve."""
        with self.settings(MAPAS_VROOM_URL='', MAPAS_ROTA_API_KEY='k'):
            otim = construir_otimizador()
        self.assertIsInstance(otim, VROOMOtimizador)
        self.assertTrue(otim.via_ors)

    def test_vroom_proprio(self):
        with self.settings(MAPAS_VROOM_URL='https://vroom.minhaempresa.com'):
            otim = construir_otimizador()
        self.assertIsInstance(otim, VROOMOtimizador)
        self.assertFalse(otim.via_ors)


class VROOMTests(TestCase):
    def _mock(self, payload):
        alvo = patch('apps.mapas.services.otimizacao.requests.post')
        mock = alvo.start()
        self.addCleanup(alvo.stop)
        mock.return_value.json.return_value = payload
        mock.return_value.raise_for_status.return_value = None
        return mock

    def test_le_a_ordem_dos_steps(self):
        self._mock({'routes': [{'steps': [
            {'type': 'start'},
            {'type': 'job', 'id': 2},
            {'type': 'job', 'id': 1},
            {'type': 'end'},
        ]}]})
        ordem = VROOMOtimizador(api_key='k').ordenar(
            [(0.0, 0.0), (0.1, 0.1), (0.2, 0.2)], fixar_primeiro=True,
        )
        self.assertEqual(ordem, [0, 2, 1])

    def test_manda_lon_lat(self):
        """Mesma inversao do OSRM; errar aqui manda a rota para outro lugar."""
        mock = self._mock({'routes': [{'steps': [{'type': 'job', 'id': 1}]}]})
        VROOMOtimizador(api_key='k').ordenar([(-5.79, -35.21), (-5.80, -35.22)])

        corpo = mock.call_args.kwargs['json']
        self.assertEqual(corpo['vehicles'][0]['start'], [-35.21, -5.79])
        self.assertEqual(corpo['jobs'][0]['location'], [-35.22, -5.80])

    def test_paradas_faltando_e_recusado(self):
        """Provider devolvendo menos paradas faria o usuario perder entregas."""
        self._mock({'routes': [{'steps': [{'type': 'job', 'id': 1}]}]})
        with self.assertRaises(ValueError):
            VROOMOtimizador(api_key='k').ordenar(
                [(0.0, 0.0), (0.1, 0.1), (0.2, 0.2)],
            )


class _RoteirizadorFalso(RoteirizadorBase):
    """Distancia proporcional ao caminho em linha reta, para o teste medir ganho."""

    nome = 'falso'
    permite_uso_comercial = True

    def rota(self, pontos):
        total = sum(
            distancia_haversine_m(pontos[i], pontos[i + 1])
            for i in range(len(pontos) - 1)
        )
        return Rota(
            distancia_m=total, duracao_s=total / 10,
            geometria=[[p[0], p[1]] for p in pontos],
        )


class OtimizacaoServiceTests(TestCase):
    def setUp(self):
        from apps.core.models import Empresa, Filial
        from apps.mapas.services.roteirizacao import RoteirizacaoService

        self.empresa = Empresa.objects.create(
            razao_social='T', cnpj='11222333000181',
            regime_tributario='simples', codigo_regime_tributario=1,
        )
        self.filial = Filial.objects.create(
            empresa=self.empresa, razao_social='M', nome_fantasia='Matriz',
            cnpj='11222333000181', uf='RN', is_matriz=True,
            latitude=0.0, longitude=0.0,
        )
        self.servico = OtimizacaoService(
            otimizador=OtimizadorLocal(),
            roteirizacao=RoteirizacaoService(roteirizador=_RoteirizadorFalso()),
        )

    def _cliente(self, nome, lat, cpf):
        from apps.cadastros.models import Cliente

        return Cliente.objects.create(
            filial=self.filial, razao_social=nome, cpf_cnpj=cpf,
            cidade='Natal', uf='RN', latitude=lat, longitude=0.0, ativo=True,
        )

    def test_reordena_e_reporta_economia(self):
        longe = self._cliente('LONGE', 0.30, '1')
        perto = self._cliente('PERTO', 0.10, '2')
        medio = self._cliente('MEDIO', 0.20, '3')

        r = self.servico.otimizar(
            filial=self.filial, cliente_ids=[longe.pk, perto.pk, medio.pk],
        )

        self.assertTrue(r.ok)
        self.assertTrue(r.melhorou)
        self.assertEqual(r.ordem_depois, [perto.pk, medio.pk, longe.pk])
        self.assertGreater(r.economia_km, 0)

    def test_ordem_ja_otima_nao_e_mexida(self):
        """Sem ganho, mantem o roteiro do usuario em vez de embaralhar."""
        a = self._cliente('A', 0.10, '1')
        b = self._cliente('B', 0.20, '2')
        c = self._cliente('C', 0.30, '3')

        r = self.servico.otimizar(filial=self.filial, cliente_ids=[a.pk, b.pk, c.pk])

        self.assertEqual(r.ordem_depois, r.ordem_antes)
        self.assertEqual(r.economia_km, 0)
        self.assertFalse(r.melhorou)

    def test_economia_nunca_e_negativa(self):
        r = Otimizacao(
            rota_antes=Rota(distancia_m=100, duracao_s=10),
            rota_depois=Rota(distancia_m=500, duracao_s=50),
        )
        self.assertEqual(r.economia_m, 0)
        self.assertEqual(r.economia_s, 0)

    def test_provider_fora_do_ar_cai_para_o_local(self):
        """Otimizador indisponivel nao pode deixar o usuario sem o recurso."""
        from apps.mapas.services.roteirizacao import RoteirizacaoService

        class _Quebrado(OtimizadorLocal):
            nome = 'quebrado'

            def ordenar(self, pontos, fixar_primeiro=True):
                raise RuntimeError('provider caiu')

        servico = OtimizacaoService(
            otimizador=_Quebrado(),
            roteirizacao=RoteirizacaoService(roteirizador=_RoteirizadorFalso()),
        )
        longe = self._cliente('LONGE', 0.30, '1')
        perto = self._cliente('PERTO', 0.10, '2')

        r = servico.otimizar(filial=self.filial, cliente_ids=[longe.pk, perto.pk])

        self.assertTrue(r.ok)
        self.assertEqual(r.estrategia, 'local (fallback)')

    def test_lista_vazia_vira_erro(self):
        r = self.servico.otimizar(filial=self.filial, cliente_ids=[])
        self.assertFalse(r.ok)

    def test_economia_em_texto(self):
        r = Otimizacao(
            rota_antes=Rota(distancia_m=5000, duracao_s=5400),
            rota_depois=Rota(distancia_m=2000, duracao_s=1800),
        )
        self.assertEqual(r.economia_km, 3.0)
        self.assertEqual(r.economia_texto, '1h00')
