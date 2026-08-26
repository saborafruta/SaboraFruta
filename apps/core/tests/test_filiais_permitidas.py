"""
Quais unidades um login enxerga -- e abre.

O QUE ESTES TESTES CERCAM:

  · VINCULO EXPLICITO E' FRONTEIRA, INCLUSIVE PARA ADMIN. Perfil responde
    pelo QUE a pessoa faz; vinculo responde por ONDE. Enquanto o perfil
    vencia o vinculo, o login de uma filial via as tres na tela de escolha
    -- e clicar entrava, porque o middleware usava a mesma regra frouxa;

  · A LISTA E O ACESSO SAO A MESMA RESPOSTA. Tela de escolha, seletor do
    cabecalho e middleware perguntam ao mesmo lugar. Regra de acesso
    duplicada nao falha com erro: falha oferecendo uma unidade a mais;

  · NINGUEM E' TRANCADO PARA FORA. Admin cadastrado antes de o vinculo
    existir -- sem nenhuma linha de acesso -- continua enxergando a empresa
    inteira.
"""
from django.test import TestCase
from django.urls import reverse

from apps.core.models import (
    Empresa, Filial, PerfilAcesso, Usuario, UsuarioFilialAcesso,
)


class FiliaisBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Grupo Tres Unidades LTDA', nome_fantasia='Grupo Tres',
            cnpj='93345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.matriz = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', nome_fantasia='MATRIZ',
            cnpj='93345678000272', uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Filial', nome_fantasia='FILIAL',
            cnpj='93345678000353', uf='RN', cidade='Parnamirim',
        )
        cls.polpa = Filial.objects.create(
            empresa=cls.empresa, razao_social='Polpa Boa D+',
            nome_fantasia='Polpa Boa D+', cnpj='93345678000434', uf='RN',
        )
        cls.perfil_admin = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Administrador', is_admin=True,
        )
        cls.perfil_operador = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Operador', is_admin=False,
        )

    def _usuario(self, email, perfil, filial, acessos=()):
        usuario = Usuario.objects.create_user(
            email=email, nome='Lucas', password='x' * 12,
            empresa=self.empresa, perfil=perfil, filial=filial,
        )
        for uma in acessos:
            UsuarioFilialAcesso.objects.create(
                usuario=usuario, filial=uma, perfil=perfil, ativo=True,
            )
        return usuario


class FiliaisPermitidasTests(FiliaisBase):
    """A regra, direto no usuario."""

    def test_o_vinculo_de_uma_filial_vence_o_perfil_admin(self):
        """
        O caso relatado: acesso a uma unidade so', e as tres na tela.
        Perfil administrador diz o que a pessoa pode fazer, nao onde.
        """
        lucas = self._usuario(
            'lucas@grupo.local', self.perfil_admin, self.polpa,
            acessos=[self.polpa],
        )

        permitidas = list(lucas.filiais_permitidas())

        self.assertEqual(permitidas, [self.polpa])
        self.assertTrue(lucas.pode_acessar_filial(self.polpa))
        self.assertFalse(lucas.pode_acessar_filial(self.matriz))
        self.assertFalse(lucas.pode_acessar_filial(self.filial))

    def test_admin_sem_nenhum_vinculo_continua_vendo_a_empresa(self):
        """Quem foi cadastrado antes do vinculo nao pode ser trancado fora."""
        chefe = self._usuario('chefe@grupo.local', self.perfil_admin, self.matriz)

        self.assertEqual(chefe.filiais_permitidas().count(), 3)
        self.assertTrue(chefe.pode_acessar_filial(self.polpa))

    def test_operador_sem_vinculo_fica_na_filial_padrao(self):
        op = self._usuario('op@grupo.local', self.perfil_operador, self.filial)

        self.assertEqual(list(op.filiais_permitidas()), [self.filial])
        self.assertFalse(op.pode_acessar_filial(self.matriz))

    def test_vinculo_inativo_nao_da_acesso(self):
        lucas = self._usuario(
            'lucas2@grupo.local', self.perfil_operador, self.polpa,
            acessos=[self.polpa, self.matriz],
        )
        lucas.acessos_filiais.filter(filial=self.matriz).update(ativo=False)

        self.assertEqual(list(lucas.filiais_permitidas()), [self.polpa])
        self.assertFalse(lucas.pode_acessar_filial(self.matriz))

    def test_filial_inativa_sai_da_lista(self):
        lucas = self._usuario(
            'lucas3@grupo.local', self.perfil_operador, self.polpa,
            acessos=[self.polpa],
        )
        Filial.objects.filter(pk=self.polpa.pk).update(ativo=False)

        self.assertEqual(list(lucas.filiais_permitidas()), [])

    def test_usuario_de_outra_empresa_nao_entra(self):
        outra = Empresa.objects.create(
            razao_social='Outra LTDA', nome_fantasia='Outra',
            cnpj='94345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        de_fora = Filial.objects.create(
            empresa=outra, razao_social='De fora', cnpj='94345678000272', uf='RN',
        )
        lucas = self._usuario(
            'lucas4@grupo.local', self.perfil_admin, self.polpa,
            acessos=[self.polpa],
        )

        self.assertFalse(lucas.pode_acessar_filial(de_fora))


class TelaDeEscolhaTests(FiliaisBase):
    """A tela mostra a mesma lista -- e uma unidade so' nem chega a aparecer."""

    def test_com_uma_unidade_a_tela_nem_pergunta(self):
        lucas = self._usuario(
            'lucas5@grupo.local', self.perfil_admin, self.polpa,
            acessos=[self.polpa],
        )
        self.client.force_login(lucas)

        resposta = self.client.get(reverse('core:selecionar-filial'))

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(self.client.session['filial_ativa_id'], self.polpa.pk)

    def test_a_tela_lista_so_o_que_o_vinculo_da(self):
        lucas = self._usuario(
            'lucas6@grupo.local', self.perfil_admin, self.polpa,
            acessos=[self.polpa, self.matriz],
        )
        self.client.force_login(lucas)

        resposta = self.client.get(reverse('core:selecionar-filial'))

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(
            {f.pk for f in resposta.context['filiais']},
            {self.polpa.pk, self.matriz.pk},
        )

    def test_trocar_para_filial_sem_vinculo_e_recusado(self):
        """
        A tela filtra, o servidor decide: a URL de troca continua existindo
        para quem digita, e e' ela que precisa dizer nao.
        """
        lucas = self._usuario(
            'lucas7@grupo.local', self.perfil_admin, self.polpa,
            acessos=[self.polpa],
        )
        self.client.force_login(lucas)

        self.client.get(reverse('core:trocar-filial', args=[self.matriz.pk]))

        self.assertNotEqual(
            self.client.session.get('filial_ativa_id'), self.matriz.pk,
        )
