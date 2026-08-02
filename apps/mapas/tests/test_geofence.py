"""
Cercas virtuais (secao 12).

O centro do teste e a maquina de estados: uma posicao so vira evento quando o
estado muda. Errar isso nao quebra nada visivelmente -- gera eventos demais ou
de menos, e o relatorio fica plausivel e errado.
"""
import datetime
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

# ~0,009 grau de latitude = ~1 km. Serve para posicionar pontos a distancias
# conferiveis a olho.
CENTRO = (-5.790, -35.210)


def deslocar(metros_norte):
    """Ponto a `metros_norte` do centro, na mesma longitude."""
    return (CENTRO[0] + metros_norte / 111_320, CENTRO[1])


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class BaseGeofence(TestCase):
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

    def _usuario(self, filial, *, admin=True):
        from apps.core.models import PerfilAcesso, Usuario

        BaseGeofence._seq += 1
        n = BaseGeofence._seq
        perfil = PerfilAcesso.objects.create(
            empresa=filial.empresa, nome=f'Perfil {n}', is_admin=admin,
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
            filial=filial or self.filial, nome=nome, ativo=True,
        )

    def _cerca(self, nome='Deposito', raio=300, ponto=CENTRO, filial=None,
               ativo=True):
        from apps.mapas.models import Geofence

        return Geofence.objects.create(
            filial=filial or self.filial, nome=nome,
            latitude=ponto[0], longitude=ponto[1], raio_m=raio, ativo=ativo,
        )

    def _posicao(self, ponto, *, motorista=None, momento=None, filial=None):
        from apps.mapas.services import GeofenceService

        return GeofenceService.processar_posicao(
            filial=filial or self.filial,
            motorista=motorista or self.motorista,
            latitude=ponto[0], longitude=ponto[1], momento=momento,
        )

    def _tipos(self):
        from apps.mapas.models import EventoGeofence

        return list(
            EventoGeofence.objects.order_by('momento', 'id').values_list('tipo', flat=True)
        )


class TravessiaTests(BaseGeofence):
    def test_entrar_registra_entrada(self):
        self._cerca(raio=300)
        eventos = self._posicao(deslocar(100))

        self.assertEqual(len(eventos), 1)
        self.assertEqual(eventos[0].tipo, 'entrada')

    def test_sair_registra_saida(self):
        self._cerca(raio=300)
        self._posicao(deslocar(100))
        eventos = self._posicao(deslocar(2000))

        self.assertEqual(len(eventos), 1)
        self.assertEqual(eventos[0].tipo, 'saida')
        self.assertEqual(self._tipos(), ['entrada', 'saida'])

    def test_ficar_dentro_nao_gera_evento_novo(self):
        """
        A posicao chega a cada poucos segundos. Sem esta regra, uma parada de
        20 minutos viraria centenas de linhas identicas.
        """
        self._cerca(raio=300)
        self._posicao(deslocar(100))

        for _ in range(5):
            self.assertEqual(self._posicao(deslocar(120)), [])

        self.assertEqual(self._tipos(), ['entrada'])

    def test_ficar_fora_nunca_gera_evento(self):
        self._cerca(raio=300)
        for _ in range(3):
            self.assertEqual(self._posicao(deslocar(5000)), [])
        self.assertEqual(self._tipos(), [])

    def test_ciclo_completo_de_duas_visitas(self):
        self._cerca(raio=300)
        self._posicao(deslocar(50))      # entra
        self._posicao(deslocar(3000))    # sai
        self._posicao(deslocar(50))      # entra de novo
        self._posicao(deslocar(3000))    # sai de novo

        self.assertEqual(self._tipos(), ['entrada', 'saida', 'entrada', 'saida'])

    def test_guarda_a_distancia_do_centro_no_evento(self):
        """Permite conferir depois se o disparo foi na borda ou bem dentro."""
        self._cerca(raio=300)
        evento = self._posicao(deslocar(100))[0]

        self.assertAlmostEqual(evento.distancia_m, 100, delta=5)


class HistereseTests(BaseGeofence):
    """
    O GPS de celular oscila dezenas de metros mesmo parado. Sem margem, quem
    para exatamente na borda geraria uma enxurrada de entrada/saida e o
    relatorio viraria ruido.
    """

    def test_oscilar_na_borda_nao_gera_vaivem(self):
        self._cerca(raio=300)
        self._posicao(deslocar(280))     # entra

        # Oscilacoes dentro da margem de saida: nada muda.
        for d in (305, 290, 330, 310, 295):
            self.assertEqual(self._posicao(deslocar(d)), [])

        self.assertEqual(self._tipos(), ['entrada'])

    def test_afastar_de_verdade_registra_a_saida(self):
        self._cerca(raio=300)
        self._posicao(deslocar(280))
        eventos = self._posicao(deslocar(500))

        self.assertEqual([e.tipo for e in eventos], ['saida'])

    def test_a_margem_nao_facilita_a_entrada(self):
        """
        A folga vale so para sair. Se valesse para entrar, a cerca seria
        maior que o raio configurado e os 300 m nao seriam 300 m.
        """
        self._cerca(raio=300)
        self.assertEqual(self._posicao(deslocar(340)), [])


