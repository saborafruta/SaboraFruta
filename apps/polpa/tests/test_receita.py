"""
A receita: participação, versão, rendimento e custo.

O QUE ESTES TESTES CERCAM:

  · NÃO EXISTE SEGUNDA FICHA TÉCNICA. A receita mora ao lado da
    `producao.FichaTecnica`, que é a que a ordem de produção lê. Se um dia
    virar tabela própria, a fábrica produz por uma e custeia pela outra;

  · A PARTICIPAÇÃO É SOBRE OS INGREDIENTES. Somar a embalagem na base faria
    "60% de fruta" virar 58% porque o pote entrou na conta — e é o
    percentual de fruta que o rótulo declara;

  · A PERDA ENTRA NO CUSTO. Comprar 1.000 kg de manga para tirar 600 kg de
    polpa não custa "600 kg de manga": o custo inteiro fica sobre o que
    sobrou. Ignorar a perda subestima o custo justamente onde a margem é
    menor;

  · VERSÃO É REGISTRO, NÃO EDIÇÃO. Mudar a receita que já produziu lotes
    apagaria a explicação do que foi feito neles. E só UMA fica ativa por
    produto: duas fariam a ordem "escolher" a fórmula;

  · O RENDIMENTO REAL SAI DAS ORDENS. Campo digitado é lembrança; ordem
    encerrada é o que a fábrica fez. Sem ordem nenhuma, é `None` — zero
    seria lido como rendimento péssimo.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import DomainError
from apps.polpa.models import FichaProduto, Receita
from apps.polpa.services import CatalogoService, ReceitaService
from apps.producao.models import FichaTecnica, ItemFichaTecnica, OrdemProducao
from apps.produtos.models import UnidadeMedida, UnidadeMedidaFilial

C = FichaProduto.Classe
T = FichaProduto.Tipo


class ReceitaBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Polpas Receita LTDA', nome_fantasia='Receita',
            cnpj='93345678000191', segmento='polpa_frutas',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='93345678000272',
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
            email='chefe@receita.local', nome='Chefe', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        self.acabado = self._item(
            T.POLPA, 'Polpa de manga 100 g', validade_dias=365,
            peso_liquido=Decimal('0.100'), quantidade_por_embalagem=Decimal('50'),
        )
        self.manga = self._item(T.FRUTA, 'Manga in natura', custo=Decimal('1.50'))
        self.acucar = self._item(T.ACUCAR, 'Acucar cristal', custo=Decimal('4.00'))
        self.pote = self._item(T.POTE, 'Pote 100 g', custo=Decimal('0.20'))

    def _item(self, tipo, descricao, custo=Decimal('0'), **extras):
        dados = {
            'tipo': tipo, 'descricao': descricao,
            'unidade_medida': self.unidade, 'preco_custo': custo,
            'codigo': descricao[:10],
        }
        dados.update(extras)
        ficha = CatalogoService.salvar(self.filial, dados)
        # `preco_custo_medio` é o que a receita usa; numa fábrica ele vem das
        # entradas, e aqui é fixado para a conta ser conferível.
        ficha.produto.preco_custo_medio = custo
        ficha.produto.save(update_fields=['preco_custo_medio'])
        return ficha.produto

    def _receita(self, **campos):
        dados = {
            'descricao': 'Polpa de manga 100 g', 'versao': '1.0',
            'quantidade_produzida': Decimal('1000'),
            'rendimento_esperado': Decimal('60'),
            'custo_mao_obra_padrao': Decimal('100'),
            'custo_indireto_padrao': Decimal('50'),
        }
        dados.update(campos)
        return ReceitaService.criar(self.filial, self.acabado, dados)

    def _com_itens(self):
        receita = self._receita()
        ItemFichaTecnica.objects.create(
            ficha=receita.ficha, materia_prima=self.manga,
            quantidade=Decimal('100'), perda_prevista=Decimal('0'),
        )
        ItemFichaTecnica.objects.create(
            ficha=receita.ficha, materia_prima=self.acucar,
            quantidade=Decimal('25'), perda_prevista=Decimal('0'),
        )
        ItemFichaTecnica.objects.create(
            ficha=receita.ficha, materia_prima=self.pote,
            quantidade=Decimal('1000'), perda_prevista=Decimal('0'),
        )
        return receita


class EstruturaTests(ReceitaBase):
    """A receita mora ao lado da ficha do ERP."""

    def test_criar_receita_cria_a_ficha_tecnica_do_erp(self):
        receita = self._receita()

        self.assertEqual(FichaTecnica.objects.count(), 1)
        self.assertEqual(receita.ficha.produto_acabado, self.acabado)
        self.assertEqual(receita.ficha.status, FichaTecnica.Status.RASCUNHO)

    def test_a_receita_nasce_em_rascunho(self):
        """
        Ativa por padrão faria a produção passar a usar uma fórmula que
        ninguém terminou de escrever.
        """
        receita = self._receita()

        self.assertFalse(receita.ativa)


class ParticipacaoTests(ReceitaBase):
    """O percentual de cada ingrediente."""

    def test_a_participacao_ignora_a_embalagem(self):
        receita = self._com_itens()

        separados = ReceitaService.itens(receita)

        self.assertEqual(len(separados['ingredientes']), 2)
        self.assertEqual(len(separados['embalagens']), 1)
        # 100 kg de manga sobre 125 kg de base = 80%.
        por_nome = {
            l['item'].materia_prima.descricao: l['participacao']
            for l in separados['ingredientes']
        }
        self.assertEqual(por_nome['Manga in natura'], Decimal('80.00'))
        self.assertEqual(por_nome['Acucar cristal'], Decimal('20.00'))

    def test_a_separacao_sai_da_ficha_do_produto(self):
        """
        O pote já foi cadastrado como embalagem uma vez; perguntar de novo a
        cada receita é como as duas respostas passam a divergir.
        """
        receita = self._com_itens()

        embalagens = ReceitaService.itens(receita)['embalagens']

        self.assertEqual(embalagens[0].materia_prima, self.pote)

    def test_receita_sem_ingrediente_nao_divide_por_zero(self):
        receita = self._receita()

        separados = ReceitaService.itens(receita)

        self.assertEqual(separados['ingredientes'], [])
        self.assertEqual(separados['base'], Decimal('0'))


class CustoTests(ReceitaBase):
    """As contas que a fábrica pergunta."""

    def test_custo_separa_materia_prima_de_embalagem(self):
        receita = self._com_itens()

        custos = ReceitaService.custos(receita)

        # 100 kg × 1,50 + 25 kg × 4,00 = 250,00
        self.assertEqual(custos['materia_prima'], Decimal('250.00'))
        # 1.000 potes × 0,20 = 200,00
        self.assertEqual(custos['embalagem'], Decimal('200.00'))
        self.assertEqual(custos['processo'], Decimal('150.00'))
        self.assertEqual(custos['total'], Decimal('600.00'))

    def test_a_perda_prevista_entra_no_custo(self):
        """
        Comprar 1.000 kg para tirar 600 kg de polpa não custa "600 kg de
        manga": o custo inteiro fica sobre o que sobrou.
        """
        receita = self._receita()
        ItemFichaTecnica.objects.create(
            ficha=receita.ficha, materia_prima=self.manga,
            quantidade=Decimal('100'), perda_prevista=Decimal('10'),
        )

        custos = ReceitaService.custos(receita)

        # 110 kg (100 + 10% de perda) × 1,50 = 165,00
        self.assertEqual(custos['materia_prima'], Decimal('165.00'))

    def test_custo_por_unidade_kg_e_caixa(self):
        receita = self._com_itens()

        custos = ReceitaService.custos(receita)

        # 600,00 por 1.000 unidades
        self.assertEqual(custos['por_unidade'], Decimal('0.6000'))
        # 1.000 un × 0,100 kg = 100 kg → 600 / 100
        self.assertEqual(custos['por_kg'], Decimal('6.0000'))
        # 0,60 × 50 por caixa
        self.assertEqual(custos['por_caixa'], Decimal('30.00'))

    def test_sem_peso_no_produto_o_custo_por_kg_e_nulo(self):
        """
        Zero seria lido como "de graça" — e é assim que um produto entra na
        tabela de preço abaixo do custo.
        """
        self.acabado.peso_liquido = None
        self.acabado.save(update_fields=['peso_liquido'])
        receita = self._com_itens()

        custos = ReceitaService.custos(receita)

        self.assertIsNone(custos['por_kg'])
        self.assertIsNotNone(custos['por_unidade'])


class VersaoTests(ReceitaBase):
    """Versão é registro, não edição."""

    def test_nova_versao_copia_itens_e_etapas(self):
        from apps.polpa.models import EtapaReceita

        receita = self._com_itens()
        EtapaReceita.objects.create(
            receita=receita, ordem=1, nome='Despolpa',
            equipamento='Despolpadeira', perda_percentual=Decimal('25'),
        )

        nova = ReceitaService.nova_versao(receita)

        self.assertEqual(nova.versao, '1.1')
        self.assertEqual(nova.ficha.itens.count(), 3)
        self.assertEqual(nova.etapas.count(), 1)
        self.assertEqual(nova.rendimento_esperado, receita.rendimento_esperado)
        self.assertEqual(nova.ficha.status, FichaTecnica.Status.RASCUNHO)

    def test_a_copia_nao_mexe_na_original(self):
        receita = self._com_itens()

        nova = ReceitaService.nova_versao(receita)
        nova.ficha.itens.first().delete()

        receita.refresh_from_db()
        self.assertEqual(receita.ficha.itens.count(), 3)

    def test_versao_repetida_e_recusada(self):
        receita = self._com_itens()

        with self.assertRaises(DomainError):
            ReceitaService.nova_versao(receita, versao='1.0')

    def test_ativar_desativa_a_versao_anterior(self):
        """
        Duas ativas fariam a ordem de produção escolher — e escolher aqui
        significa produzir por uma fórmula que ninguém decidiu.
        """
        from apps.polpa.models import EtapaReceita

        receita = self._com_itens()
        EtapaReceita.objects.create(receita=receita, ordem=1, nome='Despolpa')
        ReceitaService.ativar(receita)
        nova = ReceitaService.nova_versao(receita)

        ReceitaService.ativar(nova)

        receita.ficha.refresh_from_db()
        self.assertEqual(receita.ficha.status, FichaTecnica.Status.INATIVA)
        self.assertEqual(nova.ficha.status, FichaTecnica.Status.ATIVA)

    def test_receita_incompleta_nao_ativa(self):
        receita = self._receita()

        with self.assertRaises(DomainError) as erro:
            ReceitaService.ativar(receita)

        self.assertIn('ingrediente', str(erro.exception).lower())


class RendimentoTests(ReceitaBase):
    """O esperado, o real e o desvio."""

    def _ordem(self, entrada, saida):
        return OrdemProducao.objects.create(
            filial=self.filial, numero=f'OP{entrada}', ficha_tecnica=self.receita.ficha,
            produto_acabado=self.acabado, quantidade_planejada=Decimal('1000'),
            quantidade_produzida=Decimal('1000'),
            status=OrdemProducao.Status.ENCERRADA,
            peso_entrada_mp=entrada, peso_saida_produzido=saida,
            usuario_abertura=self.usuario,
        )

    def test_sem_ordem_encerrada_o_real_e_nulo(self):
        """
        Zero seria lido como rendimento péssimo, e receita nova nasceria
        parecendo problema.
        """
        self.receita = self._com_itens()

        real = ReceitaService.rendimento_real(self.receita)

        self.assertIsNone(real['percentual'])
        self.assertEqual(real['ordens'], 0)

    def test_o_real_sai_do_peso_que_entrou_e_saiu(self):
        self.receita = self._com_itens()
        self._ordem(Decimal('1000'), Decimal('580'))
        self._ordem(Decimal('1000'), Decimal('600'))

        real = ReceitaService.rendimento_real(self.receita)

        self.assertEqual(real['ordens'], 2)
        self.assertEqual(real['percentual'], Decimal('59.00'))
        self.assertEqual(real['perda'], Decimal('41.00'))

    def test_o_desvio_compara_real_com_esperado(self):
        """
        Dois pontos abaixo numa fábrica de 40 t/dia são 800 kg de polpa que
        alguém pagou e não vendeu.
        """
        self.receita = self._com_itens()
        self._ordem(Decimal('1000'), Decimal('580'))

        desvio = ReceitaService.desvio_de_rendimento(self.receita)

        self.assertEqual(desvio, Decimal('-2.00'))

    def test_sem_esperado_nao_ha_desvio(self):
        self.receita = self._receita(rendimento_esperado=None)
        self._ordem(Decimal('1000'), Decimal('580'))

        self.assertIsNone(ReceitaService.desvio_de_rendimento(self.receita))

    def test_as_perdas_das_etapas_se_compoem(self):
        """
        Perder 25% na despolpa e 5% no envase não dá 70% de rendimento: dá
        71,25%. As perdas se aplicam em cascata, não se somam.
        """
        from apps.polpa.models import EtapaReceita

        receita = self._com_itens()
        EtapaReceita.objects.create(
            receita=receita, ordem=1, nome='Despolpa', perda_percentual=Decimal('25'),
        )
        EtapaReceita.objects.create(
            receita=receita, ordem=2, nome='Envase', perda_percentual=Decimal('5'),
        )

        self.assertEqual(receita.rendimento_por_etapa, Decimal('71.25'))


class TelasReceitaTests(ReceitaBase):
    """As telas da formulação."""

    def test_a_lista_agrupa_por_produto(self):
        self._com_itens()

        resposta = self.client.get(reverse('polpa:receita-list'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Polpa de manga 100 g')
        self.assertContains(resposta, 'Nenhuma versão ativa')

    def test_a_rota_do_menu_nao_cai_no_placeholder(self):
        from django.urls import resolve

        from apps.polpa.views import ItemView

        achado = resolve(reverse('polpa:item', args=['formulacao', 'receitas']))

        self.assertIsNot(getattr(achado.func, 'view_class', None), ItemView)

    def test_o_detalhe_mostra_custo_e_participacao(self):
        receita = self._com_itens()

        resposta = self.client.get(reverse('polpa:receita-detail', args=[receita.pk]))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Custo da batida')
        # A vírgula é do `LANGUAGE_CODE = 'pt-br'`: o Django localiza o
        # decimal na renderização, e o número na tela é "80,00".
        self.assertContains(resposta, '80,00%')
        self.assertContains(resposta, 'Materiais de embalagem')

    def test_lancar_ingrediente_pela_tela(self):
        receita = self._receita()

        self.client.post(reverse('polpa:receita-item-add', args=[receita.pk]), {
            'materia_prima': self.manga.pk, 'quantidade': '100',
            'perda_prevista': '0', 'observacao': '',
        })

        self.assertEqual(receita.ficha.itens.count(), 1)

    def test_o_mesmo_insumo_duas_vezes_e_recusado(self):
        """
        Duas linhas do mesmo ingrediente dariam duas participações para ele,
        e o percentual do rótulo sairia errado.
        """
        receita = self._com_itens()

        self.client.post(reverse('polpa:receita-item-add', args=[receita.pk]), {
            'materia_prima': self.manga.pk, 'quantidade': '50',
            'perda_prevista': '0', 'observacao': '',
        })

        self.assertEqual(
            receita.ficha.itens.filter(materia_prima=self.manga).count(), 1,
        )

    def test_o_produto_acabado_nao_entra_como_insumo(self):
        """Receita que consome o próprio produto é um laço na necessidade."""
        from apps.polpa.forms_receita import ItemReceitaForm

        receita = self._receita()
        form = ItemReceitaForm(filial=self.filial, ficha=receita.ficha)

        oferecidos = list(form.fields['materia_prima'].queryset)

        self.assertNotIn(self.acabado, oferecidos)
        self.assertIn(self.manga, oferecidos)

    def test_lancar_etapa_pela_tela(self):
        receita = self._receita()

        self.client.post(reverse('polpa:receita-etapa-add', args=[receita.pk]), {
            'ordem': '1', 'nome': 'Pasteurizacao', 'equipamento': 'Pasteurizador',
            'tempo_minutos': '2', 'temperatura_min': '90', 'temperatura_max': '92',
            'perda_percentual': '2', 'instrucao': '',
        })

        etapa = receita.etapas.first()
        self.assertIsNotNone(etapa)
        self.assertEqual(etapa.faixa_temperatura, '90.00°C a 92.00°C')

    def test_temperatura_invertida_e_recusada(self):
        receita = self._receita()

        self.client.post(reverse('polpa:receita-etapa-add', args=[receita.pk]), {
            'ordem': '1', 'nome': 'Errada',
            'temperatura_min': '90', 'temperatura_max': '10',
            'perda_percentual': '0',
        })

        self.assertEqual(receita.etapas.count(), 0)

    def test_criar_receita_pela_tela(self):
        resposta = self.client.post(reverse('polpa:receita-create'), {
            'produto': self.acabado.pk, 'descricao': 'Polpa de manga 100 g',
            'versao': '1.0', 'quantidade_produzida': '1000',
            'rendimento_esperado': '60', 'tempo_producao_minutos': '120',
            'custo_mao_obra_padrao': '100', 'custo_indireto_padrao': '50',
        })

        self.assertEqual(resposta.status_code, 302)
        receita = Receita.objects.for_filial(self.filial).first()
        self.assertIsNotNone(receita)
        self.assertEqual(receita.rendimento_esperado, Decimal('60'))

    def test_rendimento_absurdo_e_recusado(self):
        """60 virou 600 numa tecla presa — e o custo por kg sairia dez vezes menor."""
        resposta = self.client.post(reverse('polpa:receita-create'), {
            'produto': self.acabado.pk, 'descricao': 'Polpa errada',
            'versao': '9.9', 'quantidade_produzida': '1000',
            'rendimento_esperado': '600',
        })

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'acima de 300%')


class ATelaDaReceitaTests(ReceitaBase):
    """A tela do cabeçalho da receita."""

    def test_a_tela_abre_e_agrupa_os_campos(self):
        """
        Onze campos numa coluna so' deixavam "Temperatura minima" a seis
        rolagens de "Rende por batida", sem nada dizer que uma depende da outra.
        """
        resposta = self.client.get(reverse('polpa:receita-create'))

        self.assertEqual(resposta.status_code, 200)
        for titulo in (
            'Que produto é este', 'O que sai de uma batida',
            'Condição do processo', 'Custo fixo da batida',
        ):
            self.assertContains(resposta, titulo)

    def test_o_select_vazio_diz_o_que_fazer(self):
        resposta = self.client.get(reverse('polpa:receita-create'))

        self.assertContains(resposta, 'Selecione o produto acabado')
        self.assertNotContains(resposta, '---------')

    def test_os_campos_obrigatorios_estao_marcados(self):
        resposta = self.client.get(reverse('polpa:receita-create'))

        self.assertContains(resposta, 'title="Obrigatório"')


class NenhumaTelaDePolpaVazaSintaxeDeTemplateTests(ReceitaBase):
    """
    Guarda contra o defeito que eu mesmo enviei em 26/08/2026.

    Escrevi um comentário `{# ... #}` de VÁRIAS LINHAS dentro de um `<label>`.
    Django só trata `{# #}` como comentário quando ele cabe numa linha; em
    várias, o texto vaza e é RENDERIZADO NA TELA — apareceu no meio do rótulo
    do campo Unidade, em produção.

    Os testes que eu tinha não pegaram porque só perguntavam "o botão está
    lá?" (`assertContains`), e isso passa com a tela inteira suja em volta.
    Este pergunta o contrário: sobrou alguma sintaxe de template no HTML?
    """

    TELAS = (
        'polpa:receita-create',
        'polpa:catalogo-create',
        'polpa:recebimento-create',
        'polpa:recebimento-list',
        'polpa:recebimento-classificacao',
        'polpa:recebimento-produtores',
        'polpa:catalogo-list',
        'polpa:receita-list',
    )

    def test_nenhuma_tela_renderiza_sintaxe_de_template(self):
        for nome in self.TELAS:
            with self.subTest(tela=nome):
                html = self.client.get(reverse(nome)).content.decode()
                # `{%` e `{#` nunca deveriam sobreviver à renderização. O Alpine
                # usa `{{ }}` em alguns lugares, então esse par fica de fora.
                for resto in ('{#', '#}', '{%', '%}'):
                    self.assertNotIn(
                        resto, html,
                        f'{nome} vazou "{resto}" no HTML renderizado',
                    )
