"""
Painel de indicadores do modulo de mapas (secao 14).

O ponto sensivel: km e tempo vem de rotas CALCULADAS, nao de percurso medido.
Os testes fixam de onde cada numero sai, para ninguem no futuro trocar a fonte
sem perceber que o rotulo passou a mentir.
"""
import datetime
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.mapas.services.painel import PainelService


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class BasePainel(TestCase):
    _seq = 0

    def setUp(self):
        self.filial = self._empresa('Alfa', '11222333000181')
        self.usuario = self._usuario(self.filial)
        self._logar(self.usuario, self.filial)

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

        BasePainel._seq += 1
        n = BasePainel._seq
        perfil = PerfilAcesso.objects.create(
            empresa=filial.empresa, nome=f'Perfil {n}', is_admin=True,
        )
        return Usuario.objects.create_user(
            email=f'u{n}@teste.local', nome='U', password='senha-de-teste-123',
            empresa=filial.empresa, perfil=perfil, filial=filial,
        )

    def _logar(self, usuario, filial):
        self.client.force_login(usuario)
        sessao = self.client.session
        sessao['filial_ativa_id'] = filial.pk
        sessao.save()

    def _cliente(self, nome, doc, *, lat=-5.79, filial=None):
        from apps.cadastros.models import Cliente

        return Cliente.objects.create(
            filial=filial or self.filial, razao_social=nome, cpf_cnpj=doc,
            cidade='Natal', uf='RN', latitude=lat,
            longitude=-35.21 if lat is not None else None, ativo=True,
        )

    def _entrega(self, cliente, status_delivery, *, filial=None, dias_atras=0):
        from apps.pdv.models import VendaPDV

        BasePainel._seq += 1
        return VendaPDV.objects.create(
            filial=filial or self.filial, numero_venda=BasePainel._seq,
            cliente=cliente, usuario=self.usuario, status='finalizada',
            delivery=True, status_delivery=status_delivery,
            valor_total=Decimal('10'),
            data_venda=timezone.now() - datetime.timedelta(days=dias_atras),
        )

    def _rota(self, *, distancia_m, duracao_s=600, paradas=3, filial=None,
              antes_m=None, otimizada=False):
        from apps.mapas.models import RegistroRota

        return RegistroRota.objects.create(
            filial=filial or self.filial, usuario=self.usuario,
            paradas=paradas, distancia_m=distancia_m, duracao_s=duracao_s,
            otimizada=otimizada, distancia_antes_m=antes_m,
        )

    def _ind(self, **kwargs):
        return PainelService.indicadores(self.filial, **kwargs)


class CoberturaTests(BasePainel):
    def test_conta_cadastrados_geolocalizados_e_sem_coordenada(self):
        self._cliente('COM A', '1')
        self._cliente('COM B', '2')
        self._cliente('SEM', '3', lat=None)

        d = self._ind()

        self.assertEqual(d['clientes_cadastrados'], 3)
        self.assertEqual(d['clientes_geolocalizados'], 2)
        self.assertEqual(d['clientes_sem_coordenada'], 1)
        self.assertAlmostEqual(d['cobertura_pct'], 66.7, places=1)

    def test_base_vazia_nao_divide_por_zero(self):
        d = self._ind()
        self.assertEqual(d['clientes_cadastrados'], 0)
        self.assertEqual(d['cobertura_pct'], 0.0)

    def test_cliente_de_outra_empresa_nao_entra(self):
        outra = self._empresa('Beta', '99888777000166')
        self._cliente('MEU', '1')
        self._cliente('ALHEIO', '9', filial=outra)

        self.assertEqual(self._ind()['clientes_cadastrados'], 1)


