"""
Pré-seleção de cliente ao abrir o PDV via `?cliente=<id>`.

Usada pelo botão "Nova Venda" do popup do mapa e pelo "Vender" do CRM. Sem
isso a venda abria em "Consumidor Final" e o operador tinha de procurar o
cliente de novo.
"""
import json

from django.test import TestCase, override_settings
from django.urls import reverse


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ClienteInicialTests(TestCase):
    def _filial(self, nome, cnpj):
        from apps.core.models import Empresa, Filial

        empresa = Empresa.objects.create(
            razao_social=nome, cnpj=cnpj,
            regime_tributario='simples', codigo_regime_tributario=1,
        )
        return Filial.objects.create(
            empresa=empresa, razao_social=nome, nome_fantasia=nome,
            cnpj=cnpj, uf='RN', is_matriz=True,
        )

    def _logar(self, filial):
        from apps.core.models import PerfilAcesso, Usuario

        perfil = PerfilAcesso.objects.create(
            empresa=filial.empresa, nome=f'Perfil {filial.pk}', is_admin=True,
        )
        usuario = Usuario.objects.create_user(
            email=f'op{filial.pk}@teste.local', nome='Operador',
            password='senha-de-teste-123',
            empresa=filial.empresa, perfil=perfil, filial=filial,
        )
        self.client.force_login(usuario)
        sessao = self.client.session
        sessao['filial_ativa_id'] = filial.pk
        sessao.save()
        return usuario

    def _cliente(self, filial, **kw):
        from apps.cadastros.models import Cliente

        dados = dict(
            razao_social='LUCIENE SANTOS', nome_fantasia='',
            cpf_cnpj='12345678901', celular='84986226692',
            endereco='Rua Sao Miguel', numero='100', bairro='Centro',
            cidade='Natal', uf='RN', cep='59000000', ativo=True,
        )
        dados.update(kw)
        return Cliente.objects.create(filial=filial, **dados)

    def _payload(self, resposta):
        """Extrai o JSON que a view injeta no template."""
        return resposta.context['cliente_inicial_json']

    # ------------------------------------------------------------------
    def test_sem_parametro_nao_pre_seleciona(self):
        filial = self._filial('AAA', '11222333000181')
        self._logar(filial)
        resp = self.client.get(reverse('pdv:home'))
        self.assertEqual(self._payload(resp), 'null')

    def test_pre_seleciona_cliente_do_escopo(self):
        filial = self._filial('AAA', '11222333000181')
        self._logar(filial)
        cliente = self._cliente(filial)

        resp = self.client.get(reverse('pdv:home'), {'cliente': cliente.pk})
        dados = json.loads(self._payload(resp))

        self.assertEqual(dados['id'], cliente.pk)
        self.assertEqual(dados['razao_social'], 'LUCIENE SANTOS')
        self.assertEqual(dados['celular'], '84986226692')

    def test_payload_tem_os_campos_que_o_pdv_consome(self):
        """
        Contrato com `selecionarCliente()` do template.

        Já houve bug de endereço desaparecendo do cupom por objeto de cliente
        montado pela metade — este teste trava o conjunto de campos.
        """
        filial = self._filial('AAA', '11222333000181')
        self._logar(filial)
        cliente = self._cliente(filial)

        resp = self.client.get(reverse('pdv:home'), {'cliente': cliente.pk})
        dados = json.loads(self._payload(resp))

        for campo in (
            'id', 'razao_social', 'nome_fantasia', 'cpf_cnpj', 'celular',
            'telefone', 'endereco_entrega', 'tem_endereco',
            'tabela_preco_id', 'tabela_preco_nome',
        ):
            self.assertIn(campo, dados, f'falta {campo} no payload')

    def test_endereco_entrega_vem_preenchido(self):
        filial = self._filial('AAA', '11222333000181')
        self._logar(filial)
        cliente = self._cliente(filial)

        resp = self.client.get(reverse('pdv:home'), {'cliente': cliente.pk})
        dados = json.loads(self._payload(resp))

        self.assertTrue(dados['tem_endereco'])
        self.assertEqual(dados['endereco_entrega'].get('rua'), 'Rua Sao Miguel')

    def test_cliente_de_outra_empresa_e_ignorado(self):
        """Isolamento entre inquilinos: id alheio não pré-seleciona nada."""
        filial_a = self._filial('AAA', '11222333000181')
        filial_b = self._filial('BBB', '99888777000166')
        cliente_b = self._cliente(filial_b, cpf_cnpj='98765432100')

        self._logar(filial_a)
        resp = self.client.get(reverse('pdv:home'), {'cliente': cliente_b.pk})
        self.assertEqual(self._payload(resp), 'null')

    def test_cliente_inativo_e_ignorado(self):
        filial = self._filial('AAA', '11222333000181')
        self._logar(filial)
        cliente = self._cliente(filial, ativo=False)

        resp = self.client.get(reverse('pdv:home'), {'cliente': cliente.pk})
        self.assertEqual(self._payload(resp), 'null')

    def test_id_inexistente_nao_quebra_o_pdv(self):
        filial = self._filial('AAA', '11222333000181')
        self._logar(filial)
        resp = self.client.get(reverse('pdv:home'), {'cliente': 999999})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._payload(resp), 'null')

    def test_id_nao_numerico_nao_quebra_o_pdv(self):
        """Link malformado abre o PDV normalmente, em vez de dar 500."""
        filial = self._filial('AAA', '11222333000181')
        self._logar(filial)
        resp = self.client.get(reverse('pdv:home'), {'cliente': 'abc; drop table'})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._payload(resp), 'null')
