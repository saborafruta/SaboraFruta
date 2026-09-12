"""
Provedor dos tiles do mapa (o fundo com ruas/bairros por trás dos pinos).

As quatro telas com mapa apontavam direto para
`{s}.tile.openstreetmap.org` -- os servidores públicos e voluntários do
OSM, que TÊM política de uso proibindo justamente isso: carregar tiles
direto de um app comercial, em produção, sem cache próprio. O resultado
foi o bloqueio (ver captura do usuário: "App is not following the tile
usage policy... osm.wiki/Blocked") -- toda tela de mapa parou de mostrar
o fundo.

Trocado para o CARTO (basemaps.cartocdn.com), que permite exatamente este
uso sem chave de API. Estes testes travam a URL: ninguém deveria
reintroduzir o domínio bloqueado copiando um bloco antigo de outra tela.
"""
from django.test import TestCase, override_settings
from django.urls import reverse

DOMINIO_BLOQUEADO = 'tile.openstreetmap.org'
DOMINIO_ATUAL = 'basemaps.cartocdn.com'


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class TileProviderBase(TestCase):

    def setUp(self):
        from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario

        empresa = Empresa.objects.create(
            razao_social='Mapas Tiles LTDA', cnpj='21222333000181',
            regime_tributario='simples', codigo_regime_tributario=1,
        )
        self.filial = Filial.objects.create(
            empresa=empresa, razao_social='Mapas Tiles LTDA',
            nome_fantasia='Mapas Tiles', cnpj='21222333000181',
            uf='RN', is_matriz=True,
        )
        perfil = PerfilAcesso.objects.create(
            empresa=empresa, nome='Admin', is_admin=True,
        )
        self.usuario = Usuario.objects.create_user(
            email='tiles@teste.local', nome='Fulano', password='senha-de-teste-123',
            empresa=empresa, perfil=perfil, filial=self.filial,
        )
        self.client.force_login(self.usuario)
        sessao = self.client.session
        sessao['filial_ativa_id'] = self.filial.pk
        sessao.save()

    def _assert_tile_ok(self, url_name, *args):
        html = self.client.get(reverse(url_name, args=args)).content.decode()
        self.assertNotIn(DOMINIO_BLOQUEADO, html)
        self.assertIn(DOMINIO_ATUAL, html)


class TileProviderTests(TileProviderBase):

    def test_mapa_principal_nao_usa_o_dominio_bloqueado(self):
        self._assert_tile_ok('mapas:mapa')

    def test_ao_vivo_nao_usa_o_dominio_bloqueado(self):
        self._assert_tile_ok('mapas:ao-vivo')

    def test_geofence_novo_nao_usa_o_dominio_bloqueado(self):
        self._assert_tile_ok('mapas:geofence-novo')

    def test_rastreio_nao_usa_o_dominio_bloqueado(self):
        self._assert_tile_ok('mapas:rastreio')
