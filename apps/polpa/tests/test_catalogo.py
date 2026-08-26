"""
O cadastro de produtos da fábrica: matéria-prima, embalagem e acabado.

O QUE ESTES TESTES CERCAM:

  · NÃO EXISTE SEGUNDO CATÁLOGO. Cadastrar pelo vertical grava o
    `produtos.Produto` que o estoque, a nota e o PDV leem — e a ficha ao
    lado dele. Se um dia isto virar tabela própria, a nota sai com o NCM de
    um cadastro e o estoque com o peso do outro;

  · A CLASSE DECIDE O COMPORTAMENTO. Acabado nasce controlando lote,
    validade e FEFO: em alimento congelado isso não é preferência de quem
    cadastra, e deixar como caixinha para marcar transformaria em
    esquecimento o que o processo não admite esquecer;

  · A VALIDADE É EM DIAS, e a data do lote sai dela. Enquanto o prazo do
    PRODUTO não existia, a data era digitada à mão a cada produção — que é
    como sai lote com prazo errado para a rua;

  · O QUE FALTA APARECE, e não impede. Item incompleto é útil (dá para
    comprar com ele); o que não pode é a falta passar em silêncio.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import DomainError
from apps.polpa.models import FichaProduto, Fruta, TipoItem
from apps.polpa.services import CatalogoService
from apps.produtos.models import Produto, UnidadeMedida, UnidadeMedidaFilial

C = FichaProduto.Classe
T = FichaProduto.Tipo


class CatalogoBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Polpas Catalogo LTDA', nome_fantasia='Catalogo',
            cnpj='83345678000191', segmento='polpa_frutas',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='83345678000272',
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
            email='chefe@catalogo.local', nome='Chefe', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )

    def setUp(self):
        self.client.force_login(self.usuario)

    def _dados(self, **campos):
        padrao = {
            'tipo': T.POLPA, 'classe': C.ACABADO,
            'descricao': 'Polpa de manga 100 g', 'codigo': 'PM100',
            'unidade_medida': self.unidade, 'sabor': 'Manga',
            'validade_dias': 365, 'peso_liquido': Decimal('0.100'),
            'peso_bruto': Decimal('0.110'), 'ncm': '20089900',
            'codigo_barras': '7891234567890',
            'quantidade_por_embalagem': Decimal('50'),
            'congelado': True, 'temperatura_maxima': Decimal('-18'),
        }
        padrao.update(campos)
        return padrao


class GravacaoTests(CatalogoBase):
    """Produto do ERP e ficha da fábrica, num ato só."""

    def test_cadastrar_cria_produto_e_ficha(self):
        ficha = CatalogoService.salvar(self.filial, self._dados())

        self.assertIsInstance(ficha, FichaProduto)
        self.assertEqual(Produto.objects.count(), 1)
        self.assertEqual(ficha.produto.descricao, 'Polpa de manga 100 g')
        self.assertEqual(ficha.produto.ncm, '20089900')
        self.assertEqual(ficha.sabor, 'Manga')

    def test_o_produto_fica_visivel_para_a_filial(self):
        """
        Sem o vínculo `ProdutoFilial`, o item some do próprio catálogo que
        acabou de cadastrá-lo — `Produto.objects.for_filial` lê por ele.
        """
        ficha = CatalogoService.salvar(self.filial, self._dados())

        visiveis = Produto.objects.for_filial(self.filial)

        self.assertIn(ficha.produto, visiveis)

    def test_acabado_nasce_com_lote_validade_e_fefo(self):
        """
        Em alimento congelado isso não é escolha de quem cadastra. Como
        caixinha para marcar, viraria esquecimento.
        """
        ficha = CatalogoService.salvar(self.filial, self._dados())

        produto = ficha.produto
        self.assertTrue(produto.controla_lote)
        self.assertTrue(produto.controla_validade)
        self.assertTrue(produto.saida_fefo)
        self.assertEqual(produto.metodo_saida, Produto.MetodoSaida.FEFO)

    def test_materia_prima_tambem_controla_lote(self):
        """É da matéria-prima que o recall parte."""
        ficha = CatalogoService.salvar(self.filial, self._dados(
            tipo=T.ACUCAR, classe=C.MATERIA_PRIMA, descricao='Acucar cristal',
            validade_dias=None, congelado=False,
        ))

        self.assertTrue(ficha.produto.controla_lote)

    def test_congelado_marca_a_condicao_de_armazenamento(self):
        """É dessa marca que a cadeia de frio depende para cobrar temperatura."""
        ficha = CatalogoService.salvar(self.filial, self._dados())

        self.assertEqual(
            ficha.produto.condicao_armazenamento,
            Produto.CondicaoArmazenamento.CONGELADO,
        )
        self.assertTrue(ficha.congelado)

    def test_editar_nao_cria_produto_novo(self):
        ficha = CatalogoService.salvar(self.filial, self._dados())

        CatalogoService.salvar(
            self.filial, self._dados(descricao='Polpa de manga 200 g'), ficha,
        )

        self.assertEqual(Produto.objects.count(), 1)
        ficha.refresh_from_db()
        self.assertEqual(ficha.produto.descricao, 'Polpa de manga 200 g')

    def test_sem_unidade_a_recusa_diz_onde_resolver(self):
        """
        Uma trava que só diz "não pode" transfere para quem está com pressa
        o trabalho de descobrir o porquê.
        """
        with self.assertRaises(DomainError) as erro:
            CatalogoService.salvar(self.filial, self._dados(unidade_medida=None))

        self.assertIn('unidade', str(erro.exception).lower())

    def test_sem_descricao_nao_grava(self):
        with self.assertRaises(DomainError):
            CatalogoService.salvar(self.filial, self._dados(descricao='   '))

    def test_a_classe_sai_do_tipo_quando_nao_vem(self):
        """
        Classe e tipo são a mesma informação em dois níveis. Soltas, abrem a
        porta para um "pote" cadastrado como matéria-prima.
        """
        ficha = CatalogoService.salvar(self.filial, self._dados(
            tipo=T.POTE, classe=None, descricao='Pote 500 ml',
            validade_dias=None, congelado=False,
        ))

        self.assertEqual(ficha.classe, C.EMBALAGEM)

    def test_tipo_de_outra_classe_e_recusado(self):
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            CatalogoService.salvar(self.filial, self._dados(
                tipo=T.POTE, classe=C.ACABADO, descricao='Pote errado',
            ))


class ValidadeTests(CatalogoBase):
    """O prazo do produto e a data do lote."""

    def test_a_validade_do_lote_sai_do_prazo_do_produto(self):
        ficha = CatalogoService.salvar(self.filial, self._dados(validade_dias=180))

        vencimento = ficha.validade_a_partir_de(date(2026, 1, 1))

        self.assertEqual(vencimento, date(2026, 6, 30))

    def test_produto_sem_prazo_nao_inventa_vencimento(self):
        """
        Devolver uma data qualquer seria pior que não ter: o lote sairia com
        vencimento inventado, e ninguém saberia que foi inventado.
        """
        ficha = CatalogoService.salvar(self.filial, self._dados(validade_dias=None))

        self.assertIsNone(ficha.validade_a_partir_de(date(2026, 1, 1)))

    def test_acabado_sem_validade_aparece_como_pendencia(self):
        ficha = CatalogoService.salvar(self.filial, self._dados(validade_dias=None))

        pendencias = ficha.pendencias()

        self.assertTrue(any('validade' in p.lower() for p in pendencias))

    def test_acabado_completo_nao_tem_pendencia(self):
        ficha = CatalogoService.salvar(self.filial, self._dados())

        self.assertEqual(ficha.pendencias(), [])

    def test_embalagem_sem_quantidade_por_caixa_nao_fecha_pallet(self):
        ficha = CatalogoService.salvar(self.filial, self._dados(
            tipo=T.POTE, classe=C.EMBALAGEM, descricao='Pote 500 ml',
            validade_dias=None, congelado=False, quantidade_por_embalagem=0,
        ))

        self.assertTrue(any('paletiza' in p.lower() for p in ficha.pendencias()))

    def test_congelado_sem_temperatura_maxima_e_apontado(self):
        """Sem a faixa não há desvio a apurar — a cadeia de frio fica cega."""
        ficha = CatalogoService.salvar(self.filial, self._dados(
            temperatura_maxima=None,
        ))

        self.assertTrue(any('temperatura' in p.lower() for p in ficha.pendencias()))


class ResumoTests(CatalogoBase):
    """O número que importa é o de incompletos."""

    def test_o_resumo_separa_por_classe_e_conta_o_que_falta(self):
        CatalogoService.salvar(self.filial, self._dados())
        CatalogoService.salvar(self.filial, self._dados(
            descricao='Polpa de acerola 100 g', codigo='PA100',
            codigo_barras='', validade_dias=None,
        ))
        CatalogoService.salvar(self.filial, self._dados(
            tipo=T.ACUCAR, classe=C.MATERIA_PRIMA, descricao='Acucar',
            codigo='AC', validade_dias=None, congelado=False,
        ))

        resumo = CatalogoService.resumo(self.filial)

        self.assertEqual(resumo['total'], 3)
        self.assertEqual(resumo['por_classe'][C.ACABADO]['total'], 2)
        self.assertEqual(resumo['por_classe'][C.ACABADO]['pendentes'], 1)
        self.assertEqual(resumo['pendentes'], 1)

    def test_produto_do_erp_sem_ficha_aparece_na_contagem(self):
        """
        Eles existem: entram por XML de compra ou vêm de antes do vertical.
        Escondê-los faria concluir que o produto sumiu.
        """
        from apps.produtos.models import ProdutoFilial

        avulso = Produto.objects.create(
            filial=self.filial, descricao='Caixa de papelao', ncm='48191000',
            unidade_medida=self.unidade,
        )
        # O vínculo é o que faz o produto existir para a filial — é assim
        # que ele chega por XML de compra, e é por isso que ele aparece na
        # lista de "sem ficha" em vez de simplesmente não existir.
        ProdutoFilial.objects.create(produto=avulso, filial=self.filial)
        CatalogoService.salvar(self.filial, self._dados())

        self.assertEqual(CatalogoService.sem_ficha(self.filial).count(), 1)


class TelasCatalogoTests(CatalogoBase):
    """As telas do catálogo."""

    def test_a_lista_abre_e_mostra_o_item(self):
        CatalogoService.salvar(self.filial, self._dados())

        resposta = self.client.get(reverse('polpa:catalogo-list'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Polpa de manga 100 g')

    def test_cada_item_do_menu_abre_a_lista_na_classe_certa(self):
        """
        Três itens de menu, uma tela — mas cada um precisa cair na sua aba,
        senão "Embalagens" abre nos acabados e parece que o cadastro sumiu.
        """
        alvos = {
            'polpa:produto-acabado-list': C.ACABADO,
            'polpa:embalagem-list': C.EMBALAGEM,
            'polpa:materia-prima-list': C.MATERIA_PRIMA,
        }
        for rota, classe in alvos.items():
            with self.subTest(rota=rota):
                resposta = self.client.get(reverse(rota))
                self.assertEqual(resposta.status_code, 200)
                self.assertEqual(resposta.context['classe'], classe)

    def test_as_rotas_do_menu_nao_caem_no_placeholder(self):
        from django.urls import resolve

        from apps.polpa.views import ItemView

        for grupo, item in (
            ('formulacao', 'produtos'),
            ('formulacao', 'embalagens'),
            ('formulacao', 'materias-primas'),
        ):
            with self.subTest(item=item):
                achado = resolve(reverse('polpa:item', args=[grupo, item]))
                self.assertIsNot(
                    getattr(achado.func, 'view_class', None), ItemView,
                )

    def test_cadastrar_pela_tela_grava_os_dois(self):
        resposta = self.client.post(reverse('polpa:catalogo-create'), {
            'tipo': T.POLPA, 'descricao': 'Polpa de acerola 100 g',
            'codigo': 'PAC100', 'unidade_medida': self.unidade.pk,
            'sabor': 'Acerola', 'validade_dias': '365',
            'ncm': '20089900', 'codigo_barras': '7890000000017',
            'peso_liquido': '0.100', 'peso_bruto': '0.110',
            'quantidade_por_embalagem': '50',
            'congelado': 'on', 'temperatura_maxima': '-18',
        })

        self.assertEqual(resposta.status_code, 302)
        ficha = FichaProduto.objects.for_filial(self.filial).first()
        self.assertIsNotNone(ficha)
        self.assertEqual(ficha.classe, C.ACABADO)
        self.assertTrue(ficha.produto.controla_lote)

    def test_peso_liquido_maior_que_o_bruto_e_recusado(self):
        """O bruto inclui a embalagem — invertido, a expedição calcula a menos."""
        resposta = self.client.post(reverse('polpa:catalogo-create'), {
            'tipo': T.POLPA, 'descricao': 'Polpa errada',
            'unidade_medida': self.unidade.pk,
            'peso_liquido': '2', 'peso_bruto': '1',
        })

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'menor que o líquido')
        self.assertEqual(FichaProduto.objects.count(), 0)

    def test_congelado_e_refrigerado_juntos_nao_passam(self):
        resposta = self.client.post(reverse('polpa:catalogo-create'), {
            'tipo': T.POLPA, 'descricao': 'Polpa confusa',
            'unidade_medida': self.unidade.pk,
            'congelado': 'on', 'refrigerado': 'on',
        })

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'congelado ou refrigerado')

    def test_editar_traz_o_que_ja_estava_gravado(self):
        """
        Campo esquecido no preenchimento do formulário some da tela — e
        volta vazio para o banco na primeira gravação.
        """
        ficha = CatalogoService.salvar(self.filial, self._dados())

        resposta = self.client.get(
            reverse('polpa:catalogo-update', args=[ficha.pk])
        )

        self.assertEqual(resposta.status_code, 200)
        inicial = resposta.context['form'].initial
        self.assertEqual(inicial['descricao'], 'Polpa de manga 100 g')
        self.assertEqual(inicial['validade_dias'], 365)
        self.assertEqual(inicial['sabor'], 'Manga')
        self.assertTrue(inicial['congelado'])
        self.assertEqual(inicial['ncm'], '20089900')

    def test_a_fruta_do_recebimento_pode_ser_ligada_ao_item(self):
        """
        É o elo que faz a polpa de manga saber de qual fruta ela vem — e o
        que evita um segundo cadastro de fruta dentro do catálogo.
        """
        fruta = Fruta.objects.create(
            filial=self.filial, nome='Manga', variedade='Tommy',
        )

        ficha = CatalogoService.salvar(self.filial, self._dados(fruta=fruta))

        self.assertEqual(ficha.fruta, fruta)
        self.assertIn(ficha, fruta.produtos.all())


class ATelaGuiaOPreenchimentoTests(CatalogoBase):
    """
    A ficha muda conforme a classe — e a tela tem que mudar junto.

    O comentário no topo de `catalogo_form.html` já prometia isto; a tela não
    cumpria. Ela pedia paletização de aroma e validade de pallet, e é assim que
    alguém digita zero só para o formulário fechar. Zero digitado é pior que
    campo vazio, porque parece resposta.
    """

    def test_o_rotulo_do_tipo_nao_repete_o_titulo_do_cartao(self):
        """
        "O que é este item" aparecia duas vezes coladas -- como titulo do
        cartao e como rotulo do campo logo abaixo.
        """
        html = self.client.get(reverse('polpa:catalogo-create')).content.decode()

        self.assertEqual(html.count('O que é este item'), 1)

    def test_o_select_de_unidade_diz_o_que_fazer(self):
        html = self.client.get(reverse('polpa:catalogo-create')).content.decode()

        self.assertIn('Selecione a unidade', html)
        self.assertNotIn('---------', html)

    def test_medidas_e_embalagem_so_para_acabado_e_embalagem(self):
        """Fruta a granel nao tem caixa por pallet."""
        html = self.client.get(reverse('polpa:catalogo-create')).content.decode()

        self.assertIn(
            "x-show=\"classe === 'acabado' || classe === 'embalagem'\"", html,
        )

    def test_conservacao_so_para_o_que_precisa_de_frio(self):
        """Pote e rotulo nao tem cadeia de frio; fruta e polpa tem."""
        html = self.client.get(reverse('polpa:catalogo-create')).content.decode()

        self.assertIn(
            "x-show=\"classe === 'acabado' || classe === 'materia_prima'\"", html,
        )

    def test_esconder_bloco_nao_impede_gravar_a_ficha_inteira(self):
        """
        O CAMPO ESCONDIDO CONTINUA NO DOM e e' enviado. Este teste e' o que
        garante que a mudanca visual nao virou perda de dado: um acabado com
        todos os campos preenchidos tem que gravar igual a antes.
        """
        resposta = self.client.post(reverse('polpa:catalogo-create'), {
            'tipo': T.POLPA, 'descricao': 'Polpa de goiaba 100 g',
            'codigo': 'PGO100', 'unidade_medida': self.unidade.pk,
            'validade_dias': '365', 'peso_liquido': '0.100',
            'peso_bruto': '0.110', 'quantidade_por_embalagem': '50',
            'caixas_por_pallet': '40',
            'congelado': 'on', 'temperatura_maxima': '-18',
        })

        ficha = FichaProduto.objects.get(produto__descricao='Polpa de goiaba 100 g')
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(ficha.caixas_por_pallet, 40)
        self.assertEqual(ficha.validade_dias, 365)

    def test_os_campos_obrigatorios_estao_marcados(self):
        """
        Tres dos vinte campos sao obrigatorios. Sem marca, descobre-se quais no
        botao -- depois de preencher os outros dezessete.
        """
        html = self.client.get(reverse('polpa:catalogo-create')).content.decode()

        self.assertIn('title="Obrigatorio"', html)


class UnidadeRelampagoTests(CatalogoBase):
    """
    Cadastrar a unidade sem sair da ficha.

    A tela já avisava que sem unidade o item não salva e dizia onde resolver.
    Mas mandar embora no meio do formulário é perder o que já foi digitado —
    volta-se para uma ficha em branco, e a segunda tentativa é a que não
    acontece.
    """

    def _criar(self, **dados):
        return self.client.post(reverse('polpa:unidade-ajax-create'), dados)

    def test_cria_a_unidade_e_devolve_a_opcao_pronta(self):
        import json

        resposta = self._criar(sigla='CX', descricao='Caixa')

        corpo = json.loads(resposta.content)
        unidade = UnidadeMedida.objects.get(sigla='CX')
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(corpo['id'], unidade.pk)
        self.assertIn('CX', corpo['label'])

    def test_a_unidade_nasce_VISIVEL_nesta_filial(self):
        """
        A unidade e' da EMPRESA, mas a ficha lista por `for_filial`, que passa
        pelo vinculo. Sem criar o vinculo ela nasceria invisivel: o cadastro
        grava, o select continua vazio, e quem clicou conclui que o botao esta'
        quebrado.
        """
        self._criar(sigla='CX', descricao='Caixa')

        visiveis = UnidadeMedida.objects.for_filial(self.filial)
        self.assertTrue(visiveis.filter(sigla='CX').exists())

    def test_a_sigla_vai_para_maiuscula(self):
        """"kg" e "KG" convivendo e' o estoque somando em duas unidades iguais."""
        self._criar(sigla='kg', descricao='Quilograma')

        self.assertTrue(UnidadeMedida.objects.filter(sigla='KG').exists())

    def test_sigla_repetida_e_recusada_com_recado(self):
        """
        `unique_together` ja' barra no banco, mas o erro cru de integridade nao
        diz o que fazer.
        """
        self._criar(sigla='CX', descricao='Caixa')
        resposta = self._criar(sigla='cx', descricao='Caixa de papelao')

        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(UnidadeMedida.objects.filter(sigla__iexact='CX').count(), 1)

    def test_sem_sigla_nao_grava(self):
        antes = UnidadeMedida.objects.count()

        resposta = self._criar(sigla='   ', descricao='Alguma coisa')

        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(UnidadeMedida.objects.count(), antes)

    def test_a_ficha_oferece_o_botao_no_proprio_campo(self):
        """
        A saida do beco fica NO CAMPO, e nao so' no aviso do topo: e' onde a
        pessoa esta' olhando quando descobre que falta.
        """
        resposta = self.client.get(reverse('polpa:catalogo-create'))

        self.assertContains(resposta, 'Nova unidade de medida')
        self.assertContains(resposta, reverse('polpa:unidade-ajax-create'))

    def test_a_unidade_criada_pode_ser_usada_na_ficha(self):
        """A volta do favor: criada, ela precisa gravar o item."""
        import json

        criada = json.loads(self._criar(sigla='CX', descricao='Caixa').content)

        resposta = self.client.post(reverse('polpa:catalogo-create'), {
            'tipo': T.CAIXA, 'descricao': 'Caixa de papelao 5 kg',
            'unidade_medida': criada['id'],
        })

        self.assertEqual(resposta.status_code, 302)
        self.assertTrue(
            FichaProduto.objects.filter(
                produto__descricao='Caixa de papelao 5 kg',
            ).exists()
        )