class EntregasTests(BasePainel):
    def test_conta_entregas_do_dia(self):
        c = self._cliente('A', '1')
        self._entrega(c, 'novo')
        self._entrega(c, 'em_entrega')

        self.assertEqual(self._ind()['entregas_periodo'], 2)

    def test_cancelada_nao_conta(self):
        c = self._cliente('A', '1')
        self._entrega(c, 'novo')
        self._entrega(c, 'cancelado')

        self.assertEqual(self._ind()['entregas_periodo'], 1)

    def test_visitado_e_so_quem_recebeu(self):
        """Pedido em preparo nao visitou ninguem."""
        a = self._cliente('A', '1')
        b = self._cliente('B', '2')
        self._entrega(a, 'entregue')
        self._entrega(b, 'preparando')

        d = self._ind()
        self.assertEqual(d['entregas_concluidas'], 1)
        self.assertEqual(d['clientes_visitados'], 1)

    def test_dois_pedidos_para_o_mesmo_cliente_sao_uma_visita(self):
        c = self._cliente('A', '1')
        self._entrega(c, 'entregue')
        self._entrega(c, 'finalizado')

        d = self._ind()
        self.assertEqual(d['entregas_concluidas'], 2)
        self.assertEqual(d['clientes_visitados'], 1)

    def test_entrega_de_ontem_fica_fora_do_painel_de_hoje(self):
        c = self._cliente('A', '1')
        self._entrega(c, 'entregue', dias_atras=1)

        self.assertEqual(self._ind()['entregas_periodo'], 0)

    def test_periodo_alcanca_dias_anteriores(self):
        c = self._cliente('A', '1')
        self._entrega(c, 'entregue', dias_atras=3)

        hoje = timezone.localdate()
        d = self._ind(inicio=hoje - datetime.timedelta(days=7), fim=hoje)
        self.assertEqual(d['entregas_periodo'], 1)


class RotasTests(BasePainel):
    def test_soma_km_e_tempo_das_rotas(self):
        self._rota(distancia_m=12000, duracao_s=1800)
        self._rota(distancia_m=8000, duracao_s=1800)

        d = self._ind()

        self.assertEqual(d['rotas_calculadas'], 2)
        self.assertEqual(d['km_em_rota'], 20.0)
        self.assertEqual(d['tempo_em_rota_texto'], '1h')

    def test_economia_sai_da_diferenca_de_cada_rota_otimizada(self):
        self._rota(distancia_m=8000, antes_m=10000, otimizada=True)
        self._rota(distancia_m=5000)  # sem otimizacao: nao entra na economia

        d = self._ind()
        self.assertEqual(d['economia_km'], 2.0)

    def test_otimizacao_que_nao_melhorou_nao_vira_economia_negativa(self):
        """Manter a ordem do usuario e um resultado valido, nao um prejuizo."""
        self._rota(distancia_m=10000, antes_m=10000, otimizada=True)

        self.assertEqual(self._ind()['economia_km'], 0.0)

    def test_percentual_compara_com_a_distancia_sem_otimizar(self):
        self._rota(distancia_m=8000, antes_m=10000, otimizada=True)

        self.assertEqual(self._ind()['economia_pct'], 20.0)

    def test_sem_rota_nenhuma_os_numeros_sao_zero(self):
        d = self._ind()
        self.assertEqual(d['km_em_rota'], 0)
        self.assertEqual(d['economia_km'], 0)
        self.assertEqual(d['economia_pct'], 0.0)

    def test_rota_de_outra_empresa_nao_entra(self):
        outra = self._empresa('Beta', '99888777000166')
        self._rota(distancia_m=5000)
        self._rota(distancia_m=99000, filial=outra)

        self.assertEqual(self._ind()['km_em_rota'], 5.0)


class DuracaoTests(TestCase):
    def test_formata_horas_e_minutos(self):
        self.assertEqual(PainelService.formatar_duracao(5400), '1h30')
        self.assertEqual(PainelService.formatar_duracao(3600), '1h')
        self.assertEqual(PainelService.formatar_duracao(2700), '45min')
        self.assertEqual(PainelService.formatar_duracao(0), '0min')
        self.assertEqual(PainelService.formatar_duracao(None), '0min')


