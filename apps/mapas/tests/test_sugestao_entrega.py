"""
Sugestao de clientes proximos a entrega (secao 8).

Cobre a API que o Kanban de delivery consome: de onde sai a coordenada da
entrega, o que acontece sem coordenada, o escopo entre empresas e a permissao.
"""
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class BaseSugestao(TestCase):
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

    _seq = 0

    def _usuario(self, filial, *, admin=True):
        """Cada chamada cria um usuário novo — e-mail e perfil são únicos."""
        from apps.core.models import PerfilAcesso, Usuario

        BaseSugestao._seq += 1
        n = BaseSugestao._seq
        perfil = PerfilAcesso.objects.create(
            empresa=filial.empresa, nome=f'Perfil {n}', is_admin=admin,
        )
        return Usuario.objects.create_user(
            email=f'u{n}@teste.local', nome='U',
            password='senha-de-teste-123',
            empresa=filial.empresa, perfil=perfil, filial=filial,
        )

    def _logar(self, usuario, filial):
        self.client.force_login(usuario)
        sessao = self.client.session
        sessao['filial_ativa_id'] = filial.pk
        sessao.save()

    def _cliente(self, nome, lat, doc, filial=None):
        from apps.cadastros.models import Cliente

        return Cliente.objects.create(
            filial=filial or self.filial, razao_social=nome, cpf_cnpj=doc,
            cidade='Natal', uf='RN', latitude=lat, longitude=-35.210, ativo=True,
        )

    def _venda(self, cliente, *, numero=1, entrega=None, filial=None, delivery=True):
        from django.utils import timezone

        from apps.pdv.models import VendaPDV

        filial = filial or self.filial
        return VendaPDV.objects.create(
            filial=filial, numero_venda=numero, cliente=cliente,
            usuario=self._usuario(filial, admin=False),
            delivery=delivery, status='finalizada', data_venda=timezone.now(),
            endereco_entrega=entrega or {},
        )

    def _sugerir(self, venda, **params):
        return self.client.get(
            reverse('mapas:api-sugestao-entrega', args=[venda.pk]), params,
        )


class CoordenadaDaEntregaTests(BaseSugestao):
    def test_usa_a_coordenada_do_cliente_quando_a_venda_nao_tem(self):
        destinatario = self._cliente('DESTINO', -5.790, '1')
        self._cliente('VIZINHO', -5.7905, '2')
        venda = self._venda(destinatario)

        d = self._sugerir(venda, raio=3000).json()

        self.assertEqual(d['centro'], {'lat': -5.790, 'lng': -35.210})
        self.assertEqual([c['nome'] for c in d['clientes']], ['VIZINHO'])

    def test_endereco_da_venda_tem_prioridade_sobre_o_cadastro(self):
        """O operador pode ter ajustado o ponto no checkout."""
        destinatario = self._cliente('DESTINO', -5.900, '1')
        self._cliente('VIZINHO DO AJUSTE', -5.7905, '2')
        venda = self._venda(
            destinatario, entrega={'latitude': -5.790, 'longitude': -35.210},
        )

        d = self._sugerir(venda, raio=3000).json()

        self.assertEqual(d['centro']['lat'], -5.790)
        self.assertEqual([c['nome'] for c in d['clientes']], ['VIZINHO DO AJUSTE'])

    def test_destinatario_nao_aparece_na_propria_sugestao(self):
        """A 0 m de si mesmo ele nao acrescenta nada."""
        destinatario = self._cliente('DESTINO', -5.790, '1')
        venda = self._venda(destinatario)

        nomes = [c['nome'] for c in self._sugerir(venda, raio=3000).json()['clientes']]
        self.assertNotIn('DESTINO', nomes)

    def test_ordena_do_mais_perto_para_o_mais_longe(self):
        destinatario = self._cliente('DESTINO', -5.790, '1')
        self._cliente('LONGE', -5.808, '2')
        self._cliente('PERTO', -5.7905, '3')
        venda = self._venda(destinatario)

        nomes = [c['nome'] for c in self._sugerir(venda, raio=5000).json()['clientes']]
        self.assertEqual(nomes, ['PERTO', 'LONGE'])


class SemCoordenadaTests(BaseSugestao):
    def test_sem_coordenada_explica_o_motivo_em_200(self):
        """
        Um 4xx viraria so um "falhou" generico. A saida aqui e geocodificar o
        cliente, e a tela precisa poder dizer isso.
        """
        sem_geo = self._cliente('SEM GEO', None, '1')
        venda = self._venda(sem_geo)

        resp = self._sugerir(venda, raio=3000)
        d = resp.json()

        self.assertEqual(resp.status_code, 200)
        self.assertIn('coordenada', d['motivo'])
        self.assertEqual(d['clientes'], [])
        self.assertIsNone(d['centro'])

    def test_venda_sem_cliente_tambem_e_tratada(self):
        venda = self._venda(None)
        self.assertEqual(self._sugerir(venda).status_code, 200)
        self.assertTrue(self._sugerir(venda).json()['motivo'])


