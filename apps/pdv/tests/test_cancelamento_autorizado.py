import json
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario, UsuarioFilialAcesso
from apps.estoque.models import Estoque
from apps.pdv.models import VendaPDV
from apps.pdv.services.venda_pdv_service import VendaPDVService
from apps.pdv.tests import test_venda_pdv_service as venda_tests


class CancelamentoAutorizadoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        venda_tests.VendaPDVServiceTests.setUpTestData.__func__(cls)

    criar_produto = venda_tests.VendaPDVServiceTests.criar_produto
    abastecer = venda_tests.VendaPDVServiceTests.abastecer

    def setUp(self):
        venda_tests.VendaPDVServiceTests.setUp(self)
        self.admin = Usuario.objects.create_user(
            email='autorizador@example.com', nome='Administradora', password='senha-teste',
            empresa=self.empresa, filial=self.filial, perfil=self.perfil,
        )
        self.client.force_login(self.usuario)
        session = self.client.session
        session['filial_ativa_id'] = self.filial.pk
        session.save()
        self.produto = self.criar_produto()
        self.abastecer(self.produto, '5')
        self.venda = VendaPDVService.finalizar_venda(
            sessao=self.sessao, filial=self.filial, usuario=self.usuario,
            itens=[{'produto_id':self.produto.pk, 'quantidade':1}],
            pagamentos=[{'forma_id':self.forma.pk, 'valor':'10'}],
        )
        self.url = reverse('pdv:api_cancelar_venda_historico', args=[self.venda.pk])
        self.payload = {'admin_email':self.admin.email, 'admin_senha':'senha-teste',
                        'justificativa':'Cliente desistiu da compra.'}

    def post(self, payload=None, url=None):
        return self.client.post(url or self.url, data=json.dumps(self.payload if payload is None else payload), content_type='application/json')

    def test_exige_senha_mesmo_para_operador_administrador(self):
        response = self.post({'justificativa':'Cliente desistiu da compra.'})
        self.assertEqual(response.status_code, 403)
        self.venda.refresh_from_db()
        self.assertEqual(self.venda.status, 'finalizada')
        self.assertEqual(Estoque.objects.get(produto=self.produto,filial=self.filial).quantidade_atual, 4)
        self.assertEqual(self.post({}, reverse('pdv:api_venda_cancelar',args=[self.venda.pk])).status_code,403)

    def test_cancela_estorna_e_mantem_autoria_no_historico(self):
        response = self.post()
        self.assertEqual(response.status_code, 200, response.content)
        self.venda.refresh_from_db()
        self.assertEqual(self.venda.status,'cancelada')
        self.assertEqual(self.venda.cancelado_por,self.usuario)
        self.assertEqual(self.venda.cancelamento_autorizado_por,self.admin)
        self.assertTrue(self.venda.requer_autorizacao_cancelamento)
        self.assertIsNotNone(self.venda.cancelado_em)
        self.assertEqual(Estoque.objects.get(produto=self.produto,filial=self.filial).quantidade_atual,5)
        self.sessao.refresh_from_db()
        self.assertEqual(self.sessao.total_vendas,0)
        historico=self.client.get(reverse('pdv:api_historico')).json()['vendas']
        registro=next(v for v in historico if v['id']==self.venda.pk)
        self.assertEqual(registro['status'],'cancelada')
        self.assertEqual(registro['cancelado_por'],self.usuario.nome)
        self.assertEqual(registro['autorizado_por'],self.admin.nome)
        self.assertEqual(registro['motivo_cancelamento'],self.payload['justificativa'])
        self.assertNotIn('senha-teste',json.dumps(registro))
        self.assertEqual(self.post().status_code,404)
        self.assertEqual(Estoque.objects.get(produto=self.produto,filial=self.filial).quantidade_atual,5)

    def test_senha_errada_bloqueia_apos_cinco_tentativas(self):
        for _ in range(5):
            self.assertEqual(self.post(dict(self.payload,admin_senha='errada')).status_code,403)
        self.admin.refresh_from_db()
        self.assertIsNotNone(self.admin.bloqueado_ate)
        self.assertEqual(self.post().status_code,403)

    def test_nao_admin_inativo_ou_sem_acesso_nao_autorizam(self):
        perfil=PerfilAcesso.objects.create(empresa=self.empresa,nome='Operador comum',is_admin=False)
        self.admin.perfil=perfil
        self.admin.save()
        self.assertEqual(self.post().status_code,403)
        self.admin.perfil=self.perfil
        self.admin.ativo=False
        self.admin.save()
        self.assertEqual(self.post().status_code,403)
        self.admin.ativo=True
        self.admin.save()
        outra=Filial.objects.create(empresa=self.empresa,razao_social='Outra',nome_fantasia='Outra',cnpj='52345678000999')
        UsuarioFilialAcesso.objects.create(usuario=self.admin,filial=outra,perfil=self.perfil)
        self.assertEqual(self.post().status_code,403)

    def test_admin_de_outra_empresa_nao_autoriza(self):
        empresa=Empresa.objects.create(razao_social='Outra empresa',nome_fantasia='Outra',cnpj='62345678000191',codigo_regime_tributario=1)
        self.admin.empresa=empresa
        self.admin.save()
        self.assertEqual(self.post().status_code,403)

    def test_venda_de_outra_filial_nao_pode_ser_cancelada(self):
        outra=Filial.objects.create(empresa=self.empresa,razao_social='Outra',nome_fantasia='Outra',cnpj='52345678000999')
        VendaPDV.objects.filter(pk=self.venda.pk).update(filial=outra)
        self.assertEqual(self.post().status_code,404)

    def test_csrf_e_justificativa_obrigatorios(self):
        self.assertEqual(self.post(dict(self.payload,justificativa='curto')).status_code,400)
        cliente=Client(enforce_csrf_checks=True)
        cliente.force_login(self.usuario)
        self.assertEqual(cliente.post(self.url,data=json.dumps(self.payload),content_type='application/json').status_code,403)

    def test_erro_fiscal_nao_cancela_nem_estorna(self):
        with patch('apps.pdv.views.pdv.cancelar_venda_e_documento',side_effect=RuntimeError('Falha fiscal')):
            self.assertEqual(self.post().status_code,502)
        self.venda.refresh_from_db()
        self.assertEqual(self.venda.status,'finalizada')
        self.assertEqual(Estoque.objects.get(produto=self.produto,filial=self.filial).quantidade_atual,4)

    def test_falha_no_estorno_reverte_cancelamento_estoque_e_caixa(self):
        with patch('apps.cashback.services.wallet_service.CashbackWalletService.estornar_venda',side_effect=RuntimeError('Falha no estorno')):
            self.assertEqual(self.post().status_code,400)
        self.venda.refresh_from_db()
        self.sessao.refresh_from_db()
        self.assertEqual(self.venda.status,'finalizada')
        self.assertEqual(self.sessao.total_vendas,10)
        self.assertEqual(Estoque.objects.get(produto=self.produto,filial=self.filial).quantidade_atual,4)
