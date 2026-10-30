from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial, PerfilAcesso, Permissao, Usuario
from apps.crm.models import CarteiraCliente
from apps.crm.models import OportunidadeCRM
from apps.crm.services import CarteiraService, ComercialService


class CarteiraServiceTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(
            razao_social='Empresa Carteiras', cnpj='22333444000191',
            regime_tributario='simples', codigo_regime_tributario=1,
        )
        self.filial = Filial.objects.create(
            empresa=self.empresa, razao_social='Matriz Carteiras',
            nome_fantasia='Matriz Carteiras', cnpj='22333444000191', uf='RN',
            is_matriz=True,
        )
        perfil_vendedor = PerfilAcesso.objects.create(
            empresa=self.empresa, nome='Vendedor CRM', ativo=True,
        )
        Permissao.objects.create(
            perfil=perfil_vendedor, modulo='crm', pode_ver=True,
            pode_criar=True, pode_editar=True,
        )
        perfil_gestor = PerfilAcesso.objects.create(
            empresa=self.empresa, nome='Gestor CRM', ativo=True,
        )
        Permissao.objects.create(
            perfil=perfil_gestor, modulo='crm', pode_ver=True,
            pode_criar=True, pode_editar=True, pode_aprovar=True,
        )
        self.vendedor_a = Usuario.objects.create_user(
            email='vendedor-a@example.com', nome='Vendedor A', password='teste1234',
            empresa=self.empresa, filial=self.filial, perfil=perfil_vendedor,
        )
        self.vendedor_b = Usuario.objects.create_user(
            email='vendedor-b@example.com', nome='Vendedor B', password='teste1234',
            empresa=self.empresa, filial=self.filial, perfil=perfil_vendedor,
        )
        self.gestor = Usuario.objects.create_user(
            email='gestor@example.com', nome='Gestor', password='teste1234',
            empresa=self.empresa, filial=self.filial, perfil=perfil_gestor,
        )
        self.cliente_a = Cliente.objects.create(
            filial=self.filial, razao_social='Cliente A', cpf_cnpj='11111111111',
            tipo_pessoa='F', ativo=True,
        )
        self.cliente_b = Cliente.objects.create(
            filial=self.filial, razao_social='Cliente B', cpf_cnpj='22222222222',
            tipo_pessoa='F', ativo=True,
        )
        CarteiraService.atribuir(
            cliente=self.cliente_a, vendedor=self.vendedor_a, atribuido_por=self.gestor,
        )
        CarteiraService.atribuir(
            cliente=self.cliente_b, vendedor=self.vendedor_b, atribuido_por=self.gestor,
        )

    def test_vendedor_enxerga_somente_clientes_da_propria_carteira(self):
        clientes = CarteiraService.clientes_disponiveis_na_filial(
            filial=self.filial, usuario=self.vendedor_a,
        )
        self.assertEqual(list(clientes), [self.cliente_a])

    def test_gestor_enxerga_todos_os_clientes_da_empresa(self):
        clientes = CarteiraService.clientes_disponiveis_na_filial(
            filial=self.filial, usuario=self.gestor,
        )
        self.assertCountEqual(clientes, [self.cliente_a, self.cliente_b])

    def test_reatribuicao_substitui_vendedor_sem_duplicar_cliente(self):
        carteira, criada = CarteiraService.atribuir(
            cliente=self.cliente_a, vendedor=self.vendedor_b, atribuido_por=self.gestor,
        )
        self.assertFalse(criada)
        self.assertEqual(carteira.vendedor, self.vendedor_b)
        self.assertEqual(
            CarteiraCliente.objects.filter(grupo_cliente=self.cliente_a.grupo_replicacao).count(),
            1,
        )

    def test_modelo_recusa_vendedor_de_outra_empresa(self):
        outra_empresa = Empresa.objects.create(
            razao_social='Outra Empresa', cnpj='33444555000102',
            regime_tributario='simples', codigo_regime_tributario=1,
        )
        outra_filial = Filial.objects.create(
            empresa=outra_empresa, razao_social='Outra Matriz', nome_fantasia='Outra Matriz',
            cnpj='33444555000102', uf='RN', is_matriz=True,
        )
        outro_perfil = PerfilAcesso.objects.create(
            empresa=outra_empresa, nome='Vendedor', ativo=True,
        )
        outro_vendedor = Usuario.objects.create_user(
            email='outro@example.com', nome='Outro', password='teste1234',
            empresa=outra_empresa, filial=outra_filial, perfil=outro_perfil,
        )
        carteira = CarteiraCliente(
            empresa=self.empresa, cliente=self.cliente_a, vendedor=outro_vendedor,
        )
        with self.assertRaises(ValidationError):
            carteira.save()

    def _autenticar(self, usuario):
        self.client.force_login(usuario)
        sessao = self.client.session
        sessao['filial_ativa_id'] = self.filial.pk
        sessao.save()

    def test_vendedor_nao_abre_tela_gerencial_de_carteiras(self):
        self._autenticar(self.vendedor_a)
        resposta = self.client.get(reverse('crm:carteiras'))
        self.assertRedirects(resposta, reverse('crm:funil'))

    def test_gestor_pode_reatribuir_cliente_pela_tela(self):
        self._autenticar(self.gestor)
        resposta = self.client.post(reverse('crm:carteiras'), {
            'cliente': self.cliente_a.pk,
            'vendedor': self.vendedor_b.pk,
        })
        self.assertRedirects(resposta, reverse('crm:carteiras'))
        carteira = CarteiraCliente.objects.get(grupo_cliente=self.cliente_a.grupo_replicacao)
        self.assertEqual(carteira.vendedor, self.vendedor_b)

    def test_funil_do_vendedor_nao_exibe_oportunidade_de_outra_carteira(self):
        pipeline = ComercialService.obter_pipeline_padrao(self.filial)
        etapa = pipeline.etapas.order_by('ordem').first()
        for cliente, vendedor, titulo in (
            (self.cliente_a, self.vendedor_a, 'Negócio exclusivo A'),
            (self.cliente_b, self.vendedor_b, 'Negócio exclusivo B'),
        ):
            ComercialService.criar_oportunidade(
                filial=self.filial,
                usuario=vendedor,
                dados={
                    'cliente': cliente,
                    'pipeline': pipeline,
                    'etapa': etapa,
                    'titulo': titulo,
                    'valor_estimado': 100,
                    'prioridade': OportunidadeCRM.Prioridade.NORMAL,
                    'origem': OportunidadeCRM.Origem.WHATSAPP,
                    'responsavel': vendedor,
                },
            )
        self._autenticar(self.vendedor_a)
        resposta = self.client.get(reverse('crm:funil'))
        self.assertContains(resposta, 'Negócio exclusivo A')
        self.assertNotContains(resposta, 'Negócio exclusivo B')