class TipoItemVirouCadastroTests(CatalogoBase):
    """
    O tipo saiu do enum e virou tabela.

    Os 35 cobriam polpa, açaí e sorvete — mas cada fábrica tem o seu: xarope,
    cobertura, insumo de limpeza, pote de vidro retornável. Enquanto a lista
    vivia no código, acrescentar um exigia deploy, e o que acontece na prática é
    cadastrar tudo como "Outro ingrediente", que é onde a informação morre.
    """

    def _criar(self, **dados):
        return self.client.post(reverse('polpa:tipo-ajax-create'), dados)

    # ── A semeadura ──────────────────────────────────────────────────────

    def test_a_filial_nasce_com_os_tipos_de_sistema(self):
        """
        Semeados NA LEITURA e nao por migracao de dados: migracao cuidaria das
        filiais de hoje e deixaria a de amanha com a lista vazia -- e lista
        vazia aqui e' a tela do catalogo sem opcao nenhuma.
        """
        self.assertEqual(TipoItem.objects.filter(filial=self.filial).count(), 0)

        tipos = TipoItem.da_filial(self.filial)

        self.assertEqual(tipos.count(), len(FichaProduto.CLASSE_DO_TIPO))
        self.assertTrue(all(t.sistema for t in tipos))

    def test_semear_duas_vezes_nao_duplica(self):
        """Duas abas abrindo a tela ao mesmo tempo e' o caso normal."""
        TipoItem.da_filial(self.filial)
        antes = TipoItem.objects.filter(filial=self.filial).count()

        TipoItem.garantir_padroes(self.filial)

        self.assertEqual(TipoItem.objects.filter(filial=self.filial).count(), antes)

    def test_cada_tipo_semeado_leva_a_classe_certa(self):
        TipoItem.da_filial(self.filial)

        pote = TipoItem.objects.get(filial=self.filial, codigo=T.POTE)
        polpa = TipoItem.objects.get(filial=self.filial, codigo=T.POLPA)

        self.assertEqual(pote.classe, FichaProduto.Classe.EMBALAGEM)
        self.assertEqual(polpa.classe, FichaProduto.Classe.ACABADO)

    # ── O tipo criado pela fábrica ───────────────────────────────────────

    def test_cria_o_tipo_e_devolve_a_opcao_pronta(self):
        import json

        resposta = self._criar(nome='Xarope de guaraná',
                               classe=FichaProduto.Classe.MATERIA_PRIMA)

        corpo = json.loads(resposta.content)
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(corpo['label'], 'Xarope de guaraná')
        self.assertEqual(corpo['classe'], FichaProduto.Classe.MATERIA_PRIMA)

    def test_o_codigo_sai_do_nome(self):
        """
        Identificador tecnico nao se pede: deixar alguem digitar produziria
        "Pote Vidro", "pote-vidro" e "POTE_VIDRO" como tres tipos.
        """
        self._criar(nome='Pote de vidro', classe=FichaProduto.Classe.EMBALAGEM)

        tipo = TipoItem.objects.get(filial=self.filial, nome='Pote de vidro')
        self.assertEqual(tipo.codigo, 'pote_de_vidro')

    def test_nome_repetido_e_recusado(self):
        self._criar(nome='Xarope', classe=FichaProduto.Classe.MATERIA_PRIMA)
        resposta = self._criar(nome='xarope', classe=FichaProduto.Classe.ACABADO)

        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(
            TipoItem.objects.filter(filial=self.filial, nome__iexact='xarope').count(), 1,
        )

    def test_sem_classe_nao_grava(self):
        """
        Tipo sem classe seria um item que o sistema nao sabe processar: entraria
        no catalogo e sumiria da receita, do custo e do menu.
        """
        resposta = self._criar(nome='Coisa solta', classe='')

        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(TipoItem.objects.filter(nome='Coisa solta').exists())

    def test_o_tipo_novo_nao_vaza_para_outra_filial(self):
        """
        Tipo e' vocabulario da operacao: a fabrica de sorvete nao precisa
        herdar "pote de vidro retornavel" que a de polpa inventou.
        """
        outra = Filial.objects.create(
            empresa=self.empresa, razao_social='Segunda', cnpj='55545678000299',
            uf='RN', cidade='Mossoro',
        )
        self._criar(nome='Xarope', classe=FichaProduto.Classe.MATERIA_PRIMA)

        self.assertFalse(
            TipoItem.objects.filter(filial=outra, nome='Xarope').exists()
        )

    # ── A ponta que importa: a ficha aceita o tipo novo ──────────────────

    def test_a_ficha_grava_com_tipo_criado_pela_fabrica(self):
        """
        O SERVICO CHAMA `full_clean`. Com o `choices` do enum ainda no campo,
        todo tipo proprio seria recusado como "opcao invalida" -- e o cadastro
        inteiro nao serviria para nada.
        """
        import json

        criado = json.loads(self._criar(
            nome='Xarope de guaraná', classe=FichaProduto.Classe.MATERIA_PRIMA,
        ).content)

        resposta = self.client.post(reverse('polpa:catalogo-create'), {
            'tipo': criado['id'], 'descricao': 'Xarope guaraná 1 L',
            'unidade_medida': self.unidade.pk,
        })

        ficha = FichaProduto.objects.get(produto__descricao='Xarope guaraná 1 L')
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(ficha.tipo, criado['id'])

    def test_a_classe_da_ficha_sai_do_tipo_criado(self):
        """
        `classe` sustenta custo, receita e ordem. Derivada errado, o item entra
        no catalogo e some de todo o resto.
        """
        import json

        criado = json.loads(self._criar(
            nome='Cobertura', classe=FichaProduto.Classe.MATERIA_PRIMA,
        ).content)

        self.client.post(reverse('polpa:catalogo-create'), {
            'tipo': criado['id'], 'descricao': 'Cobertura de chocolate',
            'unidade_medida': self.unidade.pk,
        })

        ficha = FichaProduto.objects.get(produto__descricao='Cobertura de chocolate')
        self.assertEqual(ficha.classe, FichaProduto.Classe.MATERIA_PRIMA)

    def test_o_nome_do_tipo_aparece_na_tela_e_nao_o_codigo(self):
        """
        `get_tipo_display()` morreu junto com o `choices`. Sem o substituto, a
        lista mostraria "polpa_base" no lugar de "Polpa-base".
        """
        self.client.post(reverse('polpa:catalogo-create'), {
            'tipo': T.POLPA_BASE, 'descricao': 'Polpa-base de manga',
            'unidade_medida': self.unidade.pk,
        })

        ficha = FichaProduto.objects.get(produto__descricao='Polpa-base de manga')
        self.assertEqual(ficha.tipo_nome, 'Polpa-base')

    def test_a_ficha_oferece_o_botao_de_tipo_novo(self):
        resposta = self.client.get(reverse('polpa:catalogo-create'))

        self.assertContains(resposta, 'Novo tipo de item')
        self.assertContains(resposta, reverse('polpa:tipo-ajax-create'))
