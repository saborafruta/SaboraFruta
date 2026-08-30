import json
from unittest.mock import patch

from django.template.loader import render_to_string
from django.test import Client, TestCase
from django.urls import reverse

from apps.cadastros.models import Cliente, ClienteFilial
from apps.core.views.menu_favoritos import _MenuLinks
from apps.pdv.tests import test_caixa_api as fixtures


class ClienteEditorPDVTests(TestCase):
    setUpTestData = classmethod(fixtures.CaixaPDVApiTests.setUpTestData.__func__)

    def setUp(self):
        fixtures.CaixaPDVApiTests.setUp(self)
        self.cliente = Cliente.objects.create(
            filial=self.filial, tipo_pessoa='F', razao_social='Cliente Original',
            cpf_cnpj='12345678901', celular='84999990000', limite_credito=300,
        )
        self.url = reverse('pdv:api_cliente_editar', args=[self.cliente.pk])

    def dados(self):
        return self.client.get(self.url).json()['dados']

    def salvar(self, dados):
        return self.client.post(self.url, json.dumps(dados), content_type='application/json')

    def test_edita_e_preserva_condicoes_comerciais(self):
        dados = self.dados()
        dados.update(razao_social='Cliente Editado', email='cliente@example.test',
                     cpf_cnpj='123.456.789-01', cep='59600-000', cidade='Mossoró', uf='RN',
                     limite_credito=0, saldo_devedor=9999, ativo=False,
                     filial_id=self.outra_filial.pk)
        response = self.salvar(dados)
        self.assertEqual(response.status_code, 200, response.content)
        self.cliente.refresh_from_db()
        self.assertEqual(self.cliente.razao_social, 'Cliente Editado')
        self.assertEqual(self.cliente.cep, '59600000')
        self.assertEqual(self.cliente.cpf_cnpj, '12345678901')
        self.assertEqual(self.cliente.limite_credito, 300)
        self.assertEqual(self.cliente.saldo_devedor, 0)
        self.assertTrue(self.cliente.ativo)
        self.assertEqual(self.cliente.filial_id, self.filial.pk)

    def test_nao_acessa_outra_filial_sem_vinculo(self):
        outro = Cliente.objects.create(filial=self.outra_filial, razao_social='Restrito')
        url = reverse('pdv:api_cliente_editar', args=[outro.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, '{}', content_type='application/json').status_code, 404)
        ClienteFilial.objects.create(cliente=outro, filial=self.filial, ativo=True)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_exige_permissao_e_autenticacao(self):
        with patch('apps.core.models.Usuario.tem_permissao', side_effect=lambda modulo, acao: modulo == 'pdv'):
            self.assertEqual(self.client.get(self.url).status_code, 403)
            self.assertEqual(self.salvar({}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_valida_campos_sem_salvar_parcialmente(self):
        dados = self.dados()
        for campo, valor in [('razao_social', ''), ('email', 'invalido'), ('cpf_cnpj', '123'),
                             ('cep', '111'), ('uf', 'XX'), ('tipo_pessoa', 'X')]:
            with self.subTest(campo=campo):
                response = self.salvar({**dados, campo: valor})
                self.assertEqual(response.status_code, 400)
                self.assertIn(campo, response.json()['campos'])
        self.cliente.refresh_from_db()
        self.assertEqual(self.cliente.razao_social, 'Cliente Original')
        self.assertEqual(self.cliente.cpf_cnpj, '12345678901')

    def test_rejeita_duplicidade_e_payload_invalido(self):
        Cliente.objects.create(filial=self.filial, razao_social='Outro', cpf_cnpj='98765432100')
        self.assertEqual(self.salvar({**self.dados(), 'cpf_cnpj': '98765432100'}).status_code, 400)
        self.assertEqual(self.salvar([]).status_code, 400)
        self.assertEqual(self.client.post(self.url, '{', content_type='application/json').status_code, 400)
        self.assertEqual(self.client.delete(self.url).status_code, 405)

    def test_post_exige_csrf(self):
        protegido = Client(enforce_csrf_checks=True)
        protegido.force_login(self.usuario)
        session = protegido.session
        session['filial_ativa_id'] = self.filial.pk
        session.save()
        self.assertEqual(protegido.post(self.url, '{}', content_type='application/json').status_code, 403)

    def test_menu_pdv_igual_ao_principal(self):
        response = self.client.get(reverse('pdv:home'))
        normal = render_to_string('core/_sidebar_navigation.html', request=response.wsgi_request)
        pdv_html = response.content.decode()
        drawer = pdv_html.split('<nav class="system-nav-drawer', 1)[1].split('</nav>', 1)[0]
        parser_normal, parser_pdv = _MenuLinks(), _MenuLinks()
        parser_normal.feed(normal)
        parser_pdv.feed(drawer)
        self.assertEqual(parser_pdv.links, parser_normal.links)
        self.assertIn(reverse('cadastros:funcionario-list'), parser_pdv.links)
        self.assertNotIn('>↵ OK</button>', pdv_html)
        self.assertNotIn('            Parcial', pdv_html)
