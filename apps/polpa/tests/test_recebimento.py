"""
O recebimento de fruta — a balança, a régua e o lote que nasce dali.

O QUE ESTES TESTES CERCAM:

  · O PESO É UMA CONTA, e ela precisa ser sempre a mesma: bruto − tara −
    desconto. Se o líquido e o aceito se confundirem, a fábrica paga pelo
    que descontou;

  · APROVAR CRIA LOTE DE VERDADE, no `estoque.LoteProduto` que o resto do
    ERP lê. Um lote só do vertical daria dois saldos da mesma fruta e
    nenhum confiável;

  · O QUE FALTA BLOQUEIA A APROVAÇÃO, e não a gravação. Romaneio salva com
    pouco (o caminhão está na balança); o que exige tudo é a decisão;

  · O DESVIO NÃO TRAVA, mas fica escrito. Brix abaixo do mínimo pode ser
    aceito -- o que não pode é a aceitação apagar o desvio;

  · NÃO SE CANCELA CARGA QUE JÁ VIROU LOTE: a fruta pode já estar no
    tanque, e o saldo ficaria sem origem.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.cadastros.models import Fornecedor, FornecedorFilial
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import DomainError
from apps.estoque.models import LoteProduto
from apps.polpa.models import Fruta, Recebimento
from apps.polpa.services import RecebimentoService
from apps.produtos.models import Produto, UnidadeMedida, UnidadeMedidaFilial


class PolpaBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Polpas do Vale LTDA', nome_fantasia='Polpas do Vale',
            cnpj='73345678000191', segmento='polpa_frutas',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='73345678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.produtor = Fornecedor.objects.create(
            filial=cls.filial, razao_social='Sitio Boa Vista',
            cpf_cnpj='12345678901',
        )
        # O VÍNCULO É O QUE FAZ O PRODUTOR EXISTIR PARA A FILIAL --
        # `Fornecedor.objects.for_filial` lê por ele, e sem isto o select do
        # romaneio abre vazio, que é o mesmo sintoma que a fábrica veria.
        FornecedorFilial.objects.create(fornecedor=cls.produtor, filial=cls.filial)
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='chefe@polpa.local', nome='Chefe', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )

    def setUp(self):
        self.client.force_login(self.usuario)

    def _fruta(self, **campos):
        padrao = dict(
            filial=self.filial, nome='Manga', variedade='Tommy',
            brix_minimo=Decimal('11'), impureza_maxima=Decimal('5'),
            rendimento_esperado=Decimal('60'),
        )
        padrao.update(campos)
        return Fruta.objects.create(**padrao)

    def _produto(self):
        # Fruta se compra por QUILO — a unidade não é detalhe de cadastro
        # aqui, é o que faz o peso da balança virar saldo de estoque.
        unidade = UnidadeMedida.objects.create(
            empresa=self.empresa, sigla='KG', descricao='Quilograma',
            tipo=UnidadeMedida.Tipo.PESO,
        )
        UnidadeMedidaFilial.objects.create(unidade=unidade, filial=self.filial)
        return Produto.objects.create(
            filial=self.filial, codigo='MP-MANGA', descricao='Manga in natura',
            unidade_medida=unidade,
        )

    def _carga(self, fruta=None, **campos):
        padrao = dict(
            filial=self.filial, fruta=fruta or self._fruta(), produtor=self.produtor,
            data=date(2026, 8, 25),
            peso_bruto=Decimal('12000'), tara=Decimal('2000'),
            preco_kg=Decimal('1.50'),
        )
        padrao.update(campos)
        return Recebimento.objects.create(**padrao)


class PesagemTests(PolpaBase):
    """A conta do peso, que é a conta do dinheiro."""

    def test_liquido_e_bruto_menos_tara(self):
        carga = self._carga()

        self.assertEqual(carga.peso_liquido, Decimal('10000'))

    def test_aceito_desconta_a_impureza(self):
        carga = self._carga(desconto_kg=Decimal('500'))

        self.assertEqual(carga.peso_liquido, Decimal('10000'))
        self.assertEqual(carga.peso_aceito, Decimal('9500'))
        self.assertEqual(carga.percentual_desconto, Decimal('5.00'))

    def test_o_valor_e_sobre_o_aceito_e_nao_sobre_o_liquido(self):
        """
        Pagar pelo líquido é pagar pela terra que veio junto — o desconto
        existe justamente para isso.
        """
        carga = self._carga(desconto_kg=Decimal('500'))

        self.assertEqual(carga.valor_total, Decimal('14250.00'))

    def test_peso_nunca_fica_negativo(self):
        """
        Tara maior que o bruto é erro de digitação, não carga negativa. O
        formulário barra; o modelo não pode devolver número impossível para
        quem chegou por outro caminho.
        """
        carga = self._carga(peso_bruto=Decimal('1000'), tara=Decimal('3000'))

        self.assertEqual(carga.peso_liquido, Decimal('0'))
        self.assertEqual(carga.peso_aceito, Decimal('0'))

    def test_rendimento_previsto_usa_a_regua_da_fruta(self):
        """40 t de manga não são 40 t de polpa: casca e caroço saem."""
        carga = self._carga()

        self.assertEqual(carga.rendimento_previsto, Decimal('6000.000'))

    def test_fruta_sem_rendimento_nao_inventa_previsao(self):
        """
        Zero seria lido como "não rende nada". Sem régua, a tela diz que
        não há previsão -- e é isso que faz alguém cadastrar.
        """
        carga = self._carga(fruta=self._fruta(rendimento_esperado=None))

        self.assertEqual(carga.rendimento_previsto, Decimal('0'))


class ReguaTests(PolpaBase):
    """O que a fruta exige da carga."""

    def test_brix_abaixo_do_minimo_e_apontado(self):
        carga = self._carga()
        carga.brix = Decimal('9')

        problemas = carga.reprovacoes()

        self.assertEqual(len(problemas), 1)
        self.assertIn('Brix', problemas[0])

    def test_impureza_acima_do_limite_e_apontada(self):
        carga = self._carga()
        carga.impureza = Decimal('8')

        self.assertTrue(any('Impureza' in p for p in carga.reprovacoes()))

    def test_carga_dentro_da_regua_nao_acusa_nada(self):
        carga = self._carga()
        carga.brix = Decimal('13')
        carga.impureza = Decimal('2')

        self.assertEqual(carga.reprovacoes(), [])

    def test_fruta_sem_regua_nao_reprova_ninguem(self):
        """
        Sem régua cadastrada não há como reprovar por medição — e a tela
        diz isso, em vez de deixar parecer que toda carga passou no teste.
        """
        fruta = self._fruta(brix_minimo=None, impureza_maxima=None)
        carga = self._carga(fruta=fruta)
        carga.brix = Decimal('2')

        self.assertFalse(fruta.tem_regua)
        self.assertEqual(carga.reprovacoes(), [])

    def test_safra_que_vira_o_ano(self):
        """
        Novembro a fevereiro é o caso normal em fruta tropical, e um
        `inicio <= mes <= fim` ingênuo diria que dezembro está fora.
        """
        fruta = self._fruta(safra_inicio=11, safra_fim=2)

        self.assertTrue(fruta.na_safra(12))
        self.assertTrue(fruta.na_safra(1))
        self.assertFalse(fruta.na_safra(6))


class AprovacaoTests(PolpaBase):
    """A decisão e o lote que nasce dela."""

    def _pronta(self):
        fruta = self._fruta()
        fruta.produto = self._produto()
        fruta.save()
        carga = self._carga(fruta=fruta, desconto_kg=Decimal('500'))
        RecebimentoService.classificar(
            carga, {'brix': Decimal('13'), 'impureza': Decimal('2')}, self.usuario,
        )
        return carga

    def test_aprovar_cria_lote_no_estoque(self):
        carga = self._pronta()

        lote = RecebimentoService.aprovar(carga, self.usuario)

        self.assertIsInstance(lote, LoteProduto)
        self.assertEqual(lote.quantidade_inicial, Decimal('9500'))
        self.assertEqual(lote.quantidade_atual, Decimal('9500'))
        self.assertEqual(lote.custo_unitario, Decimal('1.5000'))
        self.assertEqual(lote.fornecedor, self.produtor)
        carga.refresh_from_db()
        self.assertEqual(carga.lote_id, lote.pk)
        self.assertEqual(carga.status, Recebimento.Status.APROVADO)

    def test_o_numero_do_lote_conta_de_onde_veio(self):
        """
        Num recall a primeira pergunta é "de que carga saiu isto?" — e um
        sequencial anônimo obriga a consultar o sistema para responder.
        """
        carga = self._pronta()

        lote = RecebimentoService.aprovar(carga, self.usuario)

        self.assertEqual(lote.numero_lote, f'MP260825-{carga.numero:05d}')

    def test_sem_classificacao_nao_aprova(self):
        fruta = self._fruta()
        fruta.produto = self._produto()
        fruta.save()
        carga = self._carga(fruta=fruta)

        with self.assertRaises(DomainError) as erro:
            RecebimentoService.aprovar(carga, self.usuario)

        self.assertIn('Classificação', str(erro.exception))

    def test_sem_preco_nao_aprova(self):
        """Lote sem custo contamina a margem de tudo que sair daquela fruta."""
        carga = self._pronta()
        carga.preco_kg = Decimal('0')
        carga.save()

        with self.assertRaises(DomainError) as erro:
            RecebimentoService.aprovar(carga, self.usuario)

        self.assertIn('Preço', str(erro.exception))

    def test_fruta_sem_produto_no_catalogo_explica_o_que_fazer(self):
        """
        Uma trava que só diz "não pode" transfere o trabalho de descobrir o
        porquê para quem já está com o caminhão na porta.
        """
        carga = self._carga()
        RecebimentoService.classificar(carga, {'brix': Decimal('13')}, self.usuario)

        with self.assertRaises(DomainError) as erro:
            RecebimentoService.aprovar(carga, self.usuario)

        self.assertIn('catálogo', str(erro.exception))

    def test_carga_fora_da_regua_ainda_pode_ser_aceita(self):
        """
        Manga com Brix meio ponto abaixo pode servir para um produto que
        leva açúcar. Travar faria a fábrica registrar outro número para
        conseguir seguir — e aí o registro deixa de valer alguma coisa.
        """
        fruta = self._fruta()
        fruta.produto = self._produto()
        fruta.save()
        carga = self._carga(fruta=fruta)
        RecebimentoService.classificar(carga, {'brix': Decimal('9')}, self.usuario)

        lote = RecebimentoService.aprovar(carga, self.usuario)

        self.assertIsNotNone(lote)
        # E o desvio continua registrado no romaneio, alcançável pelo lote.
        self.assertTrue(carga.reprovacoes())
        self.assertEqual(lote.recebimentos_polpa.first(), carga)

    def test_aprovar_duas_vezes_nao_cria_dois_lotes(self):
        carga = self._pronta()
        RecebimentoService.aprovar(carga, self.usuario)

        with self.assertRaises(DomainError):
            RecebimentoService.aprovar(carga, self.usuario)

        self.assertEqual(LoteProduto.objects.count(), 1)


class RecusaTests(PolpaBase):
    """A carga que volta no caminhão."""

    def test_recusa_exige_motivo(self):
        carga = self._carga()

        with self.assertRaises(DomainError):
            RecebimentoService.recusar(carga, '', self.usuario)

        carga.refresh_from_db()
        self.assertNotEqual(carga.status, Recebimento.Status.RECUSADO)

    def test_recusa_guarda_o_motivo_e_quem_decidiu(self):
        carga = self._carga()

        RecebimentoService.recusar(carga, 'Fruta fermentada, chegou a 34C', self.usuario)

        carga.refresh_from_db()
        self.assertEqual(carga.status, Recebimento.Status.RECUSADO)
        self.assertIn('fermentada', carga.motivo_recusa)
        self.assertEqual(carga.decidido_por, self.usuario)
        self.assertIsNotNone(carga.decidido_em)

    def test_carga_decidida_nao_recebe_classificacao_nova(self):
        """
        Reescrever a medição depois da decisão mudaria a base de um lote que
        talvez já esteja sendo processado.
        """
        carga = self._carga()
        RecebimentoService.recusar(carga, 'Fruta fermentada', self.usuario)

        with self.assertRaises(DomainError):
            RecebimentoService.classificar(carga, {'brix': Decimal('12')}, self.usuario)

    def test_nao_cancela_carga_que_ja_virou_lote(self):
        fruta = self._fruta()
        fruta.produto = self._produto()
        fruta.save()
        carga = self._carga(fruta=fruta)
        RecebimentoService.classificar(carga, {'brix': Decimal('13')}, self.usuario)
        RecebimentoService.aprovar(carga, self.usuario)

        with self.assertRaises(DomainError) as erro:
            RecebimentoService.cancelar(carga, 'errei', self.usuario)

        self.assertIn('estoque', str(erro.exception))


class ResumoTests(PolpaBase):
    """O dia da balança, do jeito que o painel conta."""

    def test_so_o_aprovado_conta_como_entrada(self):
        """
        Carga em classificação ainda pode voltar no caminhão. Somá-la faria
        o painel prometer fruta que a fábrica talvez não tenha.
        """
        fruta = self._fruta()
        fruta.produto = self._produto()
        fruta.save()
        aprovada = self._carga(fruta=fruta, data=date.today())
        RecebimentoService.classificar(aprovada, {'brix': Decimal('13')}, self.usuario)
        RecebimentoService.aprovar(aprovada, self.usuario)
        self._carga(fruta=fruta, data=date.today())  # ainda em pesagem

        resumo = RecebimentoService.resumo(self.filial)

        self.assertEqual(resumo['cargas'], 2)
        self.assertEqual(resumo['aguardando'], 1)
        self.assertEqual(resumo['kg_aceitos'], Decimal('10000'))
        self.assertEqual(resumo['polpa_prevista'], Decimal('6000.000'))


class TelasTests(PolpaBase):
    """As telas abrem, e a fila mostra o que existe."""

    def test_hub_abre_com_o_processo(self):
        resposta = self.client.get(reverse('polpa:hub'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'O caminho da fruta')
        self.assertContains(resposta, 'Recebimento')

    def test_todas_as_telas_do_menu_abrem(self):
        """
        Nenhum link do menu pode dar 404 — nem os que ainda estão em
        construção. É o teste que pega rota engolida pelo catch-all.
        """
        from apps.polpa.menu import GRUPOS

        for grupo in GRUPOS:
            with self.subTest(grupo=grupo.slug):
                resposta = self.client.get(reverse('polpa:grupo', args=[grupo.slug]))
                self.assertEqual(resposta.status_code, 200)
            for item in grupo.itens:
                with self.subTest(item=f'{grupo.slug}/{item.slug}'):
                    resposta = self.client.get(
                        reverse('polpa:item', args=[grupo.slug, item.slug])
                    )
                    self.assertEqual(resposta.status_code, 200)

    def test_a_tela_pronta_nao_e_engolida_pelo_placeholder(self):
        """
        Rota de dois segmentos casa com qualquer coisa: se a tela pronta for
        declarada depois do catch-all, o link sai certo e a página abre no
        "em construção" — o pior tipo de defeito, porque nada avisa.
        """
        from apps.polpa.views import ItemView
        from django.urls import resolve

        achado = resolve(reverse('polpa:item', args=['recebimento', 'romaneios']))

        self.assertIsNot(getattr(achado.func, 'view_class', None), ItemView)

    def test_a_fila_lista_a_carga(self):
        carga = self._carga()

        resposta = self.client.get(reverse('polpa:recebimento-list'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, f'#{carga.numero:05d}')
        self.assertContains(resposta, 'Sitio Boa Vista')

    def test_romaneio_novo_grava_pela_tela(self):
        fruta = self._fruta()

        resposta = self.client.post(reverse('polpa:recebimento-create'), {
            'fruta': fruta.pk, 'produtor': self.produtor.pk,
            'data': '2026-08-25',
            'peso_bruto': '12000', 'tara': '2000',
            'desconto_kg': '0', 'preco_kg': '1.5',
        })

        self.assertEqual(resposta.status_code, 302)
        carga = Recebimento.objects.for_filial(self.filial).first()
        self.assertIsNotNone(carga)
        self.assertEqual(carga.peso_liquido, Decimal('10000'))
        self.assertEqual(carga.criado_por, self.usuario)

    def test_tara_maior_que_o_bruto_e_recusada_na_tela(self):
        fruta = self._fruta()

        resposta = self.client.post(reverse('polpa:recebimento-create'), {
            'fruta': fruta.pk, 'produtor': self.produtor.pk,
            'data': '2026-08-25', 'peso_bruto': '1000', 'tara': '3000',
            'desconto_kg': '0', 'preco_kg': '1.5',
        })

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'maior que o peso bruto')
        self.assertEqual(Recebimento.objects.count(), 0)

    def test_detalhe_mostra_o_que_falta_para_aprovar(self):
        """
        O que falta vem escrito ANTES do botão, não como erro depois do
        clique: quem está com o caminhão na porta precisa saber o que
        resolver, não descobrir tentando.
        """
        carga = self._carga()

        resposta = self.client.get(
            reverse('polpa:recebimento-detail', args=[carga.pk])
        )

        self.assertContains(resposta, 'Falta para aprovar')
        self.assertContains(resposta, 'Classificação não registrada')


class PermissaoTests(PolpaBase):
    """
    Quem tem o vertical entra; quem tem a área responde pela área.

    O guarda-chuva existe porque as áreas nascem DEPOIS do módulo: sem ele,
    conceder `polpa` na tela de perfis daria um menu que não abre nada — e
    a conclusão de quem recebe isso é que o módulo está quebrado.
    """

    def _perfil(self, **modulos):
        from apps.core.models import Permissao

        perfil = PerfilAcesso.objects.create(
            empresa=self.empresa, nome=f'Perfil {len(modulos)}{id(modulos)}',
        )
        for modulo, acoes in modulos.items():
            Permissao.objects.create(
                perfil=perfil, modulo=modulo,
                **{f'pode_{acao}': True for acao in acoes},
            )
        return Usuario.objects.create_user(
            email=f'u{id(perfil)}@polpa.local', nome='Fulano', password='x' * 12,
            empresa=self.empresa, perfil=perfil, filial=self.filial,
        )

    def test_so_o_modulo_ja_abre_as_areas(self):
        usuario = self._perfil(polpa=('ver',))

        self.assertTrue(usuario.tem_permissao('polpa_recebimento', 'ver'))
        self.assertTrue(usuario.tem_permissao('polpa_frio', 'ver'))

    def test_perfil_com_area_responde_pela_area(self):
        """
        Quem foi montado com áreas não herda o vertical inteiro: o pessoal
        da balança não decide o que a qualidade libera.
        """
        usuario = self._perfil(
            polpa=('ver', 'aprovar'), polpa_recebimento=('ver', 'criar'),
        )

        self.assertTrue(usuario.tem_permissao('polpa_recebimento', 'criar'))
        self.assertFalse(usuario.tem_permissao('polpa_recebimento', 'aprovar'))
        self.assertFalse(usuario.tem_permissao('polpa_qualidade', 'ver'))

    def test_o_guarda_chuva_da_moda_continua_valendo(self):
        """A generalização não pode ter mudado o vertical que já estava no ar."""
        usuario = self._perfil(moda=('ver', 'editar'))

        self.assertTrue(usuario.tem_permissao('moda_corte', 'editar'))

    def test_modulo_comum_nao_vira_area_por_engano(self):
        """
        `food_service` tem underscore no nome e NÃO é área de nada. Se o
        prefixo fosse aceito sem lista, quem tivesse `food` herdaria o
        módulo inteiro.
        """
        usuario = self._perfil(polpa=('ver',))

        self.assertFalse(usuario.tem_permissao('food_service', 'ver'))


class ATelaDaFrutaTests(PolpaBase):
    """A ficha da fruta — três perguntas, e não onze campos em fila."""

    def test_a_regua_aparece_junta_e_nomeada(self):
        """
        Empilhados, "Brix mínimo", "pH máximo" e "Impureza máxima" nao diziam
        que sao a MESMA coisa: a regua que a classificacao usa para aceitar ou
        devolver a carga.
        """
        resposta = self.client.get(reverse('polpa:fruta-create'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'A régua que decide aceitar a carga')
        self.assertContains(resposta, 'Que fruta é')
        self.assertContains(resposta, 'Rendimento e safra')

    def test_os_rotulos_tecnicos_saem_certos(self):
        """
        O padrao do Django vinha do nome do campo: "Ph maximo", sem acento e
        com caixa errada num termo que a etiqueta do laboratorio escreve "pH".
        """
        resposta = self.client.get(reverse('polpa:fruta-create'))

        self.assertContains(resposta, 'pH máximo')
        self.assertContains(resposta, 'Brix mínimo')
        self.assertNotContains(resposta, 'Ph maximo')

    def test_campo_fora_de_grupo_nao_some_da_tela(self):
        """
        A GARANTIA QUE O LACO ANTIGO DAVA. Agrupar por lista escrita a mao e'
        como se perde campo: ele existe no formulario, nao aparece na tela, e
        ninguem entende por que nunca e' preenchido. O ultimo cartao recolhe o
        que nao esta' em grupo nenhum -- `observacao` e `ativo` provam isso, e
        um campo novo cairia ali do mesmo jeito.
        """
        from apps.polpa.forms import FrutaForm
        from apps.polpa.views_recebimento import FrutaFormView

        agrupados = {
            nome
            for _t, _d, campos in FrutaFormView.GRUPOS
            for nome in campos
        }
        sobras = set(FrutaForm(filial=self.filial).fields) - agrupados
        self.assertTrue(sobras, 'o teste so vale se houver campo fora de grupo')

        html = self.client.get(reverse('polpa:fruta-create')).content.decode()
        for nome in sobras:
            self.assertIn(f'name="{nome}"', html, f'campo "{nome}" sumiu da tela')

    def test_todo_campo_do_formulario_chega_na_tela(self):
        """A mesma garantia, dita pelo total: nenhum campo pode faltar."""
        from apps.polpa.forms import FrutaForm

        html = self.client.get(reverse('polpa:fruta-create')).content.decode()

        for nome in FrutaForm(filial=self.filial).fields:
            self.assertIn(f'name="{nome}"', html, f'campo "{nome}" sumiu da tela')
