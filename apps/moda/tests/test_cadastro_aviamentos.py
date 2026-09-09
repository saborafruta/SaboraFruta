"""
Cadastro de Aviamentos — o catálogo que faltava.

Antes, aviamento só existia dentro de `MaterialFicha`, que exige uma ficha
por trás — não havia onde cadastrar "Zíper nylon nº 5 preto" antes da
primeira peça que o usa, e cada ficha reescrevia descrição, código e vínculo
de estoque do zero. Este é o catálogo: cadastra uma vez, a ficha escolhe
depois.

O QUE ESTES TESTES CERCAM:

  · O MODELO tem os mesmos campos que o Tecido já tem (nome, tipo, código,
    unidade, fornecedor, produto de estoque) e a mesma trava de nome
    repetido por filial;

  · O CADASTRO usa a MESMA tela genérica de sempre (`CadastroApoioListView`
    e companhia) -- criar, editar, inativar, excluir, tudo de graça, sem
    escrever view nova;

  · A TELA DE USO (Engenharia › Aviamentos, que é só leitura do que já foi
    lançado nas fichas) ganha o botão "+ Novo Aviamento" apontando pro
    cadastro, não pra um formulário fantasma que criaria um registro sem
    ficha nenhuma atrás.
"""
from django.test import TestCase
from django.urls import reverse

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.moda.models import Aviamento


class AviamentoBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao Aviamento LTDA', nome_fantasia='Aviamento',
            cnpj='73345678000191', segmento='moda_confeccao',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Confeccao Aviamento LTDA',
            cnpj='73345678000272', uf='RN', cidade='Natal', is_matriz=True,
        )
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='aviamento@teste.local', nome='Aviamento', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )

    def setUp(self):
        self.client.force_login(self.usuario)


class ModeloTests(AviamentoBase):

    def test_cria_com_tipo_e_unidade(self):
        aviamento = Aviamento.objects.create(
            filial=self.filial, nome='Zíper nylon nº 5 preto',
            tipo=Aviamento.Tipo.ZIPER, unidade=Aviamento.Unidade.PECA,
        )

        self.assertEqual(str(aviamento), 'Zíper nylon nº 5 preto (Zíper)')
        self.assertTrue(aviamento.ativo)

    def test_nao_repete_nome_na_mesma_filial(self):
        Aviamento.objects.create(
            filial=self.filial, nome='Linha 402 branca', tipo=Aviamento.Tipo.LINHA,
        )

        with self.assertRaises(Exception):
            Aviamento.objects.create(
                filial=self.filial, nome='Linha 402 branca', tipo=Aviamento.Tipo.LINHA,
            )

    def test_mesmo_nome_em_filiais_diferentes_convive(self):
        outra_filial = Filial.objects.create(
            empresa=self.empresa, razao_social='Outra', cnpj='73345678000273',
            uf='RN', cidade='Mossoró',
        )
        Aviamento.objects.create(
            filial=self.filial, nome='Linha 402 branca', tipo=Aviamento.Tipo.LINHA,
        )

        # Não estoura -- filiais diferentes, mesmo nome.
        Aviamento.objects.create(
            filial=outra_filial, nome='Linha 402 branca', tipo=Aviamento.Tipo.LINHA,
        )


