import shutil
import subprocess
from decimal import Decimal
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.models import (
    Empresa,
    EmpresaBanco,
    Filial,
    ParametrosSistema,
    PerfilAcesso,
    Usuario,
)
from apps.core.services.checkout import checkout_venda_ativo
from apps.produtos.models import (
    Produto,
    ProdutoCodigoBarras,
    ProdutoFilial,
    UnidadeMedida,
    UnidadeMedidaFilial,
)


class CheckoutVendaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Empresa Checkout LTDA',
            nome_fantasia='Empresa Checkout',
            cnpj='82345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa,
            razao_social='Filial Checkout',
            nome_fantasia='Matriz',
            cnpj='82345678000192',
            uf='RN',
        )
        cls.outra_filial = Filial.objects.create(
            empresa=cls.empresa,
            razao_social='Outra Filial Checkout',
            nome_fantasia='Filial Dois',
            cnpj='82345678000193',
            uf='RN',
        )
        cls.perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa,
            nome='Operador Checkout',
            is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='checkout@inoovated.com',
            nome='Operador Checkout',
            password='teste1234',
            empresa=cls.empresa,
            filial=cls.filial,
            perfil=cls.perfil,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa,
            sigla='UN',
            descricao='Unidade',
            tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        cls.produto = Produto.objects.create(
            filial=cls.filial,
            unidade_medida=cls.unidade,
            descricao='Café Especial Checkout',
            codigo='CAF-0007',
            codigo_barras='7891234567897',
            codigos_barras_extras=['2000000000007'],
            ncm='09012100',
            preco_venda=Decimal('18.90'),
            permite_venda_sem_estoque=True,
        )
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.filial)
        ProdutoCodigoBarras.objects.create(
            produto=cls.produto,
            ean='7899999999997',
            tipo=ProdutoCodigoBarras.Tipo.ALTERNATIVO,
        )
        cls.produto_externo = Produto.objects.create(
            filial=cls.outra_filial,
            unidade_medida=cls.unidade,
            descricao='Produto de outra filial',
            codigo='SOMENTE-OUTRA-FILIAL',
            codigo_barras='7890000000007',
            ncm='09012100',
            preco_venda=Decimal('9.90'),
            permite_venda_sem_estoque=True,
        )
        ProdutoFilial.objects.create(
            produto=cls.produto_externo,
            filial=cls.outra_filial,
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        session = self.client.session
        session['filial_ativa_id'] = self.filial.pk
        session.save()
        self.parametros, _ = ParametrosSistema.objects.get_or_create(filial=self.filial)

    def habilitar_checkout(self):
        self.parametros.checkout_venda_ativo = True
        self.parametros.save(update_fields=['checkout_venda_ativo'])

    def buscar(self, termo, **params):
        dados = {'q': termo, **params}
        return self.client.get(reverse('pdv:api_checkout_produtos'), dados)

    def test_checkout_fica_oculto_e_indisponivel_por_padrao(self):
        resposta = self.client.get(reverse('pdv:checkout'))

        self.assertRedirects(
            resposta,
            reverse('core:dashboard'),
            fetch_redirect_response=False,
        )
        menu = self.client.get(reverse('pdv:home'))
        self.assertNotContains(menu, 'title="Checkout de venda"')

    @override_settings(TENANT_DATABASE_ROUTING_ENABLED=True)
    def test_multibanco_le_flag_salva_na_filial_do_banco_gerencial(self):
        self.habilitar_checkout()
        banco = EmpresaBanco.objects.create(
            empresa=self.empresa,
            slug='empresa-checkout-82345678000191',
            db_alias='empresa_checkout_82345678000191',
            database_url_env_var='TENANT_DATABASE_URL_EMPRESA_CHECKOUT_82345678000191',
            ativo=True,
            status=EmpresaBanco.Status.ATIVO,
        )
        request = SimpleNamespace(
            tenant_db_alias=banco.db_alias,
            # Representa a cópia da mesma filial carregada do banco operacional.
            filial_ativa=SimpleNamespace(cnpj=self.filial.cnpj),
        )

        self.assertTrue(checkout_venda_ativo(request))

    def test_flag_da_filial_exibe_menu_e_libera_tela(self):
        self.habilitar_checkout()

        resposta = self.client.get(reverse('pdv:checkout'))
        menu = self.client.get(reverse('pdv:home'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Leitura rápida, pagamento e finalização')
        self.assertContains(resposta, 'Cadastro rápido de cliente')
        self.assertContains(menu, 'title="Checkout de venda"')

    def test_tela_expoe_operacao_por_teclado_e_quantidade_antes_da_leitura(self):
        self.habilitar_checkout()

        resposta = self.client.get(reverse('pdv:checkout'))

        self.assertContains(resposta, 'x-ref="quantidadeProduto"')
        self.assertContains(resposta, 'x-ref="formaPagamento"')
        self.assertContains(resposta, 'x-ref="valorPagamento"')
        self.assertContains(resposta, '<kbd>F10</kbd>Finalizar', html=True)
        self.assertContains(resposta, 'quantidadeParaProduto(produto)')
        self.assertNotContains(resposta, 'class="co-payment-grid"')
        self.assertContains(resposta, 'Venda finalizada!')
        self.assertContains(resposta, 'Imprimir comprovante')
        self.assertContains(resposta, "emitirFiscal('nfce')")
        self.assertContains(resposta, '/pdv/venda/0/comprovante/')
        self.assertContains(resposta, 'class="co-table" data-columns="off"')

    @skipUnless(shutil.which('node'), 'Node.js necessário para validar o JavaScript do checkout')
    def test_javascript_renderizado_tem_sintaxe_valida(self):
        self.habilitar_checkout()
        resposta = self.client.get(reverse('pdv:checkout'))
        html = resposta.content.decode('utf-8')
        script = 'function checkoutVenda()' + html.split(
            'function checkoutVenda()', 1,
        )[1].split('</script>', 1)[0]

        resultado = subprocess.run(
            [shutil.which('node'), '--check'],
            input=script,
            text=True,
            encoding='utf-8',
            capture_output=True,
            timeout=20,
        )

        self.assertEqual(resultado.returncode, 0, resultado.stdout + resultado.stderr)

    @skipUnless(shutil.which('node'), 'Node.js necessário para validar o JavaScript do checkout')
    def test_quantidade_previa_e_valor_recebido_funcionam_no_cliente(self):
        self.habilitar_checkout()
        resposta = self.client.get(reverse('pdv:checkout'))
        html = resposta.content.decode('utf-8')
        script = 'function checkoutVenda()' + html.split(
            'function checkoutVenda()', 1,
        )[1].split('</script>', 1)[0]
        script += r'''
const assert = require('node:assert/strict');
global.document = {getElementById: () => ({textContent: '[]'})};
const checkout = checkoutVenda();
checkout.$refs = {};
checkout.$nextTick = (callback) => callback();
checkout.quantidadeEntrada = '3';
checkout.adicionarProduto({
  id: 7, descricao: 'Produto', preco: 10, pode_vender: true,
  quantidade_step: 1, quantidade_decimais: 0
});
assert.equal(checkout.itens[0].quantidade, 3);
assert.equal(checkout.quantidadeEntrada, '1');
assert.equal(checkout.valorPagamentoEntrada, '30,00');
checkout.quantidadeEntrada = '2';
checkout.adicionarProduto({
  id: 7, descricao: 'Produto', preco: 10, pode_vender: true,
  quantidade_step: 1, quantidade_decimais: 0
});
assert.equal(checkout.itens[0].quantidade, 5);
checkout.sessao = {id: 1};
checkout.formasPagamento = [{id: 3, descricao: 'Dinheiro', tipo: 'dinheiro'}];
checkout.selecionarForma(checkout.formasPagamento[0]);
assert.equal(checkout.valorPagamentoEntrada, '50,00');
checkout.valorPagamentoEntrada = '60,00';
checkout.valorPagamentoManual = true;
assert.equal(checkout.troco, 10);
assert.equal(checkout.faltaPagamento, 0);
assert.equal(checkout.podeFinalizar, true);
checkout.valorPagamentoEntrada = '40,00';
assert.equal(checkout.faltaPagamento, 10);
assert.equal(checkout.podeFinalizar, false);
'''

        resultado = subprocess.run(
            [shutil.which('node')],
            input=script,
            text=True,
            encoding='utf-8',
            capture_output=True,
            timeout=20,
        )

        self.assertEqual(resultado.returncode, 0, resultado.stdout + resultado.stderr)

    def test_api_tambem_exige_a_flag_da_filial(self):
        resposta = self.buscar(self.produto.codigo_barras)

        self.assertEqual(resposta.status_code, 404)
        self.assertEqual(resposta.json()['erro'], 'Checkout não habilitado para esta filial.')

    def test_busca_restrita_encontra_somente_identificadores_exatos(self):
        self.habilitar_checkout()
        identificadores = [
            str(self.produto.pk),
            self.produto.codigo,
            self.produto.codigo.lower(),
            self.produto.codigo_barras,
            '7899999999997',
            '2000000000007',
        ]

        for identificador in identificadores:
            with self.subTest(identificador=identificador):
                resposta = self.buscar(identificador)
                self.assertEqual(resposta.status_code, 200, resposta.content)
                self.assertEqual(
                    [item['id'] for item in resposta.json()['produtos']],
                    [self.produto.pk],
                )

        self.assertEqual(self.buscar('Café Especial').json()['produtos'], [])
        self.assertEqual(self.buscar('CAF').json()['produtos'], [])
        self.assertEqual(self.buscar('SOMENTE-OUTRA-FILIAL').json()['produtos'], [])

    def test_busca_por_nome_exige_aprovacao_no_servidor(self):
        self.habilitar_checkout()
        permissao_somente_visualizar = lambda _usuario, _modulo, acao='ver': acao == 'ver'

        with patch.object(
            Usuario,
            'tem_permissao',
            autospec=True,
            side_effect=permissao_somente_visualizar,
        ):
            resposta = self.buscar('Especial Checkout', por_nome='1')
            tela = self.client.get(reverse('pdv:checkout'))

        self.assertEqual(resposta.status_code, 403)
        self.assertNotContains(tela, 'Permitir nome')

    def test_usuario_com_aprovacao_pode_buscar_por_nome(self):
        self.habilitar_checkout()

        resposta = self.buscar('Especial Checkout', por_nome='1')

        self.assertEqual(resposta.status_code, 200, resposta.content)
        self.assertEqual(
            [item['id'] for item in resposta.json()['produtos']],
            [self.produto.pk],
        )
        self.assertTrue(resposta.json()['busca_por_nome'])
