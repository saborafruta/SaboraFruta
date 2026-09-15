import shutil
import subprocess
from decimal import Decimal
from types import SimpleNamespace
from unittest import skipUnless

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.models import (
    Empresa,
    EmpresaBanco,
    Filial,
    ParametrosSistema,
    PerfilAcesso,
    Permissao,
    Usuario,
)
from apps.core.services.checkout import checkout_venda_ativo
from apps.financeiro.constants.enums import TipoFormaPagamento
from apps.financeiro.models import FormaPagamento
from apps.pdv.models import Caixa, SessaoPDV, VendaPDV
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
            is_admin=False,
        )
        Permissao.objects.create(
            perfil=cls.perfil,
            modulo=Permissao.Modulo.PDV,
            pode_ver=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='checkout@inoovated.com',
            nome='Operador Checkout',
            password='teste1234',
            empresa=cls.empresa,
            filial=cls.filial,
            perfil=cls.perfil,
        )
        cls.perfil_supervisor = PerfilAcesso.objects.create(
            empresa=cls.empresa,
            nome='Supervisor Checkout',
            is_admin=False,
        )
        cls.permissao_supervisor = Permissao.objects.create(
            perfil=cls.perfil_supervisor,
            modulo=Permissao.Modulo.PDV,
            pode_ver=True,
            pode_aprovar=True,
        )
        cls.supervisor = Usuario.objects.create_user(
            email='supervisor-checkout@inoovated.com',
            nome='Supervisor Checkout',
            password='Senha-Supervisor-42',
            empresa=cls.empresa,
            filial=cls.filial,
            perfil=cls.perfil_supervisor,
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
        self.assertContains(resposta, '<kbd class="co-doc-key">F2</kbd>', html=True)
        self.assertContains(resposta, '<kbd class="co-doc-key">F3</kbd>', html=True)
        self.assertContains(resposta, '<kbd class="co-doc-key">F4</kbd>', html=True)
        self.assertContains(resposta, '<kbd class="co-doc-key">F5</kbd>', html=True)
        self.assertContains(resposta, '<kbd>F10</kbd>Pular → Nova venda', html=True)
        self.assertContains(resposta, 'atalhoDocumento(evento)')
        self.assertContains(resposta, "this.emitirFiscal('nfce')")
        self.assertContains(resposta, "this.emitirFiscal('nfe')")
        self.assertContains(resposta, "emitirFiscal('nfce')")
        self.assertContains(resposta, '/pdv/venda/0/comprovante/')
        self.assertContains(resposta, 'class="co-table" data-columns="off"')
        self.assertContains(resposta, "scrollIntoView({block:'nearest'})")

    def test_desconto_exige_aprovacao_e_escape_nao_fecha_modal_pos_venda(self):
        self.habilitar_checkout()

        resposta = self.client.get(reverse('pdv:checkout'))

        self.assertContains(resposta, 'Autorizar desconto')
        self.assertContains(resposta, 'Exige PDV → Aprovar')
        self.assertContains(resposta, ':readonly="!descontoLiberado"')
        self.assertContains(resposta, reverse('pdv:api_checkout_venda_finalizar'))
        self.assertContains(resposta, "else if(this.modalDocumento)return;")
        self.assertContains(resposta, "this.mensagemDocumento=label+' emitida com sucesso.';")

    def test_endpoint_do_checkout_bloqueia_desconto_sem_aprovacao_e_consumo_e_unico(self):
        self.habilitar_checkout()
        caixa = Caixa.objects.create(filial=self.filial, numero=41, descricao='Checkout 41')
        SessaoPDV.objects.create(
            filial=self.filial,
            caixa=caixa,
            usuario=self.usuario,
            valor_abertura=Decimal('0'),
            status='aberto',
        )
        forma = FormaPagamento.objects.create(
            empresa=self.empresa,
            descricao='Dinheiro Checkout',
            tipo=TipoFormaPagamento.DINHEIRO,
        )
        payload = {
            'cliente_id': None,
            'desconto': '1.00',
            'acrescimo': 0,
            'itens': [{
                'produto_id': self.produto.pk,
                'quantidade': 1,
                'valor_unitario': '18.90',
                'preco_origem_tipo': 'normal',
                'preco_origem_detalhe': 'Preço normal',
                'desconto_valor': 0,
            }],
            'pagamentos': [{'forma_id': forma.pk, 'valor': '17.90'}],
        }
        endpoint = reverse('pdv:api_checkout_venda_finalizar')

        bloqueada = self.client.post(endpoint, data=payload, content_type='application/json')
        self.assertEqual(bloqueada.status_code, 403, bloqueada.content)
        self.assertEqual(VendaPDV.objects.count(), 0)

        liberacao = self.client.post(
            reverse('pdv:api_checkout_liberar_desconto'),
            data={
                'usuario_id': f'usuario:{self.supervisor.pk}',
                'senha': 'Senha-Supervisor-42',
            },
            content_type='application/json',
        )
        self.assertEqual(liberacao.status_code, 200, liberacao.content)

        finalizada = self.client.post(endpoint, data=payload, content_type='application/json')
        self.assertEqual(finalizada.status_code, 200, finalizada.content)
        self.assertEqual(VendaPDV.objects.count(), 1)

        repetida = self.client.post(endpoint, data=payload, content_type='application/json')
        self.assertEqual(repetida.status_code, 403, repetida.content)

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

    @skipUnless(shutil.which('node'), 'Node.js necessário para validar o JavaScript do checkout')
    def test_setas_percorrem_resultados_e_mantem_selecao_visivel(self):
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
let rolado = null;
checkout.$refs = {resultadosProduto: {querySelector: (seletor) => ({
  scrollIntoView: (opcoes) => { rolado = [seletor, opcoes]; }
})}};
checkout.$nextTick = (callback) => callback();
checkout.resultados = [{id:1},{id:2},{id:3}];
checkout.indiceProduto = 0;
checkout.moverResultadoProduto(1);
assert.equal(checkout.indiceProduto, 1);
assert.deepEqual(rolado, ['.co-result.active', {block:'nearest'}]);
checkout.moverResultadoProduto(-1);
assert.equal(checkout.indiceProduto, 0);
checkout.moverResultadoProduto(-1);
assert.equal(checkout.indiceProduto, 2);
'''

        resultado = subprocess.run(
            [shutil.which('node')], input=script, text=True, encoding='utf-8',
            capture_output=True, timeout=20,
        )

        self.assertEqual(resultado.returncode, 0, resultado.stdout + resultado.stderr)

    @skipUnless(shutil.which('node'), 'Node.js necessário para validar o JavaScript do checkout')
    def test_atalhos_do_modal_executam_a_acao_correta(self):
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
const acoes = [];
checkout.modalDocumento = true;
checkout.etiquetaVendaDisponivel = true;
checkout.abrirComprovante = (imprimir) => acoes.push(['comprovante', imprimir]);
checkout.baixarComprovante = () => acoes.push(['pdf']);
checkout.emitirFiscal = (tipo) => acoes.push(['fiscal', tipo]);
checkout.imprimirEtiqueta = () => acoes.push(['etiqueta']);
checkout.fecharDocumento = () => acoes.push(['nova-venda']);
function tecla(key) {
  let prevenido = false;
  checkout.atalhoTeclado({key, repeat: false, preventDefault: () => { prevenido = true; }});
  assert.equal(prevenido, true, key);
}
['F2','F3','F4','F5','F6','F10'].forEach(tecla);
assert.deepEqual(acoes, [
  ['comprovante', true], ['pdf'], ['fiscal', 'nfce'],
  ['fiscal', 'nfe'], ['etiqueta'], ['nova-venda']
]);
'''

        resultado = subprocess.run(
            [shutil.which('node')], input=script, text=True, encoding='utf-8',
            capture_output=True, timeout=20,
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

    def test_busca_por_nome_aparece_para_operador_sem_aprovacao_mas_exige_autorizacao(self):
        self.habilitar_checkout()
        Usuario.objects.create_user(
            email='supervisor-outra-filial@inoovated.com',
            nome='Supervisor Outra Filial',
            password='Senha-Supervisor-43',
            empresa=self.empresa,
            filial=self.outra_filial,
            perfil=self.perfil_supervisor,
        )
        resposta = self.buscar('Especial Checkout', por_nome='1')
        tela = self.client.get(reverse('pdv:checkout'))

        self.assertEqual(resposta.status_code, 403)
        self.assertFalse(self.usuario.tem_permissao('pdv', 'aprovar'))
        self.assertContains(tela, 'Permitir nome')
        self.assertContains(tela, 'Autorizar busca por nome')
        self.assertContains(tela, 'Usuário autorizador')
        self.assertContains(tela, 'Autorizar um item')
        self.assertEqual(
            tela.context['usuarios_autorizadores'],
            [{
                'id': f'usuario:{self.supervisor.pk}',
                'nome': self.supervisor.nome,
                'email': self.supervisor.email,
            }],
        )
        self.assertEqual(self.buscar('Especial Checkout', por_nome='1').status_code, 403)

    def test_credenciais_sem_permissao_nao_liberam_busca_por_nome(self):
        self.habilitar_checkout()
        endpoint = reverse('pdv:api_checkout_liberar_busca_nome')

        resposta = self.client.post(
            endpoint,
            data=f'{{"usuario_id":"usuario:{self.usuario.pk}","senha":"teste1234"}}',
            content_type='application/json',
        )
        self.assertEqual(resposta.status_code, 403)
        self.assertEqual(self.buscar('Especial Checkout', por_nome='1').status_code, 403)

    def test_superusuario_ativo_aparece_e_pode_autorizar_sem_perfil_aprovador(self):
        self.habilitar_checkout()
        superusuario = Usuario.objects.create_superuser(
            email='ited@ited.com.br',
            nome='iTed Superusuário',
            password='Senha-Superuser-42',
            empresa=self.empresa,
            filial=self.outra_filial,
            perfil=self.perfil,
        )

        tela = self.client.get(reverse('pdv:checkout'))
        emails = [item['email'] for item in tela.context['usuarios_autorizadores']]
        self.assertIn(superusuario.email, emails)

        liberacao = self.client.post(
            reverse('pdv:api_checkout_liberar_busca_nome'),
            data=f'{{"usuario_id":"super:{superusuario.pk}","senha":"Senha-Superuser-42"}}',
            content_type='application/json',
        )

        self.assertEqual(liberacao.status_code, 200, liberacao.content)
        self.assertEqual(self.buscar('Especial Checkout', por_nome='1').status_code, 200)

    def test_supervisor_com_credenciais_e_aprovacao_libera_busca_por_nome(self):
        self.habilitar_checkout()
        endpoint = reverse('pdv:api_checkout_liberar_busca_nome')

        liberacao = self.client.post(
            endpoint,
            data=f'{{"usuario_id":"usuario:{self.supervisor.pk}","senha":"Senha-Supervisor-42"}}',
            content_type='application/json',
        )
        self.assertEqual(liberacao.status_code, 200, liberacao.content)
        self.assertEqual(liberacao.json(), {'ok': True})

        resposta = self.buscar('Especial Checkout', por_nome='1')

        self.assertEqual(resposta.status_code, 200, resposta.content)
        self.assertEqual(
            [item['id'] for item in resposta.json()['produtos']],
            [self.produto.pk],
        )
        self.assertTrue(resposta.json()['busca_por_nome'])

    def test_autorizacao_por_nome_e_consumida_ao_adicionar_um_item(self):
        self.habilitar_checkout()
        liberacao = self.client.post(
            reverse('pdv:api_checkout_liberar_busca_nome'),
            data=f'{{"usuario_id":"usuario:{self.supervisor.pk}","senha":"Senha-Supervisor-42"}}',
            content_type='application/json',
        )
        self.assertEqual(liberacao.status_code, 200, liberacao.content)
        self.assertEqual(self.buscar('Especial Checkout', por_nome='1').status_code, 200)

        consumo = self.client.post(
            reverse('pdv:api_checkout_consumir_busca_nome'),
            data=f'{{"produto_id":{self.produto.pk}}}',
            content_type='application/json',
        )

        self.assertEqual(consumo.status_code, 200, consumo.content)
        self.assertEqual(self.buscar('Especial Checkout', por_nome='1').status_code, 403)
        segundo_consumo = self.client.post(
            reverse('pdv:api_checkout_consumir_busca_nome'),
            data=f'{{"produto_id":{self.produto.pk}}}',
            content_type='application/json',
        )
        self.assertEqual(segundo_consumo.status_code, 403)

    def test_tentativas_repetidas_de_senha_sao_limitadas(self):
        self.habilitar_checkout()
        endpoint = reverse('pdv:api_checkout_liberar_busca_nome')

        for _ in range(5):
            resposta = self.client.post(
                endpoint,
                data=f'{{"usuario_id":"usuario:{self.supervisor.pk}","senha":"incorreta"}}',
                content_type='application/json',
            )
            self.assertEqual(resposta.status_code, 403)

        bloqueada = self.client.post(
            endpoint,
            data=f'{{"usuario_id":"usuario:{self.supervisor.pk}","senha":"Senha-Supervisor-42"}}',
            content_type='application/json',
        )
        self.assertEqual(bloqueada.status_code, 429)

    def test_retirar_permissao_do_supervisor_invalida_autorizacao_anterior(self):
        self.habilitar_checkout()
        endpoint = reverse('pdv:api_checkout_liberar_busca_nome')
        self.client.post(
            endpoint,
            data=f'{{"usuario_id":"usuario:{self.supervisor.pk}","senha":"Senha-Supervisor-42"}}',
            content_type='application/json',
        )
        self.assertEqual(self.buscar('Especial Checkout', por_nome='1').status_code, 200)

        self.permissao_supervisor.pode_aprovar = False
        self.permissao_supervisor.save(update_fields=['pode_aprovar'])

        self.assertEqual(self.buscar('Especial Checkout', por_nome='1').status_code, 403)