class EscopoTests(BaseSugestao):
    def test_pedido_de_outra_empresa_e_404(self):
        outra = self._empresa('Beta', '99888777000166')
        alheio = self._cliente('ALHEIO', -5.790, '9', filial=outra)
        venda = self._venda(alheio, filial=outra)

        self.assertEqual(self._sugerir(venda).status_code, 404)

    def test_cliente_de_outra_empresa_nao_entra_na_sugestao(self):
        outra = self._empresa('Beta', '99888777000166')
        destinatario = self._cliente('DESTINO', -5.790, '1')
        self._cliente('MEU VIZINHO', -5.7905, '2')
        self._cliente('VIZINHO ALHEIO', -5.7906, '3', filial=outra)
        venda = self._venda(destinatario)

        nomes = [c['nome'] for c in self._sugerir(venda, raio=3000).json()['clientes']]
        self.assertEqual(nomes, ['MEU VIZINHO'])

    def test_venda_que_nao_e_delivery_e_404(self):
        cliente = self._cliente('BALCAO', -5.790, '1')
        venda = self._venda(cliente, delivery=False)

        self.assertEqual(self._sugerir(venda).status_code, 404)

    def test_exige_autenticacao(self):
        cliente = self._cliente('X', -5.790, '1')
        venda = self._venda(cliente)
        self.client.logout()

        self.assertIn(self._sugerir(venda).status_code, (302, 401, 403))

    def test_permissao_e_a_do_pdv_nao_a_de_mapas(self):
        """
        Quem consome e o Kanban: exigir `mapas.ver` esconderia a sugestao de
        quem esta com o pedido na mao.
        """
        from apps.core.models import Permissao

        cliente = self._cliente('DESTINO', -5.790, '1')
        self._cliente('VIZINHO', -5.7905, '2')
        venda = self._venda(cliente)

        comum = self._usuario(self.filial, admin=False)
        Permissao.objects.create(perfil=comum.perfil, modulo='pdv', pode_ver=True)
        self._logar(comum, self.filial)

        resp = self._sugerir(venda, raio=3000)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([c['nome'] for c in resp.json()['clientes']], ['VIZINHO'])


class RaioTests(BaseSugestao):
    def test_raio_menor_corta_os_distantes(self):
        destinatario = self._cliente('DESTINO', -5.790, '1')
        self._cliente('PERTO', -5.7905, '2')
        self._cliente('LONGE', -5.880, '3')
        venda = self._venda(destinatario)

        nomes = [c['nome'] for c in self._sugerir(venda, raio=1000).json()['clientes']]
        self.assertEqual(nomes, ['PERTO'])

    def test_raio_ausente_usa_o_padrao(self):
        destinatario = self._cliente('DESTINO', -5.790, '1')
        venda = self._venda(destinatario)

        from apps.mapas import constants as c
        self.assertEqual(self._sugerir(venda).json()['raio_m'], c.RAIO_PADRAO_M)


class RotaPorIdsTests(BaseSugestao):
    """`ids=` no endpoint de destinos -- como o mapa resolve a rota da URL."""

    def test_resolve_os_ids_pedidos(self):
        a = self._cliente('ALFA', -5.790, '1')
        b = self._cliente('BETA', -5.791, '2')
        self._cliente('FORA DA ROTA', -5.792, '3')

        resp = self.client.get(
            reverse('mapas:api-distancia-destinos'),
            {'tipo': 'cliente', 'ids': f'{a.pk},{b.pk}'},
        )
        nomes = sorted(r['nome'] for r in resp.json()['resultados'])
        self.assertEqual(nomes, ['ALFA', 'BETA'])

    def test_id_de_outra_empresa_e_descartado(self):
        outra = self._empresa('Beta', '99888777000166')
        meu = self._cliente('MEU', -5.790, '1')
        alheio = self._cliente('ALHEIO', -5.791, '9', filial=outra)

        resp = self.client.get(
            reverse('mapas:api-distancia-destinos'),
            {'tipo': 'cliente', 'ids': f'{meu.pk},{alheio.pk}'},
        )
        self.assertEqual([r['nome'] for r in resp.json()['resultados']], ['MEU'])

    def test_lixo_no_parametro_nao_derruba_a_lista(self):
        meu = self._cliente('MEU', -5.790, '1')

        resp = self.client.get(
            reverse('mapas:api-distancia-destinos'),
            {'tipo': 'cliente', 'ids': f'abc,,{meu.pk},-1'},
        )
        self.assertEqual([r['nome'] for r in resp.json()['resultados']], ['MEU'])

    def test_ids_acima_do_limite_padrao_vem_todos(self):
        """Uma rota de 20 paradas nao pode voltar cortada em 15."""
        ids = [self._cliente(f'C{i}', -5.79 - i / 1000, str(i)).pk for i in range(20)]

        resp = self.client.get(
            reverse('mapas:api-distancia-destinos'),
            {'tipo': 'cliente', 'ids': ','.join(map(str, ids))},
        )
        self.assertEqual(len(resp.json()['resultados']), 20)
