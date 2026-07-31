"""
Distancia entre cadastros (secao 6).

Reaproveita o provider de rotas do paragrafo 4 -- se usasse outro caminho, a
distancia mostrada no cadastro poderia divergir da mostrada no mapa para o
mesmo par de pontos.
"""
from django.test import TestCase

from apps.mapas.services.distancia import TIPOS, DistanciaService, rotulo
from apps.mapas.services.otimizacao import distancia_haversine_m
from apps.mapas.services.roteirizacao import Rota, RoteirizadorBase


class _RoteirizadorFalso(RoteirizadorBase):
    nome = 'falso'
    permite_uso_comercial = True

    def __init__(self, fator=1.0, falhar=False):
        self.fator = fator
        self.falhar = falhar

    def rota(self, pontos):
        if self.falhar:
            raise RuntimeError('provider caiu')
        reta = distancia_haversine_m(pontos[0], pontos[-1])
        return Rota(
            distancia_m=reta * self.fator, duracao_s=reta / 10,
            geometria=[[p[0], p[1]] for p in pontos],
        )


class BaseDistancia(TestCase):
    def setUp(self):
        from apps.core.models import Empresa, Filial
        from apps.mapas.services.roteirizacao import RoteirizacaoService

        self.empresa = Empresa.objects.create(
            razao_social='T', cnpj='11222333000181',
            regime_tributario='simples', codigo_regime_tributario=1,
        )
        self.filial = Filial.objects.create(
            empresa=self.empresa, razao_social='Matriz SA', nome_fantasia='Matriz',
            cnpj='11222333000181', uf='RN', is_matriz=True,
            latitude=0.0, longitude=0.0,
        )
        self.servico = DistanciaService(
            roteirizacao=RoteirizacaoService(roteirizador=_RoteirizadorFalso())
        )

    def _cliente(self, nome, lat, cpf, lng=0.0):
        from apps.cadastros.models import Cliente

        return Cliente.objects.create(
            filial=self.filial, razao_social=nome, cpf_cnpj=cpf,
            cidade='Natal', uf='RN', latitude=lat, longitude=lng, ativo=True,
        )


class RotuloTests(TestCase):
    def test_prefere_o_primeiro_campo_preenchido(self):
        class Falso:
            pk = 1
            nome_fantasia = ''
            razao_social = 'RAZAO'

        self.assertEqual(rotulo(Falso(), ('nome_fantasia', 'razao_social')), 'RAZAO')

    def test_cai_para_o_id_quando_nao_ha_nome(self):
        class Falso:
            pk = 7
            nome = ''

        self.assertEqual(rotulo(Falso(), ('nome',)), '#7')


class ResolverTests(BaseDistancia):
    def test_resolve_cliente(self):
        cli = self._cliente('A', 0.1, '1')
        self.assertEqual(DistanciaService.resolver(self.filial, 'cliente', cli.pk), cli)

    def test_resolve_filial(self):
        achada = DistanciaService.resolver(self.filial, 'filial', self.filial.pk)
        self.assertEqual(achada, self.filial)

    def test_tipo_desconhecido_devolve_none(self):
        self.assertIsNone(DistanciaService.resolver(self.filial, 'planeta', 1))

    def test_cadastro_de_outra_empresa_nao_resolve(self):
        """Isolamento entre inquilinos: id alheio nao vira distancia."""
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
            cidade='Natal', uf='RN', latitude=1.0, longitude=1.0, ativo=True,
        )
        self.assertIsNone(
            DistanciaService.resolver(self.filial, 'cliente', alheio.pk)
        )