class VariasCercasTests(BaseGeofence):
    def test_cada_cerca_tem_estado_proprio(self):
        self._cerca('Perto', raio=300)
        self._cerca('Longe', raio=300, ponto=deslocar(5000))

        eventos = self._posicao(deslocar(50))
        self.assertEqual([e.geofence.nome for e in eventos], ['Perto'])

    def test_cercas_concentricas_disparam_juntas(self):
        self._cerca('Grande', raio=1000)
        self._cerca('Pequena', raio=200)

        eventos = self._posicao(deslocar(100))
        self.assertEqual(
            sorted(e.geofence.nome for e in eventos), ['Grande', 'Pequena'])

    def test_sair_da_pequena_sem_sair_da_grande(self):
        self._cerca('Grande', raio=1000)
        self._cerca('Pequena', raio=200)
        self._posicao(deslocar(100))

        eventos = self._posicao(deslocar(500))
        self.assertEqual([(e.geofence.nome, e.tipo) for e in eventos],
                         [('Pequena', 'saida')])

    def test_cerca_inativa_e_ignorada(self):
        self._cerca('Desligada', raio=300, ativo=False)
        self.assertEqual(self._posicao(deslocar(50)), [])

    def test_motoristas_nao_compartilham_estado(self):
        """O segundo motorista tem de gerar a propria entrada."""
        self._cerca(raio=300)
        outro = self._motorista('Maria')

        self._posicao(deslocar(50))
        eventos = self._posicao(deslocar(50), motorista=outro)

        self.assertEqual([e.tipo for e in eventos], ['entrada'])


class EscopoTests(BaseGeofence):
    def test_cerca_de_outra_empresa_nao_dispara(self):
        outra = self._empresa('Beta', '99888777000166')
        self._cerca('Alheia', raio=300, filial=outra)

        self.assertEqual(self._posicao(deslocar(50)), [])

    def test_api_recusa_motorista_de_outra_empresa(self):
        outra = self._empresa('Beta', '99888777000166')
        alheio = self._motorista('Alheio', filial=outra)

        resp = self.client.post(
            reverse('mapas:api-posicao'),
            data=f'{{"motorista": {alheio.pk}, "lat": -5.79, "lng": -35.21}}',
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 404)


class ApiPosicaoTests(BaseGeofence):
    def _post(self, corpo):
        return self.client.post(
            reverse('mapas:api-posicao'), data=corpo,
            content_type='application/json',
        )

    def test_posicao_valida_devolve_os_eventos(self):
        self._cerca('Deposito', raio=300)
        lat, lng = deslocar(50)

        d = self._post(f'{{"motorista": {self.motorista.pk}, "lat": {lat}, "lng": {lng}}}').json()

        self.assertTrue(d['ok'])
        self.assertEqual(len(d['eventos']), 1)
        self.assertEqual(d['eventos'][0]['cerca'], 'Deposito')
        self.assertEqual(d['eventos'][0]['tipo'], 'entrada')

    def test_posicao_sem_cerca_por_perto_devolve_lista_vazia(self):
        d = self._post(f'{{"motorista": {self.motorista.pk}, "lat": -5.79, "lng": -35.21}}').json()
        self.assertEqual(d['eventos'], [])

    def test_coordenada_ausente_e_400(self):
        self.assertEqual(self._post('{"motorista": 1}').status_code, 400)

    def test_coordenada_fora_do_brasil_e_400(self):
        """Lat/lng trocados caem aqui, em vez de virar uma cerca no oceano."""
        resp = self._post(
            f'{{"motorista": {self.motorista.pk}, "lat": 48.85, "lng": 2.35}}')
        self.assertEqual(resp.status_code, 400)

    def test_json_invalido_e_400(self):
        self.assertEqual(self._post('nao e json').status_code, 400)

    def test_get_nao_e_aceito(self):
        self.assertEqual(self.client.get(reverse('mapas:api-posicao')).status_code, 405)

    def test_exige_autenticacao(self):
        self.client.logout()
        resp = self._post('{"motorista": 1, "lat": -5.79, "lng": -35.21}')
        self.assertIn(resp.status_code, (302, 401, 403))


