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
from types import SimpleNamespace
from unittest.mock import MagicMock

from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.core.context_processors import filial_context
from apps.core.models import (
    Empresa, Filial, FilialFavorita, PerfilAcesso, Usuario,
    UsuarioFilialAcesso,
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

    def test_vinculos_explicitos_liberam_filiais_de_outra_empresa(self):
        outra = Empresa.objects.create(
            razao_social='Outra LTDA', nome_fantasia='Outra',
            cnpj='94345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        outra_filial = Filial.objects.create(
            empresa=outra, razao_social='Outra Filial',
            cnpj='94345678000272', uf='RN',
        )
        outro_perfil = PerfilAcesso.objects.create(
            empresa=outra, nome='Gestor', is_admin=True,
        )
        lucas = self._usuario(
            'lucas-multi@grupo.local', self.perfil_operador, self.filial,
            acessos=[self.filial],
        )
        UsuarioFilialAcesso.objects.create(
            usuario=lucas,
            filial=outra_filial,
            perfil=outro_perfil,
            ativo=True,
        )

        self.assertEqual(
            set(lucas.filiais_permitidas()),
            {self.filial, outra_filial},
        )
        self.assertTrue(lucas.pode_acessar_filial(outra_filial))

    def test_contexto_oferece_troca_para_usuario_com_mais_de_uma_empresa(self):
        outra = Empresa.objects.create(
            razao_social='Outra LTDA', nome_fantasia='Outra',
            cnpj='94345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        outra_filial = Filial.objects.create(
            empresa=outra, razao_social='Outra Filial',
            cnpj='94345678000272', uf='RN',
        )
        outro_perfil = PerfilAcesso.objects.create(
            empresa=outra, nome='Gestor', is_admin=True,
        )
        usuario_central = self._usuario(
            'suporte-central@grupo.local', self.perfil_admin, self.matriz,
            acessos=[self.matriz],
        )
        UsuarioFilialAcesso.objects.create(
            usuario=usuario_central, filial=outra_filial, perfil=outro_perfil,
            ativo=True,
        )
        usuario_contextual = self._usuario(
            'suporte-tenant@grupo.local', self.perfil_admin, self.matriz,
            acessos=[self.matriz, self.filial],
        )
        request = RequestFactory().get('/dashboard/')
        request.user = usuario_contextual
        request._central_authenticated_user = usuario_central
        request.filial_ativa = self.matriz

        contexto = filial_context(request)

        self.assertTrue(contexto['pode_trocar_empresa'])

    def test_contexto_nao_oferece_troca_com_acessos_em_uma_empresa(self):
        usuario = self._usuario(
            'suporte-local@grupo.local', self.perfil_admin, self.matriz,
            acessos=[self.matriz, self.filial],
        )
        request = RequestFactory().get('/dashboard/')
        request.user = usuario
        request.filial_ativa = self.matriz

        contexto = filial_context(request)

        self.assertFalse(contexto['pode_trocar_empresa'])

    def test_contexto_do_tenant_nao_expoe_filial_orfa_do_banco_local(self):
        """A lista local nunca pode sobrepor o diretorio gerencial."""
        outra_empresa = Empresa.objects.create(
            razao_social='Empresa Orfa LTDA', nome_fantasia='Empresa Orfa',
            cnpj='94345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        filial_orfa = Filial.objects.create(
            empresa=outra_empresa, razao_social='Filial Orfa',
            nome_fantasia='FILIAL ORFA', cnpj='94345678000272', uf='RN',
        )
        from apps.core.models import EmpresaBanco

        banco = EmpresaBanco.objects.create(
            empresa=self.empresa,
            slug='grupo-tres',
            db_alias='empresa_grupo_tres',
            status=EmpresaBanco.Status.ATIVO,
        )

        class FiliaisLocais:
            """Queryset minimo: o tenant pode reutilizar PKs do gerencial."""

            def __init__(self, filiais):
                self.filiais = filiais

            def filter(self, **kwargs):
                cnpjs = kwargs.get('cnpj__in')
                if cnpjs is not None:
                    return FiliaisLocais([
                        filial for filial in self.filiais if filial.cnpj in cnpjs
                    ])
                return self

            def none(self):
                return FiliaisLocais([])

            def order_by(self, *_args):
                return self.filiais

        # Simula a lista poluida de um banco operacional. Os PKs locais 901
        # e 902 poderiam apontar para qualquer outra filial no gerencial;
        # por isso o menu deve usar o PK central obtido pelo CNPJ.
        filial_local_valida = SimpleNamespace(
            pk=901,
            cnpj=self.matriz.cnpj,
            nome_fantasia=self.matriz.nome_fantasia,
            razao_social=self.matriz.razao_social,
        )
        filial_local_orfa = SimpleNamespace(
            pk=902,
            cnpj=filial_orfa.cnpj,
            nome_fantasia=filial_orfa.nome_fantasia,
            razao_social=filial_orfa.razao_social,
        )
        usuario_local = MagicMock(
            is_authenticated=True,
            is_superuser=True,
            filiais_permitidas=MagicMock(
                return_value=FiliaisLocais([
                    filial_local_valida, filial_local_orfa,
                ]),
            ),
            _state=SimpleNamespace(db=banco.db_alias),
        )
        request = RequestFactory().get('/dashboard/')
        request.user = usuario_local
        request.filial_ativa = self.matriz
        request.selected_tenant_db_alias = banco.db_alias

        contexto = filial_context(request)

        self.assertEqual(contexto['filiais_disponiveis'], [filial_local_valida])
        self.assertEqual(filial_local_valida.id_troca, self.matriz.pk)
        self.assertNotEqual(filial_local_valida.id_troca, filial_local_valida.pk)


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

    def test_tela_expoe_favoritas_do_usuario_e_filtro(self):
        lucas = self._usuario(
            'lucas-favoritas@grupo.local', self.perfil_admin, self.polpa,
            acessos=[self.polpa, self.matriz],
        )
        FilialFavorita.objects.create(usuario=lucas, filial=self.matriz)
        self.client.force_login(lucas)

        resposta = self.client.get(reverse('core:selecionar-filial'))

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context['filiais_favoritas_ids'], [self.matriz.pk])
        self.assertContains(resposta, 'filiais-favoritas-data')
        self.assertContains(resposta, 'Somente favoritas')
        self.assertContains(resposta, 'Adicionar aos favoritos')
        self.assertContains(resposta, "modo: 'list'")
        self.assertContains(resposta, 'grid-cols-1 gap-3 lg:grid-cols-2')

    def test_superusuario_seleciona_empresa_antes_de_ver_as_filiais(self):
        outra_empresa = Empresa.objects.create(
            razao_social='Outra Empresa LTDA', nome_fantasia='Outra Empresa',
            cnpj='12345678000195',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        outra_filial = Filial.objects.create(
            empresa=outra_empresa, razao_social='Outra Matriz',
            nome_fantasia='OUTRA MATRIZ', cnpj='12345678000276', uf='RN',
            cidade='Mossoro', is_matriz=True,
        )
        admin = Usuario.objects.create_superuser(
            email='global@ited.local', nome='Admin Global', password='x' * 12,
            empresa=self.empresa, filial=self.matriz, perfil=self.perfil_admin,
        )
        self.client.force_login(admin)

        resposta = self.client.get(
            reverse('core:selecionar-filial'),
            {'empresa': self.empresa.pk},
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Selecionar contexto de acesso')
        self.assertContains(resposta, 'Filiais de Grupo Tres')
        self.assertEqual(resposta.context['empresas_total'], 2)
        self.assertEqual(
            {filial.pk for filial in resposta.context['filiais_page']},
            {self.matriz.pk, self.filial.pk, self.polpa.pk},
        )
        self.assertContains(resposta, 'selecionarEmpresa')
        self.assertContains(resposta, 'data-filiais-panel')
        self.assertContains(resposta, 'data-empresas-panel')
        self.assertContains(resposta, 'agendarBusca')
        self.assertContains(resposta, 'buscaEmpresa')
        self.assertContains(resposta, 'buscaFilial')
        self.assertNotContains(resposta, outra_filial.nome_fantasia)

    def test_superusuario_filtra_e_pagina_filiais_no_servidor(self):
        admin = Usuario.objects.create_superuser(
            email='escala@ited.local', nome='Admin Escala', password='x' * 12,
            empresa=self.empresa, filial=self.matriz, perfil=self.perfil_admin,
        )
        for indice in range(5):
            Filial.objects.create(
                empresa=self.empresa,
                razao_social=f'Unidade {indice}',
                nome_fantasia=f'UNIDADE {indice}',
                cnpj=f'82345678000{indice + 10:03d}',
                uf='RN',
                cidade='Natal' if indice < 4 else 'Caico',
            )
        self.client.force_login(admin)

        paginada = self.client.get(
            reverse('core:selecionar-filial'),
            {'empresa': self.empresa.pk},
        )
        filtrada = self.client.get(
            reverse('core:selecionar-filial'),
            {'empresa': self.empresa.pk, 'filial_busca': 'Caico'},
        )

        self.assertEqual(paginada.context['filiais_page'].paginator.count, 8)
        self.assertEqual(paginada.context['filiais_page'].paginator.num_pages, 2)
        self.assertEqual(len(filtrada.context['filiais_page']), 1)
        self.assertEqual(filtrada.context['filiais_page'][0].cidade, 'Caico')

    def test_empresa_sem_logo_usa_imagem_da_primeira_filial(self):
        self.matriz.imagem = 'filiais/imagens/logo-matriz.png'
        self.matriz.save(update_fields=['imagem'])
        self.filial.imagem = 'filiais/imagens/logo-filial.png'
        self.filial.save(update_fields=['imagem'])
        admin = Usuario.objects.create_superuser(
            email='logos@ited.local', nome='Admin Logos', password='x' * 12,
            empresa=self.empresa, filial=self.matriz, perfil=self.perfil_admin,
        )
        self.client.force_login(admin)

        resposta = self.client.get(reverse('core:selecionar-filial'))

        empresa = resposta.context['empresas_page'][0]
        self.assertTrue(empresa.logo_selecao_url.endswith('logo-matriz.png'))
        self.assertContains(resposta, 'logo-matriz.png')

    def test_logo_da_empresa_tem_prioridade_sobre_a_primeira_filial(self):
        self.empresa.logo_url = 'https://cdn.example.com/logo-empresa.png'
        self.empresa.save(update_fields=['logo_url'])
        self.matriz.imagem = 'filiais/imagens/logo-matriz.png'
        self.matriz.save(update_fields=['imagem'])
        admin = Usuario.objects.create_superuser(
            email='logo-empresa@ited.local', nome='Admin Logo', password='x' * 12,
            empresa=self.empresa, filial=self.matriz, perfil=self.perfil_admin,
        )
        self.client.force_login(admin)

        resposta = self.client.get(reverse('core:selecionar-filial'))

        empresa = resposta.context['empresas_page'][0]
        self.assertEqual(
            empresa.logo_selecao_url,
            'https://cdn.example.com/logo-empresa.png',
        )
        self.assertContains(resposta, 'https://cdn.example.com/logo-empresa.png')

    def test_usuario_pode_favoritar_e_desfavoritar_filial_permitida(self):
        lucas = self._usuario(
            'lucas-alterna-favorita@grupo.local', self.perfil_admin, self.polpa,
            acessos=[self.polpa, self.matriz],
        )
        self.client.force_login(lucas)
        url = reverse('core:alternar-filial-favorita', args=[self.matriz.pk])

        resposta = self.client.post(url)

        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(resposta.json()['favorita'])
        self.assertTrue(FilialFavorita.objects.filter(
            usuario=lucas,
            filial=self.matriz,
        ).exists())

        resposta = self.client.post(url)

        self.assertEqual(resposta.status_code, 200)
        self.assertFalse(resposta.json()['favorita'])
        self.assertFalse(FilialFavorita.objects.filter(
            usuario=lucas,
            filial=self.matriz,
        ).exists())

    def test_usuario_nao_pode_favoritar_filial_sem_acesso(self):
        lucas = self._usuario(
            'lucas-favorita-negada@grupo.local', self.perfil_operador, self.polpa,
            acessos=[self.polpa],
        )
        self.client.force_login(lucas)

        resposta = self.client.post(
            reverse('core:alternar-filial-favorita', args=[self.matriz.pk]),
        )

        self.assertEqual(resposta.status_code, 403)
        self.assertFalse(FilialFavorita.objects.filter(usuario=lucas).exists())

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
