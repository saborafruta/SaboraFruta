"""
Coordenadas automaticas ao salvar (secao 2 da especificacao).

"Caso o endereco seja alterado, atualizar automaticamente latitude e
longitude" -- antes o signal so invalidava a coordenada e o preenchimento
esperava o comando manual, entao na pratica nao era automatico.

O risco desse automatismo e o lote: uma importacao de CSV viraria centenas de
chamadas de rede dentro do request. Daí o `modo_lote()`.
"""
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.mapas.services.geocoder import Resultado
from apps.mapas.signals import em_modo_lote, modo_lote


@override_settings(MAPAS_GEOCODIFICAR_AO_SALVAR=True)
class BaseGeo(TestCase):
    def setUp(self):
        # Rede bloqueada: nenhum teste pode chamar o provider de verdade.
        rede = patch('apps.mapas.services.geocoder.requests.get',
                     side_effect=AssertionError('teste tentou acessar a rede'))
        rede.start()
        self.addCleanup(rede.stop)

        # Substitui o PROVIDER inteiro, e nao `GeocoderBase.geocodificar`:
        # os providers concretos sobrescrevem esse metodo, entao um patch na
        # classe base nao teria efeito nenhum.
        from apps.mapas.services.geocoder import GeocoderBase

        class _Stub(GeocoderBase):
            nome = 'stub'
            permite_uso_comercial = True

        self.stub = _Stub()
        self.mock_geo = patch.object(
            _Stub, 'geocodificar', return_value=Resultado(-5.79, -35.21, 'exata'),
        ).start()
        self.addCleanup(patch.stopall)

        construir = patch(
            'apps.mapas.services.geocoder.construir_geocoder',
            return_value=self.stub,
        )
        construir.start()
        self.addCleanup(construir.stop)

        # Throttle zerado: nao faz sentido dormir 1,1s por save em teste.
        sem_espera = patch('apps.mapas.services.geocoder._Throttle.aguardar')
        sem_espera.start()
        self.addCleanup(sem_espera.stop)

        from apps.core.models import Empresa, Filial

        self.empresa = Empresa.objects.create(
            razao_social='T', cnpj='11222333000181',
            regime_tributario='simples', codigo_regime_tributario=1,
        )
        self.filial = Filial.objects.create(
            empresa=self.empresa, razao_social='M', nome_fantasia='M',
            cnpj='11222333000181', uf='RN', is_matriz=True,
        )

    def _cliente(self, **kw):
        from apps.cadastros.models import Cliente

        dados = dict(
            filial=self.filial, razao_social='CLI', cpf_cnpj='12345678901',
            endereco='Rua A', numero='10', bairro='Centro',
            cidade='Natal', uf='RN', cep='59000000', ativo=True,
        )
        dados.update(kw)
        return Cliente.objects.create(**dados)