class VisitasTests(BaseGeofence):
    """Eventos emparelhados: e o par que responde 'quanto tempo ficou la'."""

    def _visitas(self, **kw):
        from apps.mapas.services import GeofenceService

        return GeofenceService.visitas(self.filial, **kw)['visitas']

    @staticmethod
    def _manha():
        """
        Hoje as 8h locais.

        `timezone.now()` como ancora tornaria o teste dependente da hora em
        que a suite roda: perto da meia-noite, "duas horas depois" cai no dia
        seguinte e sai do filtro de data.
        """
        import datetime as dt

        return timezone.make_aware(
            dt.datetime.combine(timezone.localdate(), dt.time(8, 0)),
            timezone.get_current_timezone(),
        )

    def test_empareia_entrada_com_saida_e_calcula_a_permanencia(self):
        self._cerca('Deposito', raio=300)
        agora = self._manha()

        self._posicao(deslocar(50), momento=agora)
        self._posicao(deslocar(3000), momento=agora + datetime.timedelta(minutes=45))

        v = self._visitas()
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0]['permanencia_s'], 2700)
        self.assertEqual(v[0]['permanencia'], '45min')
        self.assertFalse(v[0]['em_aberto'])

    def test_entrada_sem_saida_aparece_em_aberto(self):
        """Omitir a linha esconderia tanto 'ainda la' quanto 'rastreio caiu'."""
        self._cerca('Deposito', raio=300)
        self._posicao(deslocar(50))

        v = self._visitas()
        self.assertEqual(len(v), 1)
        self.assertTrue(v[0]['em_aberto'])
        self.assertEqual(v[0]['permanencia'], '—')

    def test_duas_visitas_no_mesmo_dia_viram_duas_linhas(self):
        self._cerca('Deposito', raio=300)
        agora = self._manha()

        self._posicao(deslocar(50), momento=agora)
        self._posicao(deslocar(3000), momento=agora + datetime.timedelta(minutes=10))
        self._posicao(deslocar(50), momento=agora + datetime.timedelta(hours=2))
        self._posicao(deslocar(3000), momento=agora + datetime.timedelta(hours=3))

        self.assertEqual(len(self._visitas()), 2)

    def test_filtra_por_motorista(self):
        self._cerca('Deposito', raio=300)
        outro = self._motorista('Maria')
        self._posicao(deslocar(50))
        self._posicao(deslocar(50), motorista=outro)

        v = self._visitas(motorista_id=outro.pk)
        self.assertEqual([x['motorista'] for x in v], ['Maria'])

    def test_visita_de_outra_empresa_nao_aparece(self):
        outra = self._empresa('Beta', '99888777000166')
        cerca_alheia = self._cerca('Alheia', raio=300, filial=outra)
        motorista_alheio = self._motorista('Alheio', filial=outra)

        from apps.mapas.models import EventoGeofence
        EventoGeofence.objects.create(
            geofence=cerca_alheia, motorista=motorista_alheio, tipo='entrada',
            momento=timezone.now(), latitude=CENTRO[0], longitude=CENTRO[1],
        )
        self.assertEqual(self._visitas(), [])


class PaginasTests(BaseGeofence):
    def test_telas_abrem(self):
        self._cerca('Deposito')
        for nome in ('mapas:geofence-list', 'mapas:geofence-novo',
                     'mapas:geofence-eventos', 'mapas:rastreio'):
            with self.subTest(nome=nome):
                self.assertEqual(self.client.get(reverse(nome)).status_code, 200)

    def test_lista_avisa_que_precisa_de_fonte_de_posicao(self):
        """Sem o aviso, alguem cadastra dez cercas e so depois descobre."""
        resp = self.client.get(reverse('mapas:geofence-list'))
        self.assertContains(resp, 'só dispara se alguém informar onde o motorista está')

    def test_rastreio_avisa_que_a_pagina_precisa_ficar_aberta(self):
        resp = self.client.get(reverse('mapas:rastreio'))
        self.assertContains(resp, 'Deixe esta página aberta')

    def test_criar_cerca_pela_tela(self):
        from apps.mapas.models import Geofence

        resp = self.client.post(reverse('mapas:geofence-novo'), {
            'nome': 'Nova', 'latitude': '-5.79', 'longitude': '-35.21',
            'raio_m': '300', 'ativo': 'on', 'observacao': '',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Geofence.objects.get().nome, 'Nova')

    def test_select_de_cliente_nao_lista_outra_empresa(self):
        """ModelForm por `fields` monta o select com a base inteira."""
        from apps.cadastros.models import Cliente

        outra = self._empresa('Beta', '99888777000166')
        meu = Cliente.objects.create(
            filial=self.filial, razao_social='MEU', cpf_cnpj='1',
            cidade='Natal', uf='RN', latitude=-5.79, longitude=-35.21, ativo=True)
        alheio = Cliente.objects.create(
            filial=outra, razao_social='ALHEIO', cpf_cnpj='9',
            cidade='Recife', uf='PE', latitude=-8.05, longitude=-34.9, ativo=True)

        resp = self.client.get(reverse('mapas:geofence-novo'))
        opcoes = list(resp.context['form'].fields['cliente'].queryset)

        self.assertIn(meu, opcoes)
        self.assertNotIn(alheio, opcoes)

    def test_editar_cerca_de_outra_empresa_e_404(self):
        outra = self._empresa('Beta', '99888777000166')
        alheia = self._cerca('Alheia', filial=outra)

        resp = self.client.get(reverse('mapas:geofence-editar', args=[alheia.pk]))
        self.assertEqual(resp.status_code, 404)