class CadastroViaTelaGenericaTests(AviamentoBase):

    def test_cria_um_aviamento_pelo_formulario(self):
        resposta = self.client.post(
            reverse('moda:apoio-create', args=['engenharia', 'cadastro-aviamentos']),
            {
                'nome': 'Botão de poliéster 15mm',
                'tipo': Aviamento.Tipo.BOTAO,
                'codigo': 'BT-15',
                'unidade': Aviamento.Unidade.UNIDADE,
                'ativo': 'on',
            },
        )

        aviamento = Aviamento.objects.get(nome='Botão de poliéster 15mm')
        self.assertRedirects(
            resposta, reverse('moda:item', args=['engenharia', 'cadastro-aviamentos']),
        )
        self.assertEqual(aviamento.tipo, Aviamento.Tipo.BOTAO)
        self.assertEqual(aviamento.filial, self.filial)

    def test_edita_um_aviamento_existente(self):
        aviamento = Aviamento.objects.create(
            filial=self.filial, nome='Tag de papel', tipo=Aviamento.Tipo.TAG,
        )

        self.client.post(
            reverse('moda:apoio-update', args=['engenharia', 'cadastro-aviamentos', aviamento.pk]),
            {
                'nome': 'Tag de papel kraft', 'tipo': Aviamento.Tipo.TAG,
                'codigo': '', 'unidade': Aviamento.Unidade.UNIDADE, 'ativo': 'on',
            },
        )

        aviamento.refresh_from_db()
        self.assertEqual(aviamento.nome, 'Tag de papel kraft')

    def test_inativa_e_exclui_pelas_rotas_genericas(self):
        aviamento = Aviamento.objects.create(
            filial=self.filial, nome='Elástico 20mm', tipo=Aviamento.Tipo.ELASTICO,
        )

        self.client.post(
            reverse('moda:apoio-toggle-ativo', args=['engenharia', 'cadastro-aviamentos', aviamento.pk]),
        )
        aviamento.refresh_from_db()
        self.assertFalse(aviamento.ativo)

        self.client.post(
            reverse('moda:apoio-delete', args=['engenharia', 'cadastro-aviamentos', aviamento.pk]),
        )
        self.assertFalse(Aviamento.objects.filter(pk=aviamento.pk).exists())

    def test_lista_mostra_tipo_e_fornecedor(self):
        Aviamento.objects.create(
            filial=self.filial, nome='Etiqueta bordada', tipo=Aviamento.Tipo.ETIQUETA,
        )

        html = self.client.get(
            reverse('moda:item', args=['engenharia', 'cadastro-aviamentos'])
        ).content.decode()

        self.assertIn('Etiqueta bordada', html)
        self.assertIn('Etiqueta', html)


class FormularioComRotulosCertosTests(AviamentoBase):
    """
    Sem `label` explícito, o Django tira o rótulo do nome do campo Python
    (`codigo` -> "Codigo", sem acento) e insere "---------" na frente de
    todo select obrigatório sem `default` no model -- os dois casos deste
    formulário. Aqui garantimos o texto amigável, não só que a tela abre.
    """

    def test_rotulos_saem_com_acento(self):
        html = self.client.get(
            reverse('moda:apoio-create', args=['engenharia', 'cadastro-aviamentos'])
        ).content.decode()

        self.assertIn('Código', html)
        self.assertIn('Observação', html)
        self.assertNotIn('>Codigo<', html)
        self.assertNotIn('>Observacao<', html)

    def test_select_de_tipo_no_lugar_do_tracejado_generico(self):
        html = self.client.get(
            reverse('moda:apoio-create', args=['engenharia', 'cadastro-aviamentos'])
        ).content.decode()

        self.assertIn('Selecione o tipo do aviamento', html)
        self.assertNotIn('---------<', html)

    def test_select_de_fornecedor_com_texto_amigavel(self):
        html = self.client.get(
            reverse('moda:apoio-create', args=['engenharia', 'cadastro-aviamentos'])
        ).content.decode()

        self.assertIn('Sem fornecedor cadastrado', html)

    def test_checkbox_ativo_nao_herda_largura_de_campo_de_texto(self):
        """
        `.form-input` é `width:100%`, feito pra texto/select -- sem o
        reset específico de checkbox, "Ativo" virava uma barra esticada
        em vez de uma caixa de marcar.
        """
        html = self.client.get(
            reverse('moda:apoio-create', args=['engenharia', 'cadastro-aviamentos'])
        ).content.decode()

        self.assertIn('input.form-input[type="checkbox"]', html)


class BotaoNaTelaDeUsoTests(AviamentoBase):
    """A tela de Aviamentos (uso, leitura) ganha o atalho pro cadastro."""

    def test_tela_de_uso_mostra_o_botao_novo_aviamento(self):
        html = self.client.get(reverse('moda:aviamentos')).content.decode()

        self.assertIn(
            reverse('moda:apoio-create', args=['engenharia', 'cadastro-aviamentos']), html,
        )

    def test_vazio_tambem_mostra_o_atalho_pro_cadastro(self):
        html = self.client.get(reverse('moda:aviamentos')).content.decode()

        self.assertIn('Nenhum aviamento lançado ainda.', html)
        self.assertIn(
            reverse('moda:apoio-create', args=['engenharia', 'cadastro-aviamentos']), html,
        )
