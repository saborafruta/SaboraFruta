"""
APIs de território — o contrato que o modo de desenho (Leaflet.draw) consome.

Cobre o caminho todo: desenhar, editar, remover, permissão e isolamento entre
empresas.
"""
from django.test import TestCase, override_settings
from django.urls import reverse

QUADRADO = [[-5.80, -35.25], [-5.80, -35.15], [-5.90, -35.15], [-5.90, -35.25]]


# O hasher de produção (PBKDF2, centenas de milhares de iterações) fazia esta
# suíte levar ~19s só criando usuários. Aqui a senha é irrelevante — o que
# importa é a sessão autenticada.
@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class BaseTerritorioApi(TestCase):
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

    def _usuario(self, filial, *, editar=True):
        """
        Usuário vinculado à filial.

        `is_admin` no perfil faz `tem_permissao` devolver True para tudo — é o
        atalho certo aqui, porque o objeto do teste é a API, não a matriz de
        permissões. O caso sem permissão usa um perfil comum e sem nenhuma
        `Permissao` cadastrada, que nega por padrão.
        """
        from apps.core.models import PerfilAcesso, Usuario

        perfil = PerfilAcesso.objects.create(
            empresa=filial.empresa, nome=f'Perfil {filial.pk} {editar}',
            is_admin=editar,
        )
        return Usuario.objects.create_user(
            email=f'u{filial.pk}{int(editar)}@teste.local',
            nome=f'Usuario {filial.pk}',
            password='senha-de-teste-123',
            empresa=filial.empresa, perfil=perfil, filial=filial,
        )

    def _logar(self, usuario, filial):
        """
        Autentica e garante a filial ativa.

        O middleware resolve `request.filial_ativa` pela sessão
        (`filial_ativa_id`) e, na falta dela, pelo `filial` do usuário — o
        teste preenche os dois para não depender de qual caminho vale.
        """
        self.client.force_login(usuario)
        sessao = self.client.session
        sessao['filial_ativa_id'] = filial.pk
        sessao.save()

    def _praca(self, filial, nome='Zona Sul', poligono=None):
        from apps.cadastros.models import Praca

        praca = Praca.objects.create(filial=filial, nome=nome)
        if poligono:
            praca.definir_poligono(poligono)
            praca.save()
        return praca


