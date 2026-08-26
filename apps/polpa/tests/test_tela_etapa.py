"""
A tela de cadastro das etapas da casa.

O modelo já funcionava e tinha teste; faltava por onde o administrador cria a
etapa. Sem tela, "o motor é configurável" era verdade só para quem abrisse o
shell do Django — que não é o administrador de uma fábrica de polpa.

O que os testes cercam:

  · O CÓDIGO SE GERA DO NOME. Ele é chave técnica e atravessa apontamento,
    receita e indicador; pedi-lo em branco faria alguém digitar "Fermentação "
    com espaço no fim e passar a semana sem entender por que a etapa some do
    relatório;
  · O CÓDIGO NÃO MUDA DEPOIS. Ele já está gravado em ordens apontadas —
    trocá-lo desligaria a etapa do histórico dela sem avisar ninguém;
  · O VOCABULÁRIO COMUM NÃO SE RECRIA, e a recusa aparece NO CAMPO, com o
    motivo;
  · A RÉGUA FICA À VISTA. A sequência decide se a etapa nova serve para
    alguma coisa, e ninguém sabe de cabeça em que posição está o envase;
  · NÃO SE APAGA ETAPA — desativa. E a tela diz por quê, senão o botão
    ausente vira chamado de suporte;
  · O MENU DESCOBRE A TELA. O hub resolve a rota do item para saber se ela
    existe; caminho e slug divergentes deixariam o selo "em breve" numa tela
    pronta.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.polpa.models import ApontamentoEtapa, Etapa, EtapaProcesso, OrdemPolpa
from apps.polpa.models.processo import POSICAO
from apps.polpa.services import CatalogoService, OrdemPolpaService, ReceitaService
from apps.polpa.models import EtapaReceita, FichaProduto
from apps.producao.models import ItemFichaTecnica
from apps.produtos.models import UnidadeMedida, UnidadeMedidaFilial

T = FichaProduto.Tipo
ZERO = Decimal('0')


class TelaEtapaBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Congelados Tela LTDA', nome_fantasia='Tela',
            cnpj='44445678000191', segmento='polpa_frutas',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='44445678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='KG', descricao='Quilograma',
            tipo=UnidadeMedida.Tipo.PESO,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='chefe@tela.local', nome='Chefe', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )

    def setUp(self):
        self.client.force_login(self.usuario)

    def _url(self, nome, *args):
        return reverse(f'polpa:{nome}', args=args)

    def _etapa(self, **campos):
        dados = {
            'filial': self.filial, 'codigo': 'fermentacao',
            'nome': 'Fermentação', 'sequencia': POSICAO[Etapa.MISTURA] + 1,
        }
        dados.update(campos)
        return EtapaProcesso.objects.create(**dados)


class ListaTests(TelaEtapaBase):

    def test_a_lista_abre(self):
        self._etapa()

        resposta = self.client.get(self._url('etapa-list'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Fermentação')
        self.assertContains(resposta, 'fermentacao')

    def test_a_regua_do_vocabulario_fica_a_vista(self):
        """
        A sequência decide se a etapa nova serve para alguma coisa, e ninguém
        sabe de cabeça em que posição está o envase.
        """
        resposta = self.client.get(self._url('etapa-list'))

        self.assertContains(resposta, 'O vocabulário comum')
        self.assertContains(resposta, 'Envase')
        self.assertContains(resposta, 'Despolpamento')

    def test_a_lista_vazia_explica_para_que_serve(self):
        resposta = self.client.get(self._url('etapa-list'))

        self.assertContains(resposta, 'Nenhuma etapa própria cadastrada')
        self.assertContains(resposta, 'defumação')

    def test_a_lista_diz_quantas_ordens_apontaram(self):
        """
        É a pergunta que a pessoa faz antes de mexer: etapa com histórico não
        se apaga.
        """
        etapa = self._etapa()
        ordem = self._ordem_com_etapa()

        resposta = self.client.get(self._url('etapa-list'))

        self.assertContains(resposta, 'apontada em 1 ordem')
        self.assertEqual(etapa.codigo, 'fermentacao')
        self.assertTrue(
            ordem.etapas_processo.filter(etapa='fermentacao').exists(),
        )

    def test_sem_uso_a_lista_diz_isso_tambem(self):
        self._etapa()

        self.assertContains(
            self.client.get(self._url('etapa-list')),
            'ainda não apontada',
        )

    def _ordem_com_etapa(self):
        acabado = CatalogoService.salvar(self.filial, {
            'tipo': T.POLPA, 'descricao': 'Polpa fermentada',
            'codigo': 'PF', 'unidade_medida': self.unidade,
        }).produto
        fruta = CatalogoService.salvar(self.filial, {
            'tipo': T.FRUTA, 'descricao': 'Fruta', 'codigo': 'FR',
            'unidade_medida': self.unidade,
        }).produto
        receita = ReceitaService.criar(self.filial, acabado, {
            'descricao': 'Polpa fermentada', 'versao': '1.0',
            'quantidade_produzida': Decimal('100'),
            'rendimento_esperado': Decimal('70'),
        })
        ItemFichaTecnica.objects.create(
            ficha=receita.ficha, materia_prima=fruta,
            quantidade=Decimal('50'), perda_prevista=ZERO,
        )
        EtapaReceita.objects.create(
            receita=receita, ordem=1, nome='Fermentação', etapa='fermentacao',
        )
        ReceitaService.ativar(receita)
        return OrdemPolpaService.criar(
            self.filial, receita,
            {'quantidade_planejada': Decimal('100')}, self.usuario,
        )


class CadastroTests(TelaEtapaBase):

    def test_cria_a_etapa_pela_tela(self):
        self.client.post(self._url('etapa-nova'), {
            'nome': 'Fermentação', 'codigo': '', 'sequencia': 15,
            'exige_peso': 'on', 'ativo': 'on',
        })

        etapa = EtapaProcesso.all_objects.get()
        self.assertEqual(etapa.nome, 'Fermentação')
        self.assertEqual(etapa.sequencia, 15)
        self.assertEqual(etapa.filial, self.filial)

    def test_o_codigo_se_gera_do_nome(self):
        """
        Ele é chave técnica. Digitado à mão sai com espaço, acento e
        maiúscula — e aí a etapa some do relatório sem ninguém entender.
        """
        self.client.post(self._url('etapa-nova'), {
            'nome': 'Apuração de ponto', 'codigo': '', 'sequencia': 15,
            'exige_peso': 'on', 'ativo': 'on',
        })

        self.assertEqual(
            EtapaProcesso.all_objects.get().codigo, 'apuracao_de_ponto',
        )

    def test_o_codigo_informado_e_respeitado(self):
        self.client.post(self._url('etapa-nova'), {
            'nome': 'Defumação', 'codigo': 'defuma', 'sequencia': 15,
            'exige_peso': 'on', 'ativo': 'on',
        })

        self.assertEqual(EtapaProcesso.all_objects.get().codigo, 'defuma')

    def test_recusa_recriar_o_vocabulario_comum(self):
        """A recusa aparece no campo, com o motivo — não numa tela de erro."""
        resposta = self.client.post(self._url('etapa-nova'), {
            'nome': 'Pasteurização da casa', 'codigo': Etapa.PASTEURIZACAO,
            'sequencia': 15, 'ativo': 'on',
        })

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'já é uma etapa do vocabulário comum')
        self.assertEqual(EtapaProcesso.all_objects.count(), 0)

    def test_sem_nome_nao_grava(self):
        resposta = self.client.post(self._url('etapa-nova'), {
            'nome': '', 'codigo': '', 'sequencia': 15,
        })

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(EtapaProcesso.all_objects.count(), 0)

    def test_avisa_que_a_etapa_nao_entra_sozinha_nas_receitas(self):
        """
        Cadastrá-la é criar o vocabulário; quem decide em quais produtos ela
        acontece é a receita. Sem o aviso, alguém cadastra e espera.
        """
        resposta = self.client.post(self._url('etapa-nova'), {
            'nome': 'Fermentação', 'codigo': '', 'sequencia': 15,
            'exige_peso': 'on', 'ativo': 'on',
        }, follow=True)

        mensagens = [str(m) for m in resposta.context['messages']]
        self.assertTrue(
            any('declare-a nas receitas' in m for m in mensagens), mensagens,
        )


class EdicaoTests(TelaEtapaBase):

    def test_edita_o_nome_e_a_posicao(self):
        etapa = self._etapa()

        self.client.post(self._url('etapa-editar', etapa.pk), {
            'nome': 'Fermentação lenta', 'sequencia': 16,
            'exige_peso': 'on', 'exige_temperatura': 'on', 'ativo': 'on',
        })

        etapa.refresh_from_db()
        self.assertEqual(etapa.nome, 'Fermentação lenta')
        self.assertEqual(etapa.sequencia, 16)
        self.assertTrue(etapa.exige_temperatura)

    def test_o_codigo_nao_muda_depois(self):
        """
        Ele já está gravado em ordens apontadas — trocá-lo desligaria a etapa
        do histórico dela sem avisar ninguém.
        """
        etapa = self._etapa()

        self.client.post(self._url('etapa-editar', etapa.pk), {
            'nome': 'Fermentação', 'codigo': 'outro_codigo', 'sequencia': 15,
            'exige_peso': 'on', 'ativo': 'on',
        })

        etapa.refresh_from_db()
        self.assertEqual(etapa.codigo, 'fermentacao')

    def test_a_tela_explica_por_que_nao_ha_apagar(self):
        """Botão ausente sem explicação vira chamado de suporte."""
        etapa = self._etapa()

        resposta = self.client.get(self._url('etapa-editar', etapa.pk))

        self.assertContains(resposta, 'Não existe apagar')
        self.assertContains(resposta, 'desmarque')

    def test_desativar_tira_de_circulacao(self):
        etapa = self._etapa()

        self.client.post(self._url('etapa-editar', etapa.pk), {
            'nome': 'Fermentação', 'sequencia': 15, 'exige_peso': 'on',
        })

        etapa.refresh_from_db()
        self.assertFalse(etapa.ativo)


class MenuTests(TelaEtapaBase):

    def test_o_hub_descobre_que_a_tela_existe(self):
        """
        O hub resolve a rota do item para saber se ela existe. Caminho e slug
        divergentes deixariam o selo "em breve" numa tela pronta.
        """
        from django.urls import resolve

        from apps.polpa.views_etapa import EtapaListView

        achado = resolve(reverse('polpa:item', args=['pcp', 'etapas-processo']))

        self.assertIs(achado.func.view_class, EtapaListView)

    def test_o_item_aparece_no_grupo_de_pcp(self):
        from apps.polpa.menu import buscar_item

        grupo, item = buscar_item('pcp', 'etapas-processo')

        self.assertIsNotNone(item)
        self.assertEqual(item.label, 'Etapas do processo')


class SemTagVazadaTests(TelaEtapaBase):

    def test_nada_de_tag_vaza_nas_duas_telas(self):
        etapa = self._etapa()

        for url in (self._url('etapa-list'), self._url('etapa-editar', etapa.pk)):
            corpo = self.client.get(url).content.decode()
            for marca in ('{%', '%}', '{#', '#}', 'endcomment'):
                self.assertNotIn(marca, corpo, f'{marca} vazou em {url}')