class GeocodificacaoAoSalvarTests(BaseGeo):
    def test_cliente_novo_ja_nasce_com_coordenada(self):
        cli = self._cliente()
        cli.refresh_from_db()

        self.assertEqual(cli.latitude, -5.79)
        self.assertEqual(cli.longitude, -35.21)
        self.assertEqual(cli.geo_precisao, 'exata')

    def test_alterar_endereco_atualiza_a_coordenada(self):
        """O requisito literal da especificacao."""
        cli = self._cliente()
        self.mock_geo.return_value = Resultado(-6.10, -35.50, 'aproximada')

        cli.endereco = 'Rua B'
        cli.numero = '999'
        cli.save()
        cli.refresh_from_db()

        self.assertEqual(cli.latitude, -6.10)
        self.assertEqual(cli.longitude, -35.50)

    def test_salvar_sem_mexer_no_endereco_nao_gasta_chamada(self):
        cli = self._cliente()
        chamadas = self.mock_geo.call_count

        cli.razao_social = 'OUTRO NOME'
        cli.save()

        self.assertEqual(self.mock_geo.call_count, chamadas)

    def test_coordenada_fixada_a_mao_nao_e_sobrescrita(self):
        cli = self._cliente()
        cli.geo_fixado = True
        cli.latitude, cli.longitude = -1.0, -2.0
        cli.save()

        cli.endereco = 'Rua Nova'
        cli.save()
        cli.refresh_from_db()

        self.assertEqual(cli.latitude, -1.0)
        self.assertEqual(cli.longitude, -2.0)

    def test_falha_do_provider_nao_impede_o_cadastro(self):
        """Provider fora do ar nao pode fazer o usuario perder o cadastro."""
        self.mock_geo.side_effect = RuntimeError('provider caiu')

        cli = self._cliente(razao_social='RESILIENTE')
        cli.refresh_from_db()

        self.assertEqual(cli.razao_social, 'RESILIENTE')
        self.assertIsNone(cli.latitude)

    def test_empresa_tambem_recebe_coordenada(self):
        """Secao 2: 'toda empresa cadastrada' passa a ter lat/lng."""
        from apps.core.models import Empresa

        emp = Empresa.objects.create(
            razao_social='NOVA', cnpj='99888777000166',
            regime_tributario='simples', codigo_regime_tributario=1,
            endereco='Av Central', numero='500', bairro='Centro',
            cidade='Natal', uf='RN', cep='59000000',
        )
        emp.refresh_from_db()
        self.assertEqual(emp.latitude, -5.79)

    def test_sem_endereco_nao_tenta_geocodificar(self):
        chamadas = self.mock_geo.call_count
        self._cliente(
            razao_social='SEM END', cpf_cnpj='99999999999',
            endereco='', numero='', bairro='', cidade='', uf='', cep='',
        )
        self.assertEqual(self.mock_geo.call_count, chamadas)


class ModoLoteTests(BaseGeo):
    def test_modo_lote_desliga_a_geocodificacao(self):
        self.mock_geo.reset_mock()  # ignora o que o setUp tenha feito
        with modo_lote():
            cli = self._cliente()
        cli.refresh_from_db()

        self.mock_geo.assert_not_called()
        self.assertIsNone(cli.latitude)

    def test_registro_do_lote_fica_pendente_para_o_backfill(self):
        with modo_lote():
            cli = self._cliente()
        self.assertTrue(cli.geo_desatualizado)

    def test_modo_lote_e_restaurado_apos_o_bloco(self):
        self.assertFalse(em_modo_lote())
        with modo_lote():
            self.assertTrue(em_modo_lote())
        self.assertFalse(em_modo_lote())

    def test_modo_lote_restaurado_mesmo_com_excecao(self):
        """Uma linha ruim do CSV nao pode deixar o modo ligado para sempre."""
        with self.assertRaises(ValueError):
            with modo_lote():
                raise ValueError('linha invalida')
        self.assertFalse(em_modo_lote())

    def test_aninhamento_preserva_o_estado(self):
        with modo_lote():
            with modo_lote():
                self.assertTrue(em_modo_lote())
            self.assertTrue(em_modo_lote())
        self.assertFalse(em_modo_lote())

class EnderecoFracoTests(BaseGeo):
    """
    Cadastro sem cidade nao deve ser geocodificado.

    So a UF produziria a busca "RN, Brasil", que o provider resolve para o
    centro do estado -- um pino no meio do nada, indistinguivel de uma
    coordenada boa.
    """

    def test_sem_cidade_nao_geocodifica(self):
        self.mock_geo.reset_mock()
        cli = self._cliente(
            razao_social='SO UF', cpf_cnpj='11111111111',
            endereco='', numero='', bairro='', cidade='', uf='RN', cep='',
        )
        cli.refresh_from_db()

        self.mock_geo.assert_not_called()
        self.assertIsNone(cli.latitude)

    def test_com_cidade_geocodifica(self):
        self.mock_geo.reset_mock()
        cli = self._cliente(
            razao_social='COM CIDADE', cpf_cnpj='22222222222',
            endereco='', numero='', bairro='', cidade='Natal', uf='RN', cep='',
        )
        cli.refresh_from_db()

        self.assertEqual(cli.latitude, -5.79)

    def test_endereco_montado_termina_em_brasil(self):
        cli = self._cliente()
        self.assertTrue(cli.endereco_para_geocodificar().endswith('Brasil'))
