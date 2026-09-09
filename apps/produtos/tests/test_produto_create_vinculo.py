"""
"+ Novo produto" a partir de outro cadastro (o Tecido da Moda, por
exemplo): a tela de criar produto aceita `nome`/`codigo` na URL pra não
pedir de novo o que quem mandou já sabia, e um `next` pra voltar pra lá
depois de salvar -- devolvendo o id do produto recém-criado em
`produto_criado` pra quem chamou vincular sozinho.

O QUE ESTES TESTES CERCAM:

  · PRÉ-PREENCHER não é obrigar: `nome`/`codigo` na URL só sugerem, o
    formulário continua editável;

  · `next` só é aceito quando aponta pro próprio site -- um `next` de
    fora seria redirecionamento aberto pra fora do ERP;

  · SEM `next` o comportamento de sempre continua (volta pro próprio
    produto criado), pra não quebrar quem chega direto em "Novo Produto"
    pelo menu, sem vir de lugar nenhum.
"""
from django.test import TestCase
from django.urls import reverse

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario


class ProdutoCreateVinculoBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Empresa Produto Vinculo LTDA', nome_fantasia='Produto Vinculo',
            cnpj='83345678000101', regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Empresa Produto Vinculo LTDA',
            cnpj='83345678000282', uf='RN', cidade='Natal', is_matriz=True,
        )
        perfil = PerfilAcesso.objects.create(empresa=cls.empresa, nome='Admin', is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email='produto-vinculo@teste.local', nome='Produto Vinculo', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )

    def setUp(self):
        self.client.force_login(self.usuario)


class PreenchimentoInicialTests(ProdutoCreateVinculoBase):

    def test_nome_e_codigo_da_url_preenchem_o_formulario(self):
        resposta = self.client.get(
            reverse('produtos:produto-create'), {'nome': 'Active Air', 'codigo': 'TEC-01'},
        )

        self.assertEqual(resposta.context['form'].initial.get('descricao'), 'Active Air')
        self.assertEqual(resposta.context['form'].initial.get('codigo'), 'TEC-01')

    def test_sem_nome_e_codigo_no_form_continua_igual(self):
        resposta = self.client.get(reverse('produtos:produto-create'))

        self.assertNotIn('descricao', resposta.context['form'].initial)
        self.assertNotIn('codigo', resposta.context['form'].initial)


class NextSeguroTests(ProdutoCreateVinculoBase):

    def test_next_do_proprio_site_aparece_no_form_como_campo_oculto(self):
        destino = '/moda/engenharia/materiais/7/editar/'

        html = self.client.get(
            reverse('produtos:produto-create'), {'next': destino}
        ).content.decode()

        self.assertIn(f'value="{destino}"', html)

    def test_next_de_fora_e_ignorado(self):
        html = self.client.get(
            reverse('produtos:produto-create'), {'next': 'https://evil.example.com/roubo/'}
        ).content.decode()

        self.assertNotIn('evil.example.com', html)