class SalvarPoligonoTests(BaseTerritorioApi):
    def setUp(self):
        self.filial = self._empresa('AAA', '11222333000181')
        self._logar(self._usuario(self.filial), self.filial)

    def _url(self, praca):
        return reverse('mapas:api-territorio-poligono', args=[praca.pk])

    def test_salva_poligono_e_recalcula_clientes(self):
        from apps.cadastros.models import Cliente, Praca

        praca = self._praca(self.filial)
        Cliente.objects.create(
            filial=self.filial, razao_social='DENTRO', cpf_cnpj='1',
            latitude=-5.85, longitude=-35.20, ativo=True,
        )
        Cliente.objects.create(
            filial=self.filial, razao_social='FORA', cpf_cnpj='2',
            latitude=-5.85, longitude=-35.40, ativo=True,
        )

        resp = self.client.post(
            self._url(praca), {'poligono': QUADRADO},
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        dados = resp.json()
        self.assertTrue(dados['tem_poligono'])
        self.assertEqual(dados['clientes'], 1)

        praca = Praca.objects.get(pk=praca.pk)
        self.assertEqual(praca.bbox_sul, -5.90)

    def test_remover_poligono_limpa_bbox_e_atribuicao(self):
        from apps.cadastros.models import Praca
        from apps.mapas.models import ClienteTerritorio

        praca = self._praca(self.filial, poligono=QUADRADO)
        resp = self.client.post(
            self._url(praca), {'poligono': None}, content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()['tem_poligono'])

        praca = Praca.objects.get(pk=praca.pk)
        self.assertIsNone(praca.poligono)
        self.assertIsNone(praca.bbox_norte)
        self.assertFalse(ClienteTerritorio.objects.filter(praca=praca).exists())

    def test_poligono_com_dois_pontos_e_rejeitado(self):
        praca = self._praca(self.filial)
        resp = self.client.post(
            self._url(praca), {'poligono': [[-5.8, -35.2], [-5.9, -35.1]]},
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('3 pontos', resp.json()['erro'])

    def test_poligono_de_tipo_errado_e_rejeitado(self):
        praca = self._praca(self.filial)
        resp = self.client.post(
            self._url(praca), {'poligono': 'nao sou lista'},
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_json_invalido_e_rejeitado(self):
        praca = self._praca(self.filial)
        resp = self.client.post(
            self._url(praca), data='{quebrado', content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_get_nao_e_permitido(self):
        praca = self._praca(self.filial)
        self.assertEqual(self.client.get(self._url(praca)).status_code, 405)


class ListarTerritoriosTests(BaseTerritorioApi):
    def setUp(self):
        self.filial = self._empresa('AAA', '11222333000181')
        self._logar(self._usuario(self.filial), self.filial)
        self._praca(self.filial, 'Com Poligono', poligono=QUADRADO)
        self._praca(self.filial, 'Sem Poligono')

    def test_padrao_traz_so_quem_tem_poligono(self):
        resp = self.client.get(reverse('mapas:api-territorios'))
        nomes = [t['nome'] for t in resp.json()['territorios']]
        self.assertEqual(nomes, ['Com Poligono'])

    def test_todas_inclui_sem_poligono_para_o_seletor(self):
        """O modo de desenho precisa das praças ainda sem polígono."""
        resp = self.client.get(reverse('mapas:api-territorios'), {'todas': '1'})
        territorios = resp.json()['territorios']
        nomes = sorted(t['nome'] for t in territorios)

        self.assertEqual(nomes, ['Com Poligono', 'Sem Poligono'])
        sem = next(t for t in territorios if t['nome'] == 'Sem Poligono')
        self.assertIsNone(sem['poligono'])


class IsolamentoEPermissaoTests(BaseTerritorioApi):
    def test_nao_edita_praca_de_outra_empresa(self):
        """Isolamento entre inquilinos: 404, não 200."""
        filial_a = self._empresa('AAA', '11222333000181')
        filial_b = self._empresa('BBB', '99888777000166')
        praca_b = self._praca(filial_b, 'Alheia')

        self._logar(self._usuario(filial_a), filial_a)
        resp = self.client.post(
            reverse('mapas:api-territorio-poligono', args=[praca_b.pk]),
            {'poligono': QUADRADO}, content_type='application/json',
        )
        self.assertEqual(resp.status_code, 404)

    def test_nao_lista_territorio_de_outra_empresa(self):
        filial_a = self._empresa('AAA', '11222333000181')
        filial_b = self._empresa('BBB', '99888777000166')
        self._praca(filial_b, 'Alheia', poligono=QUADRADO)

        self._logar(self._usuario(filial_a), filial_a)
        resp = self.client.get(reverse('mapas:api-territorios'), {'todas': '1'})
        self.assertEqual(resp.json()['territorios'], [])

    def test_sem_permissao_de_editar_nao_salva(self):
        filial = self._empresa('CCC', '77666555000144')
        praca = self._praca(filial)
        self._logar(self._usuario(filial, editar=False), filial)

        resp = self.client.post(
            reverse('mapas:api-territorio-poligono', args=[praca.pk]),
            {'poligono': QUADRADO}, content_type='application/json',
        )
        # requer_permissao redireciona (302) ou nega (403); o que não pode é gravar.
        self.assertIn(resp.status_code, (302, 401, 403))
        praca.refresh_from_db()
        self.assertIsNone(praca.poligono)

    def test_anonimo_nao_acessa(self):
        filial = self._empresa('DDD', '55444333000122')
        praca = self._praca(filial, poligono=QUADRADO)
        resp = self.client.get(
            reverse('mapas:api-territorio-indicadores', args=[praca.pk])
        )
        self.assertIn(resp.status_code, (302, 401, 403))


class IndicadoresApiTests(BaseTerritorioApi):
    def setUp(self):
        self.filial = self._empresa('AAA', '11222333000181')
        self._logar(self._usuario(self.filial), self.filial)

    def test_indicadores_do_territorio(self):
        from decimal import Decimal

        from apps.cadastros.models import Cliente, Praca
        from apps.mapas.services import TerritorioService

        praca = self._praca(self.filial, poligono=QUADRADO)
        praca.meta_mensal = Decimal('500.00')
        praca.save()
        Cliente.objects.create(
            filial=self.filial, razao_social='DENTRO', cpf_cnpj='1',
            latitude=-5.85, longitude=-35.20, ativo=True,
        )
        TerritorioService.recalcular_praca(praca)

        resp = self.client.get(
            reverse('mapas:api-territorio-indicadores', args=[praca.pk])
        )
        dados = resp.json()
        self.assertEqual(dados['clientes'], 1)
        self.assertEqual(dados['meta'], 500.0)
        self.assertEqual(dados['dias'], 30)

    def test_dias_fora_da_faixa_e_limitado(self):
        praca = self._praca(self.filial, poligono=QUADRADO)
        url = reverse('mapas:api-territorio-indicadores', args=[praca.pk])

        self.assertEqual(self.client.get(url, {'dias': '9999'}).json()['dias'], 365)
        self.assertEqual(self.client.get(url, {'dias': '0'}).json()['dias'], 1)
        self.assertEqual(self.client.get(url, {'dias': 'abc'}).json()['dias'], 30)
