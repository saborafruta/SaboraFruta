"""
Rastreamento em tempo real (secao 13).

Dois pontos sensiveis:

- **Velocidade nula nao e zero.** "Nao sei" e diferente de "parado", e trocar
  um pelo outro faz a tela afirmar algo que o sistema nao sabe.
- **O historico e filtrado.** Sem o filtro, um veiculo parado no semaforo
  geraria centenas de pontos identicos; com filtro errado, o trajeto fica com
  buracos. Os testes fixam os dois lados.
"""
import datetime

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

CENTRO = (-5.790, -35.210)


def deslocar(metros_norte):
    return (CENTRO[0] + metros_norte / 111_320, CENTRO[1])


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class BaseRastreio(TestCase):
    _seq = 0

    def setUp(self):
        self.filial = self._empresa('Alfa', '11222333000181')
        self.usuario = self._usuario(self.filial)
        self.motorista = self._motorista('Joao')
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

        BaseRastreio._seq += 1
        n = BaseRastreio._seq
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

    def _motorista(self, nome, filial=None):
        from apps.cadastros.models import Motorista

        return Motorista.objects.create(
            filial=filial or self.filial, nome=nome, ativo=True)

    @staticmethod
    def _manha(hora=8, minuto=0):
        """Hoje, em horário local fixo — evita o teste cruzar a meia-noite."""
        return timezone.make_aware(
            datetime.datetime.combine(
                timezone.localdate(), datetime.time(hora, minuto)),
            timezone.get_current_timezone(),
        )

    def _registrar(self, ponto, **kw):
        from apps.mapas.services import RastreioService

        kw.setdefault('filial', self.filial)
        kw.setdefault('motorista', self.motorista)
        return RastreioService.registrar(
            latitude=ponto[0], longitude=ponto[1], **kw)


class PosicaoTests(BaseRastreio):
    def test_guarda_uma_linha_por_motorista(self):
        """Sobrescreve: a consulta 'onde estao agora' nao pode crescer com o uso."""
        from apps.mapas.models import PosicaoMotorista

        self._registrar(deslocar(0))
        self._registrar(deslocar(500))
        self._registrar(deslocar(1000))

        self.assertEqual(PosicaoMotorista.objects.count(), 1)
        p = PosicaoMotorista.objects.get()
        self.assertAlmostEqual(p.latitude, deslocar(1000)[0], places=5)

    def test_velocidade_informada_pelo_aparelho_e_usada(self):
        p = self._registrar(deslocar(0), velocidade_kmh=42.0)
        self.assertEqual(p.velocidade_kmh, 42.0)

    def test_velocidade_ausente_e_calculada_entre_duas_posicoes(self):
        agora = self._manha()
        self._registrar(deslocar(0), momento=agora)
        # 1000 m em 60 s = 60 km/h
        p = self._registrar(deslocar(1000), momento=agora + datetime.timedelta(seconds=60))

        self.assertAlmostEqual(p.velocidade_kmh, 60.0, delta=1.0)

    def test_primeira_posicao_sem_velocidade_fica_nula(self):
        """Nao ha com o que comparar -- e zero afirmaria 'parado'."""
        p = self._registrar(deslocar(0))
        self.assertIsNone(p.velocidade_kmh)

    def test_intervalo_curto_demais_nao_vira_velocidade(self):
        """
        Em poucos segundos o erro do GPS domina a conta e produziria
        velocidades absurdas para quem esta parado.
        """
        agora = self._manha()
        self._registrar(deslocar(0), momento=agora)
        p = self._registrar(deslocar(30), momento=agora + datetime.timedelta(seconds=2))

        self.assertIsNone(p.velocidade_kmh)

    def test_guarda_a_precisao_informada(self):
        p = self._registrar(deslocar(0), precisao_m=25)
        self.assertEqual(p.precisao_m, 25)


