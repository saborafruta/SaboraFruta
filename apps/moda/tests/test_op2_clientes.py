import json
import re
import shutil
import subprocess
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.moda.models import PedidoProducao


class Op2ClientesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(razao_social='Clientes teste', cnpj='53345678000191', codigo_regime_tributario=1)
        cls.filial = Filial.objects.create(empresa=cls.empresa, razao_social='Matriz', cnpj='53345678000272')
        cls.perfil = PerfilAcesso.objects.create(empresa=cls.empresa, nome='Admin', is_admin=True)
        cls.usuario = Usuario.objects.create_user('clientes@example.com', 'Teste', perfil=cls.perfil, filial=cls.filial, empresa=cls.empresa, is_superuser=True)
        cls.cliente = Cliente.objects.create(filial=cls.filial, razao_social='Cliente inicial', tipo_pessoa='F', ativo=True, cidade='Natal', endereco='Endereço preservado')
        cls.pedido = PedidoProducao.objects.create(filial=cls.filial, cliente=cls.cliente)

    def setUp(self):
        self.client.force_login(self.usuario)
        session = self.client.session
        session['filial_id'] = self.filial.pk
        session.save()
        self.url = reverse('moda:cliente-editar-json', args=[self.cliente.pk])

    def test_telas_oferecem_busca_cadastro_e_edicao_no_mesmo_lugar(self):
        for url in (reverse('moda:op2-create'), reverse('moda:op2-detail', args=[self.pedido.pk])):
            with self.subTest(url=url):
                resposta = self.client.get(url)
                self.assertEqual(resposta.status_code, 200)
                for trecho in ('op2_clientes.js', 'digitarCliente()', 'abrirCadastroCliente()', 'abrirCadastroCliente(clienteId)', 'name="razao_social"', 'aria-label="Cadastro de cliente"', 'style="background:#16a34a;justify-content:center"', '>Cadastrar cliente</button>', 'aria-label="Adicionar cliente"', '>+</button>'):
                    self.assertContains(resposta, trecho)
                self.assertContains(resposta, 'class="hidden md:block" style="height:12px" aria-hidden="true" data-op2-contact-alignment')
                self.assertNotContains(resposta, '>+ Adicionar cliente</button>')
                self.assertNotContains(resposta, '>+ Cadastrar cliente</button>')

    def test_edicao_carrega_os_dados_do_cliente_escolhido(self):
        resposta = self.client.get(self.url, HTTP_ACCEPT='application/json')
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json()['campos']['razao_social'], 'Cliente inicial')
        self.assertEqual(resposta.json()['campos']['cidade'], 'Natal')

    def test_cliente_principal_permanece_visivel_durante_adicao(self):
        for url in (reverse('moda:op2-create'), reverse('moda:op2-detail', args=[self.pedido.pk])):
            resposta = self.client.get(url)
            self.assertContains(resposta, 'x-show="adicionandoCliente && clienteId && clienteAtual"')
            self.assertContains(resposta, 'Cliente principal:')
            self.assertContains(resposta, 'contatos.push({nome:\'\',telefone:\'\'})')
            self.assertContains(resposta, 'name="contato_extra_nome"')
            self.assertContains(resposta, 'name="contato_extra_telefone"')
            html = resposta.content.decode()
            self.assertLess(html.index('name="contato_extra_telefone"'), html.index('>+ Contato</button>'))
            self.assertContains(resposta, 'class="flex justify-end mt-2"')

    def test_nova_op_exibe_grades_coloridas_e_campos_de_pagamento_alinhados(self):
        resposta = self.client.get(reverse('moda:op2-create'))
        from apps.moda.services.item_groups import GRADE_CORES
        self.assertEqual(resposta.context['grade_cores_json'], GRADE_CORES)
        for trecho in ('op2-grade-tag', ':style="estiloGrade(item)"', 'item.grade_nome || \'Sem grade\'', 'class="op2-payment-row"', '.op2-payment-row .form-input{height:42px}'):
            self.assertContains(resposta, trecho)
        self.assertNotContains(resposta, "[item.codigo, item.tipo_impressao_label")

    def test_contatos_extras_reabrem_separados_e_nao_duplicam_ao_salvar(self):
        self.pedido.observacoes = 'Conferir gola.\n\nContatos extras:\n- Maria: 84999990000'
        self.pedido.save()
        url = reverse('moda:op2-detail', args=[self.pedido.pk])
        resposta = self.client.get(url)
        self.assertEqual(resposta.context['observacoes_livres'], 'Conferir gola.')
        self.assertEqual(resposta.context['contatos_json'], [{'nome': 'Maria', 'telefone': '84999990000'}])
        dados = {'acao': 'cabecalho', 'cliente': self.cliente.pk, 'data_pedido': '2026-08-31', 'observacoes': 'Conferir gola.', 'contato_extra_nome': ['Maria', 'João'], 'contato_extra_telefone': ['84999990000', '84888880000']}
        resposta = self.client.post(reverse('moda:op2-action', args=[self.pedido.pk]), dados)
        self.assertEqual(resposta.status_code, 302)
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.observacoes.count('Contatos extras:'), 1)
        self.assertIn('- João: 84888880000', self.pedido.observacoes)
        dados.pop('contato_extra_nome')
        dados.pop('contato_extra_telefone')
        self.client.post(reverse('moda:op2-action', args=[self.pedido.pk]), dados)
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.observacoes, 'Conferir gola.')

    @skipUnless(shutil.which('node'), 'Node necessário para integração JavaScript')
    def test_scripts_renderizados_inicializam_e_selecionam_pelo_estado_reativo(self):
        for rota, funcao in ((reverse('moda:op2-create'), 'op2NovaMelhorada'), (reverse('moda:op2-detail', args=[self.pedido.pk]), 'op2WorkspaceCompleto')):
            with self.subTest(rota=rota):
                html = self.client.get(rota).content.decode()
                nodes = dict(re.findall(r'<script id="([^"]+)" type="application/json">(.*?)</script>', html, re.S))
                script = next(script for script in re.findall(r'<script>(.*?)</script>', html, re.S) if f'function {funcao}(' in script)
                resultado = subprocess.run([
                    shutil.which('node'), '-e', '''
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const payload = JSON.parse(fs.readFileSync(0, 'utf8'));
const context = vm.createContext({document: {getElementById: id => ({textContent: payload.nodes[id]}), addEventListener() {}}, clearTimeout() {}});
for (const source of payload.sources) vm.runInContext(fs.readFileSync(source, 'utf8'), context);
vm.runInContext(payload.script, context);
const state = context[payload.funcao]();
if (payload.funcao === 'op2NovaMelhorada') {
  assert.equal(state.enviandoFormulario, false);
  state.itens = [{uid:1, produto_id:'5'}, {uid:2, produto_id:'5'}, {uid:3, produto_id:'6'}];
  assert.equal(state.coresGrade(state.itens[0])[0], '#2563eb');
  assert.equal(state.coresGrade(state.itens[1])[0], '#7c3aed');
  assert.equal(state.coresGrade(state.itens[2])[0], '#2563eb');
  assert.ok(state.estiloGrade(state.itens[1]).includes('background:#ede9fe'));
}
const writes = [];
const proxy = new Proxy(state, {set(target, key, value) { writes.push(key); return Reflect.set(target, key, value); }});
proxy.selecionarCliente({id: 999, nome: 'Cliente selecionado', contato: 'Contato', telefone: '123'});
assert.equal(proxy.clienteId, '999');
for (const key of ['clienteId', 'buscaCliente', 'contatoNome', 'contatoTelefone']) assert.ok(writes.includes(key), key);
'''], input=json.dumps({
                    'nodes': nodes,
                    'script': script,
                    'funcao': funcao,
                    'sources': [
                        str(settings.BASE_DIR / 'static/js/op2_clientes.js'),
                        str(settings.BASE_DIR / 'static/js/op2_modelo_validacao.js'),
                    ],
                }), text=True, capture_output=True, timeout=20)
                self.assertEqual(resultado.returncode, 0, resultado.stderr)

    def test_edicao_preserva_campos_fora_do_cadastro_rapido_e_vinculo_da_op(self):
        dados = self.client.get(self.url).json()['campos']
        dados = {nome: valor for nome, valor in dados.items() if valor is not None}
        dados.update(razao_social='Cliente corrigido', contato_nome='Contato novo', celular='84999990000')
        dados.pop('contribuinte_icms', None)
        resposta = self.client.post(self.url, dados)
        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(resposta.json()['ok'])
        self.cliente.refresh_from_db()
        self.pedido.refresh_from_db()
        self.assertEqual(self.cliente.razao_social, 'Cliente corrigido')
        self.assertEqual(self.cliente.endereco, 'Endereço preservado')
        self.assertEqual(self.pedido.cliente_id, self.cliente.pk)
        self.assertEqual(resposta.json()['cliente']['contato'], 'Contato novo')

    def test_edicao_rejeita_documento_duplicado_sem_alterar_cliente(self):
        Cliente.objects.create(filial=self.filial, razao_social='Outro', cpf_cnpj='12345678901')
        resposta = self.client.post(self.url, {'razao_social': 'Tentativa', 'cpf_cnpj': '12345678901'})
        self.assertEqual(resposta.status_code, 400)
        self.cliente.refresh_from_db()
        self.assertEqual(self.cliente.razao_social, 'Cliente inicial')

    def test_edicao_rejeita_formulario_vazio(self):
        resposta = self.client.post(self.url, {})
        self.assertEqual(resposta.status_code, 400)
        self.assertIn('__all__', resposta.json()['erros'])

    def test_edicao_nao_acessa_cliente_de_outra_empresa(self):
        empresa = Empresa.objects.create(razao_social='Outra', cnpj='13345678000191', codigo_regime_tributario=1)
        filial = Filial.objects.create(empresa=empresa, razao_social='Outra', cnpj='13345678000272')
        cliente = Cliente.objects.create(filial=filial, razao_social='Não acessível')
        url = reverse('moda:cliente-editar-json', args=[cliente.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, {'razao_social': 'Alteração'}).status_code, 404)

    def test_edicao_respeita_permissao_de_cadastros(self):
        with patch.object(Usuario, 'tem_permissao', autospec=True, side_effect=lambda usuario, modulo, acao='ver': modulo != 'cadastros'):
            for metodo in (self.client.get, self.client.post):
                self.assertEqual(metodo(self.url, HTTP_ACCEPT='application/json').status_code, 403)
        self.cliente.refresh_from_db()
        self.assertEqual(self.cliente.razao_social, 'Cliente inicial')

    def test_cadastro_apenas_com_nome_fica_disponivel_na_busca(self):
        resposta = self.client.post(reverse('moda:cliente-criar-json'), {'razao_social': 'Cliente recém cadastrado'})
        self.assertEqual(resposta.status_code, 200)
        busca = self.client.get(reverse('moda:cliente-buscar'), {'q': 'recém'})
        self.assertIn(resposta.json()['cliente']['id'], [cliente['id'] for cliente in busca.json()['clientes']])