class SugestoesTests(BasePainel):
    def test_soma_os_clientes_oferecidos(self):
        from apps.mapas.models import SugestaoProximidade

        SugestaoProximidade.objects.create(filial=self.filial, raio_m=3000, total=8)
        SugestaoProximidade.objects.create(filial=self.filial, raio_m=1000, total=3)

        d = self._ind()
        self.assertEqual(d['clientes_sugeridos'], 11)
        self.assertEqual(d['consultas_de_sugestao'], 2)


class RegistroAutomaticoTests(BasePainel):
    """
    O painel so tem numeros se as telas gravarem. Estes testes provam que
    gravam -- sem eles, o painel ficaria zerado e ninguem saberia por que.
    """

    def test_criar_rota_grava_um_registro(self):
        from unittest.mock import patch

        from apps.mapas.models import RegistroRota
        from apps.mapas.services.roteirizacao import Parada, Rota

        rota = Rota(
            distancia_m=15000, duracao_s=1200, geometria=[[0, 0], [1, 1]],
            paradas=[Parada(ordem=1, nome='A', lat=-5.79, lng=-35.21, cliente_id=1)],
        )
        alvo = 'apps.mapas.services.roteirizacao.RoteirizacaoService.rota_de_clientes'
        p = patch(alvo, return_value=rota)
        p.start()
        self.addCleanup(p.stop)

        resp = self.client.post(
            reverse('mapas:api-rota'), data='{"clientes": [1, 2]}',
            content_type='application/json',
        )

        self.assertEqual(resp.status_code, 200)
        registro = RegistroRota.objects.get()
        self.assertEqual(registro.distancia_m, 15000)
        self.assertFalse(registro.otimizada)

    def test_falha_ao_gravar_nao_derruba_a_rota(self):
        """O log serve a um indicador; a rota, a operacao."""
        from unittest.mock import patch

        from apps.mapas.services.roteirizacao import Parada, Rota

        rota = Rota(
            distancia_m=15000, duracao_s=1200, geometria=[[0, 0]],
            paradas=[Parada(ordem=1, nome='A', lat=-5.79, lng=-35.21, cliente_id=1)],
        )
        p1 = patch(
            'apps.mapas.services.roteirizacao.RoteirizacaoService.rota_de_clientes',
            return_value=rota)
        p1.start()
        self.addCleanup(p1.stop)
        p2 = patch('apps.mapas.models.RegistroRota.objects.create',
                   side_effect=RuntimeError('banco fora'))
        p2.start()
        self.addCleanup(p2.stop)

        resp = self.client.post(
            reverse('mapas:api-rota'), data='{"clientes": [1]}',
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['distancia_m'], 15000)


class ViewTests(BasePainel):
    def test_pagina_abre_com_o_dia_de_hoje(self):
        resp = self.client.get(reverse('mapas:painel'))

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['ind']['periodo_e_hoje'])

    def test_avisa_que_km_nao_e_percurso_medido(self):
        """
        Sem rastreamento, apresentar km planejado como percorrido levaria
        alguem a decidir sobre frota em cima de um numero que nao existe.
        """
        resp = self.client.get(reverse('mapas:painel'))
        self.assertContains(resp, 'não de percurso medido por GPS')

    def test_data_invalida_cai_no_dia_de_hoje(self):
        resp = self.client.get(reverse('mapas:painel'), {'de': 'ontem'})
        self.assertEqual(resp.status_code, 200)

    def test_periodo_invertido_e_corrigido(self):
        hoje = timezone.localdate()
        antes = hoje - datetime.timedelta(days=5)

        resp = self.client.get(reverse('mapas:painel'),
                               {'de': hoje.isoformat(), 'ate': antes.isoformat()})
        self.assertEqual(resp.context['inicio'], antes)
        self.assertEqual(resp.context['fim'], hoje)

    def test_exige_autenticacao(self):
        self.client.logout()
        resp = self.client.get(reverse('mapas:painel'))
        self.assertIn(resp.status_code, (302, 401, 403))