class HistoricoTests(BaseRastreio):
    def test_parado_nao_enche_o_historico(self):
        """Veiculo no semaforo geraria centenas de pontos identicos."""
        from apps.mapas.models import PontoPercurso

        agora = self._manha()
        for i in range(10):
            self._registrar(deslocar(i), momento=agora + datetime.timedelta(seconds=5 * i))

        self.assertEqual(PontoPercurso.objects.count(), 1)

    def test_andar_o_bastante_grava_ponto_novo(self):
        from apps.mapas.models import PontoPercurso

        agora = self._manha()
        self._registrar(deslocar(0), momento=agora)
        self._registrar(deslocar(200), momento=agora + datetime.timedelta(seconds=30))

        self.assertEqual(PontoPercurso.objects.count(), 2)

    def test_muito_tempo_parado_grava_mesmo_sem_andar(self):
        """Sem isto, uma parada longa viraria um buraco no trajeto."""
        from apps.mapas.models import PontoPercurso

        agora = self._manha()
        self._registrar(deslocar(0), momento=agora)
        self._registrar(deslocar(5), momento=agora + datetime.timedelta(minutes=10))

        self.assertEqual(PontoPercurso.objects.count(), 2)

    def test_percurso_devolve_a_linha_e_a_distancia(self):
        from apps.mapas.services import RastreioService

        agora = self._manha()
        self._registrar(deslocar(0), momento=agora, velocidade_kmh=30)
        self._registrar(deslocar(1000), momento=agora + datetime.timedelta(minutes=2),
                        velocidade_kmh=50)
        self._registrar(deslocar(2000), momento=agora + datetime.timedelta(minutes=4),
                        velocidade_kmh=40)

        d = RastreioService.percurso(self.filial, self.motorista.pk)

        self.assertEqual(d['total_pontos'], 3)
        self.assertAlmostEqual(d['km'], 2.0, delta=0.1)
        self.assertEqual(d['velocidade_maxima_kmh'], 50.0)
        self.assertEqual(d['velocidade_media_kmh'], 40.0)

    def test_percurso_de_outra_empresa_nao_vaza(self):
        from apps.mapas.services import RastreioService

        outra = self._empresa('Beta', '99888777000166')
        alheio = self._motorista('Alheio', filial=outra)
        self._registrar(deslocar(0), filial=outra, motorista=alheio)

        d = RastreioService.percurso(self.filial, alheio.pk)
        self.assertEqual(d['total_pontos'], 0)

    def test_expurgo_apaga_so_o_que_e_antigo(self):
        from apps.mapas.models import PontoPercurso
        from apps.mapas.services import RastreioService

        agora = self._manha()
        self._registrar(deslocar(0), momento=agora)
        PontoPercurso.objects.create(
            motorista=self.motorista, filial=self.filial,
            latitude=CENTRO[0], longitude=CENTRO[1],
            momento=agora - datetime.timedelta(days=90),
        )

        RastreioService.expurgar(dias=30)
        self.assertEqual(PontoPercurso.objects.count(), 1)


class AoVivoTests(BaseRastreio):
    def _ao_vivo(self):
        from apps.mapas.services import RastreioService

        return RastreioService.ao_vivo(self.filial)

    def test_posicao_recente_aparece_online(self):
        self._registrar(deslocar(0), momento=timezone.now())

        m = self._ao_vivo()[0]
        self.assertTrue(m['online'])
        self.assertEqual(m['atraso_texto'], 'agora')

    def test_posicao_velha_aparece_offline_mas_nao_some(self):
        """Sumir da tela seria lido como fim de turno, e pode ser falta de sinal."""
        self._registrar(
            deslocar(0), momento=timezone.now() - datetime.timedelta(hours=2))

        m = self._ao_vivo()[0]
        self.assertFalse(m['online'])
        self.assertEqual(m['atraso_texto'], '2 h')

    def test_motorista_de_outra_empresa_nao_aparece(self):
        outra = self._empresa('Beta', '99888777000166')
        alheio = self._motorista('Alheio', filial=outra)
        self._registrar(deslocar(0), filial=outra, motorista=alheio)
        self._registrar(deslocar(100))

        self.assertEqual([m['nome'] for m in self._ao_vivo()], ['Joao'])

    def test_destino_vem_do_cliente_da_venda(self):
        from apps.cadastros.models import Cliente
        from apps.pdv.models import VendaPDV

        cliente = Cliente.objects.create(
            filial=self.filial, razao_social='PADARIA', cpf_cnpj='1',
            cidade='Natal', uf='RN', latitude=-5.80, longitude=-35.20, ativo=True)
        venda = VendaPDV.objects.create(
            filial=self.filial, numero_venda=1, cliente=cliente,
            usuario=self.usuario, status='finalizada', delivery=True,
            data_venda=timezone.now())

        self._registrar(deslocar(0), destino_venda_id=venda.pk)

        m = self._ao_vivo()[0]
        self.assertEqual(m['destino']['nome'], 'PADARIA')

    def test_sem_destino_o_campo_vem_nulo(self):
        self._registrar(deslocar(0))
        self.assertIsNone(self._ao_vivo()[0]['destino'])


class TempoDesdeTests(TestCase):
    def test_formata_a_defasagem(self):
        from apps.mapas.services import RastreioService

        self.assertEqual(RastreioService.tempo_desde(30), 'agora')
        self.assertEqual(RastreioService.tempo_desde(300), '5 min')
        self.assertEqual(RastreioService.tempo_desde(7200), '2 h')
        self.assertEqual(RastreioService.tempo_desde(180000), '2 d')