class CalcularTests(BaseDistancia):
    def test_distancia_e_tempo_entre_cliente_e_filial(self):
        cli = self._cliente('A', 0.1, '1')
        r = self.servico.calcular(
            filial=self.filial, origem_tipo='cliente', origem_id=cli.pk,
            destino_tipo='filial', destino_id=self.filial.pk,
        )

        self.assertNotIn('erro', r)
        self.assertAlmostEqual(r['distancia_km'], 11.12, delta=0.2)
        self.assertEqual(r['origem']['nome'], 'A')
        self.assertEqual(r['destino']['nome'], 'Matriz')
        self.assertTrue(r['geometria'])

    def test_traz_a_linha_reta_para_comparacao(self):
        cli = self._cliente('A', 0.1, '1')
        r = self.servico.calcular(
            filial=self.filial, origem_tipo='cliente', origem_id=cli.pk,
            destino_tipo='filial', destino_id=self.filial.pk,
        )
        self.assertIn('linha_reta_km', r)
        self.assertEqual(r['desvio'], 1.0)

    def test_desvio_sinaliza_rota_muito_maior_que_a_reta(self):
        """Rio/serra no caminho -- ou endereco geocodificado errado."""
        from apps.mapas.services.roteirizacao import RoteirizacaoService

        servico = DistanciaService(
            roteirizacao=RoteirizacaoService(roteirizador=_RoteirizadorFalso(fator=3.0))
        )
        cli = self._cliente('A', 0.1, '1')
        r = servico.calcular(
            filial=self.filial, origem_tipo='cliente', origem_id=cli.pk,
            destino_tipo='filial', destino_id=self.filial.pk,
        )
        self.assertEqual(r['desvio'], 3.0)

    def test_origem_sem_coordenada_e_erro_explicito(self):
        sem = self._cliente('SEM GEO', None, '2')
        r = self.servico.calcular(
            filial=self.filial, origem_tipo='cliente', origem_id=sem.pk,
            destino_tipo='filial', destino_id=self.filial.pk,
        )
        self.assertIn('SEM GEO', r['erro'])

    def test_destino_sem_coordenada_e_erro_explicito(self):
        cli = self._cliente('A', 0.1, '1')
        sem = self._cliente('SEM GEO', None, '2')
        r = self.servico.calcular(
            filial=self.filial, origem_tipo='cliente', origem_id=cli.pk,
            destino_tipo='cliente', destino_id=sem.pk,
        )
        self.assertIn('SEM GEO', r['erro'])

    def test_origem_igual_ao_destino_e_recusado(self):
        cli = self._cliente('A', 0.1, '1')
        r = self.servico.calcular(
            filial=self.filial, origem_tipo='cliente', origem_id=cli.pk,
            destino_tipo='cliente', destino_id=cli.pk,
        )
        self.assertIn('mesmo cadastro', r['erro'])

    def test_cadastro_inexistente_e_erro(self):
        r = self.servico.calcular(
            filial=self.filial, origem_tipo='cliente', origem_id=999999,
            destino_tipo='filial', destino_id=self.filial.pk,
        )
        self.assertIn('nao encontrado', r['erro'])

    def test_provider_fora_do_ar_vira_erro_tratado(self):
        from apps.mapas.services.roteirizacao import RoteirizacaoService

        servico = DistanciaService(
            roteirizacao=RoteirizacaoService(roteirizador=_RoteirizadorFalso(falhar=True))
        )
        cli = self._cliente('A', 0.1, '1')
        r = servico.calcular(
            filial=self.filial, origem_tipo='cliente', origem_id=cli.pk,
            destino_tipo='filial', destino_id=self.filial.pk,
        )
        self.assertIn('Falha no servico de rotas', r['erro'])


class BuscarDestinoTests(BaseDistancia):
    def test_lista_apenas_quem_tem_coordenada(self):
        """Oferecer destino sem coordenada garantiria erro no passo seguinte."""
        self._cliente('COM GEO', 0.1, '1')
        self._cliente('SEM GEO', None, '2')

        nomes = [d['nome'] for d in DistanciaService.buscar(self.filial, 'cliente', '')]
        self.assertIn('COM GEO', nomes)
        self.assertNotIn('SEM GEO', nomes)

    def test_filtra_pelo_termo(self):
        self._cliente('PADARIA', 0.1, '1')
        self._cliente('ACOUGUE', 0.2, '2')

        nomes = [d['nome'] for d in DistanciaService.buscar(self.filial, 'cliente', 'pada')]
        self.assertEqual(nomes, ['PADARIA'])

    def test_tipo_invalido_devolve_vazio(self):
        self.assertEqual(DistanciaService.buscar(self.filial, 'planeta', ''), [])

    def test_todos_os_tipos_da_especificacao_existem(self):
        for tipo in ('filial', 'fornecedor', 'cliente', 'motorista'):
            self.assertIn(tipo, TIPOS)
