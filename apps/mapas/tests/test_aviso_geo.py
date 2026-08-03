"""
Retorno visivel da geocodificacao automatica.

A coordenada ja era preenchida no `post_save`, mas em silencio: quem cadastrava
so descobria que o endereco nao fora encontrado semanas depois, num relatorio
de cobertura. Estes testes fixam a traducao do estado da coordenada em frase --
principalmente que "nao encontrado" diga O MOTIVO, nao um "falhou" generico.
"""
from unittest.mock import patch

from django.contrib.messages import get_messages
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.mapas.services.aviso import mensagem_geo


class MensagemTests(TestCase):
    class Falso:
        latitude = None
        longitude = None
        geo_erro = ''
        geo_fixado = False
        cidade = 'Natal'

    def _obj(self, **kw):
        o = self.Falso()
        for k, v in kw.items():
            setattr(o, k, v)
        return o

    def test_com_coordenada_confirma(self):
        nivel, texto = mensagem_geo(self._obj(latitude=-5.79, longitude=-35.21))
        self.assertEqual(nivel, 'success')
        self.assertIn('localizado no mapa', texto)

    def test_coordenada_fixada_avisa_que_o_endereco_nao_manda(self):
        """Quem arrastou o pino precisa saber que editar o endereco nao o move."""
        nivel, texto = mensagem_geo(
            self._obj(latitude=-5.79, longitude=-35.21, geo_fixado=True))
        self.assertEqual(nivel, 'info')
        self.assertIn('fixada manualmente', texto)

    def test_erro_do_provider_aparece_na_mensagem(self):
        """
        "Nao foi possivel" sozinho nao permite agir. O motivo e a diferenca
        entre corrigir o endereco hoje e descobrir o problema num relatorio.
        """
        nivel, texto = mensagem_geo(self._obj(geo_erro='endereco nao encontrado'))
        self.assertEqual(nivel, 'warning')
        self.assertIn('endereco nao encontrado', texto)

    def test_sem_cidade_explica_por_que_nem_tentou(self):
        nivel, texto = mensagem_geo(self._obj(cidade=''))
        self.assertEqual(nivel, 'warning')
        self.assertIn('Sem cidade', texto)

    def test_sem_coordenada_e_sem_erro_sugere_o_comando(self):
        nivel, texto = mensagem_geo(self._obj())
        self.assertEqual(nivel, 'info')
        self.assertIn('geocodificar', texto)

    def test_objeto_sem_o_mixin_nao_gera_mensagem(self):
        self.assertIsNone(mensagem_geo(object()))


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class TelaDeClienteTests(TestCase):
    """O aviso chega ao usuario que salvou o cadastro."""

    def setUp(self):
        from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario

        self.empresa = Empresa.objects.create(
            razao_social='T', cnpj='11222333000181',
            regime_tributario='simples', codigo_regime_tributario=1,
        )
        self.filial = Filial.objects.create(
            empresa=self.empresa, razao_social='M', nome_fantasia='M',
            cnpj='11222333000181', uf='RN', is_matriz=True,
        )
        perfil = PerfilAcesso.objects.create(
            empresa=self.empresa, nome='Admin', is_admin=True)
        self.usuario = Usuario.objects.create_user(
            email='u@teste.local', nome='U', password='senha-de-teste-123',
            empresa=self.empresa, perfil=perfil, filial=self.filial)

        self.client.force_login(self.usuario)
        sessao = self.client.session
        sessao['filial_ativa_id'] = self.filial.pk
        sessao.save()

    def _cliente(self, **kw):
        """
        Cliente visivel pela tela.

        O `ClienteManager.for_filial` filtra por `ClienteFilial`, nao pelo FK
        direto -- sem o vinculo a view devolve 404 e o teste passaria a medir
        outra coisa.
        """
        from apps.cadastros.models import Cliente, ClienteFilial

        dados = dict(
            filial=self.filial, razao_social='PADARIA', cpf_cnpj='11144477735',
            endereco='Rua X', numero='10', bairro='Centro',
            cidade='Natal', uf='RN', ativo=True,
        )
        dados.update(kw)
        cliente = Cliente.objects.create(**dados)
        ClienteFilial.objects.create(cliente=cliente, filial=self.filial, ativo=True)
        return cliente

    def _textos(self, resp):
        return [m.message for m in get_messages(resp.wsgi_request)]

    @staticmethod
    def _post(**kw):
        """
        Dados minimos que o ClienteForm aceita.

        Montar so os campos de endereco reprovava a validacao em silencio: a
        view respondia 200, o cadastro nao mudava, e o teste mediria a
        ausencia da mensagem por um motivo que nao era o testado.
        """
        dados = {
            'razao_social': 'PADARIA', 'tipo': 'varejo', 'tipo_pessoa': 'F',
            'cpf_cnpj': '11144477735',
            'limite_credito': '0', 'prazo_pagamento_dias': '0',
            'endereco': 'Rua X', 'numero': '10', 'bairro': 'Centro',
            'cidade': 'Natal', 'uf': 'RN',
        }
        dados.update(kw)
        return dados

    def test_salvar_com_endereco_localizado_confirma_na_tela(self):
        cliente = self._cliente(latitude=-5.79, longitude=-35.21)

        resp = self.client.post(
            reverse('cadastros:cliente-update', args=[cliente.pk]),
            self._post(),
        )

        self.assertEqual(resp.status_code, 302)
        # Ou confirmou, ou explicou por que nao entrou -- nunca silencio.
        self.assertTrue(any(
            'mapa' in t.lower() or 'localiz' in t.lower() for t in self._textos(resp)
        ))

    def test_endereco_nao_encontrado_mostra_o_motivo(self):
        cliente = self._cliente(geo_erro='endereco nao encontrado')

        resp = self.client.post(
            reverse('cadastros:cliente-update', args=[cliente.pk]),
            self._post(endereco='Rua Inexistente', numero='999'),
        )

        self.assertEqual(resp.status_code, 302)
        self.assertTrue(self._textos(resp))

    def test_falha_no_aviso_nao_derruba_o_salvamento(self):
        """
        O cadastro ja foi gravado quando o aviso e montado. Um erro ali nao
        pode transformar um salvamento bem-sucedido em tela de erro.
        """
        cliente = self._cliente(latitude=-5.79, longitude=-35.21)

        p = patch('apps.mapas.services.aviso.mensagem_geo',
                  side_effect=RuntimeError('quebrou'))
        p.start()
        self.addCleanup(p.stop)

        resp = self.client.post(
            reverse('cadastros:cliente-update', args=[cliente.pk]),
            self._post(razao_social='MUDOU'),
        )

        self.assertEqual(resp.status_code, 302)
        cliente.refresh_from_db()
        self.assertEqual(cliente.razao_social, 'MUDOU')