class ApiTests(BaseRastreio):
    def test_ao_vivo_devolve_os_motoristas(self):
        self._registrar(deslocar(0), velocidade_kmh=35)

        d = self.client.get(reverse('mapas:api-ao-vivo')).json()

        self.assertEqual(len(d['motoristas']), 1)
        self.assertEqual(d['motoristas'][0]['velocidade_kmh'], 35.0)

    def test_percurso_devolve_a_linha(self):
        agora = self._manha()
        self._registrar(deslocar(0), momento=agora)
        self._registrar(deslocar(500), momento=agora + datetime.timedelta(minutes=1))

        d = self.client.get(
            reverse('mapas:api-percurso', args=[self.motorista.pk])).json()
        self.assertEqual(d['total_pontos'], 2)

    def test_posicao_alimenta_o_rastreamento(self):
        """A mesma entrada do paragrafo 12 atualiza a posicao ao vivo."""
        from apps.mapas.models import PosicaoMotorista

        lat, lng = deslocar(0)
        resp = self.client.post(
            reverse('mapas:api-posicao'),
            data=('{"motorista": %d, "lat": %s, "lng": %s, '
                  '"velocidade": 10, "precisao": 12}' % (self.motorista.pk, lat, lng)),
            content_type='application/json',
        )

        self.assertEqual(resp.status_code, 200)
        p = PosicaoMotorista.objects.get()
        # 10 m/s = 36 km/h
        self.assertAlmostEqual(p.velocidade_kmh, 36.0, delta=0.1)
        self.assertEqual(p.precisao_m, 12)

    def test_destino_de_outra_empresa_e_descartado(self):
        from apps.cadastros.models import Cliente
        from apps.mapas.models import PosicaoMotorista
        from apps.pdv.models import VendaPDV

        outra = self._empresa('Beta', '99888777000166')
        cliente = Cliente.objects.create(
            filial=outra, razao_social='ALHEIO', cpf_cnpj='9',
            cidade='Recife', uf='PE', ativo=True)
        venda = VendaPDV.objects.create(
            filial=outra, numero_venda=1, cliente=cliente,
            usuario=self._usuario(outra), status='finalizada', delivery=True,
            data_venda=timezone.now())

        lat, lng = deslocar(0)
        self.client.post(
            reverse('mapas:api-posicao'),
            data=('{"motorista": %d, "lat": %s, "lng": %s, "destino_venda": %d}'
                  % (self.motorista.pk, lat, lng, venda.pk)),
            content_type='application/json',
        )

        self.assertIsNone(PosicaoMotorista.objects.get().destino_venda_id)

    def test_exige_autenticacao(self):
        self.client.logout()
        self.assertIn(
            self.client.get(reverse('mapas:api-ao-vivo')).status_code,
            (302, 401, 403))


class PaginaTests(BaseRastreio):
    def test_tela_ao_vivo_abre(self):
        self.assertEqual(self.client.get(reverse('mapas:ao-vivo')).status_code, 200)

    def test_avisa_que_a_posicao_depende_do_celular(self):
        """Sem o aviso, "sem sinal" seria lido como falha do sistema."""
        resp = self.client.get(reverse('mapas:ao-vivo'))
        self.assertContains(resp, 'aberta no celular do motorista')

    def test_pagina_do_motorista_oferece_as_entregas_abertas(self):
        from apps.cadastros.models import Cliente
        from apps.pdv.models import VendaPDV

        cliente = Cliente.objects.create(
            filial=self.filial, razao_social='PADARIA', cpf_cnpj='1',
            cidade='Natal', uf='RN', ativo=True)
        VendaPDV.objects.create(
            filial=self.filial, numero_venda=7, cliente=cliente,
            usuario=self.usuario, status='finalizada', delivery=True,
            status_delivery='em_entrega', data_venda=timezone.now())

        resp = self.client.get(reverse('mapas:rastreio'))
        self.assertContains(resp, 'PADARIA')

    def test_entrega_finalizada_nao_e_oferecida_como_destino(self):
        from apps.cadastros.models import Cliente
        from apps.pdv.models import VendaPDV

        cliente = Cliente.objects.create(
            filial=self.filial, razao_social='JA ENTREGUE', cpf_cnpj='1',
            cidade='Natal', uf='RN', ativo=True)
        VendaPDV.objects.create(
            filial=self.filial, numero_venda=7, cliente=cliente,
            usuario=self.usuario, status='finalizada', delivery=True,
            status_delivery='finalizado', data_venda=timezone.now())

        resp = self.client.get(reverse('mapas:rastreio'))
        self.assertNotContains(resp, 'JA ENTREGUE')
