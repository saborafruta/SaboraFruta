"""
O checklist de qualidade: montar, preencher e deixar o laudo decidir.

O cadastro de parâmetros por produto e etapa já existia, com tela. O que
faltava era a outra metade: o REGISTRO. `AnaliseQualidade.parametros` era um
JSON `{"brix": 12.5}` — valores soltos, sem veredito por linha e sem memória
do que era exigido. Um pH esquecido não virava pendência: virava chave
ausente, e o laudo fechava aprovado sem ele. E não havia rota nenhuma para
registrar uma análise.

O que os testes cercam:

  · O CHECKLIST NASCE INTEIRO, em pendente. Análise que só ganha linha quando
    alguém digita deixa o esquecido invisível — e o esquecido é o pH que
    ninguém mediu;
  · O VEREDITO SOBE DOS ITENS. Um não conforme reprova o laudo, e não vira
    média com os nove conformes: em alimento, o parâmetro fora da faixa é o
    que importa;
  · O QUE O NÚMERO NÃO DECIDE, A PESSOA DECIDE. Aparência e odor não têm
    mínimo e máximo, e um "conforme" automático neles seria um carimbo que
    ninguém deu;
  · OBRIGATÓRIO EM BRANCO NÃO FECHA. Fechar com pendência é assinar que se
    conferiu o que não se conferiu;
  · REPROVAR BLOQUEIA O LOTE, dizendo QUAIS itens reprovaram — o motivo vai
    para o lote, e é o texto que alguém lê ao esbarrar nele na câmara.
"""
from decimal import Decimal

from django.test import TestCase

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import DomainError
from apps.estoque.models import LoteProduto
from apps.qualidade.constants.enums import ResultadoAnalise
from apps.qualidade.models import ItemAnalise, ParametroQualidadeProduto
from apps.qualidade.services.checklist_service import (
    ETAPA_ACABADO, ETAPA_RECEBIMENTO, ChecklistService,
)
from apps.produtos.models import Produto, ProdutoFilial, UnidadeMedida

SIT = ItemAnalise.Situacao


class ChecklistBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Qualidade LTDA', nome_fantasia='Qualidade',
            cnpj='33345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='33345678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='KG', descricao='Quilograma',
        )
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='tecnico@q.local', nome='Técnica', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )

    def setUp(self):
        self.manga = Produto.objects.create(
            filial=self.filial, codigo='MANGA', descricao='Manga in natura',
            unidade_medida=self.unidade, controla_lote=True,
        )
        # `Produto.for_filial` olha o VÍNCULO, não a FK direta: produto sem
        # vínculo é invisível para a filial, e a tela devolveria 404.
        ProdutoFilial.objects.create(produto=self.manga, filial=self.filial)

    def _parametro(self, nome, etapa=ETAPA_RECEBIMENTO, minimo=None,
                   maximo=None, obrigatorio=True, tipo='numero'):
        return ParametroQualidadeProduto.objects.create(
            filial=self.filial, produto=self.manga, etapa=etapa,
            nome_parametro=nome, tipo_valor=tipo,
            valor_minimo=minimo, valor_maximo=maximo,
            obrigatorio=obrigatorio,
        )

    def _recebimento_padrao(self):
        """O checklist do print: aparência, maturação, temperatura, peso."""
        self._parametro('Brix', minimo=Decimal('11'), maximo=Decimal('16'))
        self._parametro('Temperatura', minimo=Decimal('0'), maximo=Decimal('10'))
        self._parametro('Aparência', tipo='texto')
        self._parametro('Embalagem', tipo='sim_nao', obrigatorio=False)

    def _lote(self, numero='FRUTA-001'):
        return LoteProduto.objects.create(
            filial=self.filial, produto=self.manga, numero_lote=numero,
            quantidade_inicial=Decimal('100'), quantidade_atual=Decimal('100'),
            custo_unitario=Decimal('2'), status=LoteProduto.Status.ATIVO,
        )

    def _abrir(self, etapa=ETAPA_RECEBIMENTO, lote=None):
        return ChecklistService.abrir(
            self.filial, self.manga, etapa, self.usuario, lote=lote,
        )

    def _item(self, analise, nome):
        return analise.itens.get(nome_parametro=nome)


class MontarTests(ChecklistBase):

    def test_o_checklist_nasce_inteiro(self):
        self._recebimento_padrao()

        analise = self._abrir()

        nomes = set(analise.itens.values_list('nome_parametro', flat=True))
        self.assertEqual(
            nomes, {'Brix', 'Temperatura', 'Aparência', 'Embalagem'},
        )

    def test_todo_item_nasce_pendente(self):
        """
        Item não medido tem de ficar visível como não medido. Nascer conforme
        é como sai um laudo aprovado sem o pH.
        """
        self._recebimento_padrao()

        analise = self._abrir()

        self.assertTrue(all(
            i.situacao == SIT.PENDENTE for i in analise.itens.all()
        ))

    def test_a_faixa_e_copiada_na_gravacao(self):
        """
        O parâmetro pode ser renomeado ou ter a faixa apertada depois. Um
        laudo que aprovou com Brix entre 11 e 16 não pode virar reprovado
        porque alguém mexeu no cadastro no mês seguinte.
        """
        self._recebimento_padrao()
        analise = self._abrir()
        parametro = ParametroQualidadeProduto.objects.get(nome_parametro='Brix')
        parametro.valor_minimo = Decimal('14')
        parametro.save(update_fields=['valor_minimo'])

        item = self._item(analise, 'Brix')

        self.assertEqual(item.valor_minimo, Decimal('11.000'))

    def test_so_traz_a_etapa_pedida(self):
        self._recebimento_padrao()
        self._parametro('Selagem', etapa=ETAPA_ACABADO, tipo='sim_nao')

        analise = self._abrir(etapa=ETAPA_RECEBIMENTO)

        self.assertNotIn(
            'Selagem',
            set(analise.itens.values_list('nome_parametro', flat=True)),
        )

    def test_parametro_inativo_fica_de_fora(self):
        self._recebimento_padrao()
        ParametroQualidadeProduto.objects.filter(
            nome_parametro='Brix',
        ).update(ativo=False)

        analise = self._abrir()

        self.assertEqual(analise.itens.filter(nome_parametro='Brix').count(), 0)

    def test_sem_cadastro_nao_abre(self):
        """
        Laudo sem nada a conferir não é laudo aprovado — é cadastro que falta,
        e deixar passar produziria um "aprovado" que não olhou para nada.
        """
        with self.assertRaises(DomainError):
            self._abrir()


class PreencherTests(ChecklistBase):

    def test_numero_dentro_da_faixa_fica_conforme(self):
        self._recebimento_padrao()
        analise = self._abrir()
        item = self._item(analise, 'Brix')

        ChecklistService.preencher(analise, {item.pk: {'valor': '13'}})

        item.refresh_from_db()
        self.assertEqual(item.situacao, SIT.CONFORME)

    def test_numero_fora_da_faixa_fica_nao_conforme(self):
        self._recebimento_padrao()
        analise = self._abrir()
        item = self._item(analise, 'Brix')

        ChecklistService.preencher(analise, {item.pk: {'valor': '8'}})

        item.refresh_from_db()
        self.assertEqual(item.situacao, SIT.NAO_CONFORME)

    def test_virgula_e_aceita_como_decimal(self):
        """Quem digita na fábrica escreve 12,5 — não 12.5."""
        self._recebimento_padrao()
        analise = self._abrir()
        item = self._item(analise, 'Brix')

        ChecklistService.preencher(analise, {item.pk: {'valor': '12,5'}})

        item.refresh_from_db()
        self.assertEqual(item.valor_numero, Decimal('12.5000'))

    def test_campo_subjetivo_espera_o_veredito_da_pessoa(self):
        """
        Aparência não tem mínimo nem máximo. Conforme automático aqui seria um
        carimbo que ninguém deu.
        """
        self._recebimento_padrao()
        analise = self._abrir()
        item = self._item(analise, 'Aparência')

        ChecklistService.preencher(analise, {item.pk: {'valor': 'Boa'}})

        item.refresh_from_db()
        self.assertEqual(item.valor_texto, 'Boa')
        self.assertEqual(item.situacao, SIT.PENDENTE)

    def test_a_pessoa_marca_o_subjetivo(self):
        self._recebimento_padrao()
        analise = self._abrir()
        item = self._item(analise, 'Aparência')

        ChecklistService.preencher(analise, {
            item.pk: {'valor': 'Manchas na casca', 'situacao': SIT.NAO_CONFORME},
        })

        item.refresh_from_db()
        self.assertEqual(item.situacao, SIT.NAO_CONFORME)

    def test_apagar_o_valor_devolve_a_pendente(self):
        """
        "Apaguei o que digitei" e "conferi e está conforme" não podem terminar
        iguais.
        """
        self._recebimento_padrao()
        analise = self._abrir()
        item = self._item(analise, 'Brix')
        ChecklistService.preencher(analise, {item.pk: {'valor': '13'}})

        ChecklistService.preencher(analise, {item.pk: {'valor': ''}})

        item.refresh_from_db()
        self.assertEqual(item.situacao, SIT.PENDENTE)
        self.assertIsNone(item.valor_numero)

    def test_item_ausente_do_formulario_nao_e_apagado(self):
        self._recebimento_padrao()
        analise = self._abrir()
        brix = self._item(analise, 'Brix')
        temperatura = self._item(analise, 'Temperatura')
        ChecklistService.preencher(analise, {brix.pk: {'valor': '13'}})

        ChecklistService.preencher(analise, {temperatura.pk: {'valor': '4'}})

        brix.refresh_from_db()
        self.assertEqual(brix.valor_numero, Decimal('13.0000'))

    def test_a_acao_corretiva_carimba_quem_e_quando(self):
        """Guardar só o texto deixaria a auditoria com uma frase sem dono."""
        self._recebimento_padrao()
        analise = self._abrir()
        item = self._item(analise, 'Brix')

        ChecklistService.preencher(
            analise,
            {item.pk: {'valor': '8', 'acao_corretiva': 'Carga devolvida ao produtor'}},
            usuario=self.usuario,
        )

        item.refresh_from_db()
        self.assertEqual(item.acao_responsavel, self.usuario)
        self.assertIsNotNone(item.acao_em)

    def test_o_json_antigo_continua_em_dia(self):
        """
        `AnaliseQualidade.parametros` é lido por código que existia antes
        desta tabela. Deixá-lo parado faria as duas leituras discordarem.
        """
        self._recebimento_padrao()
        analise = self._abrir()
        item = self._item(analise, 'Brix')

        ChecklistService.preencher(analise, {item.pk: {'valor': '13'}})

        analise.refresh_from_db()
        self.assertEqual(analise.parametros.get('Brix'), 13.0)


class ConcluirTests(ChecklistBase):

    def _preencher_tudo(self, analise, brix='13'):
        ChecklistService.preencher(analise, {
            self._item(analise, 'Brix').pk: {'valor': brix},
            self._item(analise, 'Temperatura').pk: {'valor': '4'},
            self._item(analise, 'Aparência').pk: {
                'valor': 'Boa', 'situacao': SIT.CONFORME,
            },
        }, usuario=self.usuario)

    def test_tudo_conforme_aprova(self):
        self._recebimento_padrao()
        analise = self._abrir()
        self._preencher_tudo(analise)

        ChecklistService.concluir(analise, self.usuario)

        analise.refresh_from_db()
        self.assertEqual(analise.resultado, ResultadoAnalise.APROVADO)

    def test_um_nao_conforme_reprova_o_laudo(self):
        """
        Não é média: em alimento, o parâmetro fora da faixa é o que importa, e
        diluí-lo entre os conformes produziria um aprovado que ninguém
        sustenta.
        """
        self._recebimento_padrao()
        analise = self._abrir()
        self._preencher_tudo(analise, brix='8')

        ChecklistService.concluir(analise, self.usuario)

        analise.refresh_from_db()
        self.assertEqual(analise.resultado, ResultadoAnalise.REPROVADO)

    def test_obrigatorio_em_branco_nao_fecha(self):
        """Fechar com pendência é assinar que se conferiu o que não se conferiu."""
        self._recebimento_padrao()
        analise = self._abrir()

        with self.assertRaises(DomainError) as erro:
            ChecklistService.concluir(analise, self.usuario)

        self.assertIn('Brix', str(erro.exception))

    def test_opcional_em_branco_nao_impede(self):
        self._recebimento_padrao()
        analise = self._abrir()
        self._preencher_tudo(analise)  # deixa "Embalagem" (opcional) em branco

        ChecklistService.concluir(analise, self.usuario)

        analise.refresh_from_db()
        self.assertEqual(analise.resultado, ResultadoAnalise.APROVADO)

    def test_reprovar_bloqueia_o_lote(self):
        self._recebimento_padrao()
        lote = self._lote()
        analise = self._abrir(lote=lote)
        self._preencher_tudo(analise, brix='8')

        ChecklistService.concluir(analise, self.usuario)

        lote.refresh_from_db()
        self.assertEqual(lote.status, LoteProduto.Status.BLOQUEADO)

    def test_o_motivo_diz_quais_itens_reprovaram(self):
        """
        O motivo vai para o lote, e é o texto que alguém lê meses depois ao
        esbarrar num lote travado. "Análise reprovada" não diz o que fazer.
        """
        self._recebimento_padrao()
        lote = self._lote()
        analise = self._abrir(lote=lote)
        self._preencher_tudo(analise, brix='8')

        ChecklistService.concluir(analise, self.usuario)

        lote.refresh_from_db()
        self.assertIn('Brix', lote.motivo_bloqueio)

    def test_aprovar_libera_o_lote(self):
        self._recebimento_padrao()
        lote = self._lote()
        analise = self._abrir(lote=lote)
        self._preencher_tudo(analise)

        ChecklistService.concluir(analise, self.usuario)

        lote.refresh_from_db()
        self.assertEqual(lote.status, LoteProduto.Status.ATIVO)


class ResumoTests(ChecklistBase):

    def test_o_resumo_conta_as_situacoes(self):
        self._recebimento_padrao()
        analise = self._abrir()
        ChecklistService.preencher(analise, {
            self._item(analise, 'Brix').pk: {'valor': '13'},
            self._item(analise, 'Temperatura').pk: {'valor': '40'},
        })

        resumo = ChecklistService.resumo(analise)

        self.assertEqual(resumo['conformes'], 1)
        self.assertEqual(resumo['nao_conformes'], 1)
        self.assertEqual(resumo['obrigatorios_pendentes'], 1)

    def test_o_resumo_aponta_nao_conforme_sem_acao(self):
        """
        Não conforme sem ação corretiva é o que a auditoria cobra — e o que
        alguém esquece de preencher com pressa.
        """
        self._recebimento_padrao()
        analise = self._abrir()
        item = self._item(analise, 'Brix')
        ChecklistService.preencher(analise, {item.pk: {'valor': '8'}})

        resumo = ChecklistService.resumo(analise)

        self.assertEqual([i.pk for i in resumo['sem_acao']], [item.pk])

    def test_com_acao_sai_da_lista(self):
        self._recebimento_padrao()
        analise = self._abrir()
        item = self._item(analise, 'Brix')
        ChecklistService.preencher(
            analise,
            {item.pk: {'valor': '8', 'acao_corretiva': 'Devolvida'}},
            usuario=self.usuario,
        )

        self.assertEqual(ChecklistService.resumo(analise)['sem_acao'], [])


class TelaTests(ChecklistBase):
    """A metade que não tinha rota: registrar."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.usuario)
        self._recebimento_padrao()

    def _url(self, nome, *args):
        from django.urls import reverse
        return reverse(f'qualidade:{nome}', args=args)

    def test_a_tela_lista_as_conferencias_abertas(self):
        analise = self._abrir()

        resposta = self.client.get(self._url('checklist_list'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Em conferência')
        self.assertContains(resposta, str(analise.pk))

    def test_abrir_pela_tela_monta_o_checklist(self):
        resposta = self.client.post(self._url('checklist_abrir'), {
            'produto': self.manga.pk, 'etapa': ETAPA_RECEBIMENTO,
        }, follow=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Brix')
        self.assertContains(resposta, 'Aparência')

    def test_abrir_etapa_sem_checklist_avisa_em_vez_de_estourar(self):
        """
        O produto tem checklist de recebimento, mas nada cadastrado para
        produto acabado. Abrir mesmo assim produziria um laudo que não olhou
        para nada — e um "aprovado" desses é pior que nenhum.
        """
        resposta = self.client.post(self._url('checklist_abrir'), {
            'produto': self.manga.pk, 'etapa': ETAPA_ACABADO,
        }, follow=True)

        mensagens = [str(m) for m in resposta.context['messages']]
        self.assertTrue(
            any('parâmetros de qualidade' in m for m in mensagens),
            f'esperava aviso de cadastro faltando, veio: {mensagens}',
        )

    def test_a_tela_mostra_a_faixa_exigida(self):
        """Sem a faixa na tela, quem confere não sabe o que é conforme."""
        analise = self._abrir()

        resposta = self.client.get(self._url('checklist_detail', analise.pk))

        self.assertContains(resposta, '11 a 16')

    def test_gravar_pela_tela_julga_o_valor(self):
        analise = self._abrir()
        brix = self._item(analise, 'Brix')

        self.client.post(self._url('checklist_salvar', analise.pk), {
            f'valor_{brix.pk}': '8',
        })

        brix.refresh_from_db()
        self.assertEqual(brix.situacao, SIT.NAO_CONFORME)

    def test_a_tela_avisa_o_que_fechar_vai_fazer(self):
        """
        Reprovar bloqueia material. O botão não pode ser a primeira vez que a
        pessoa descobre isso.
        """
        analise = self._abrir()
        self.client.post(self._url('checklist_salvar', analise.pk), {
            f'valor_{self._item(analise, "Brix").pk}': '8',
            f'valor_{self._item(analise, "Temperatura").pk}': '4',
            f'valor_{self._item(analise, "Aparência").pk}': 'Boa',
            f'situacao_{self._item(analise, "Aparência").pk}': SIT.CONFORME,
        })

        resposta = self.client.get(self._url('checklist_detail', analise.pk))

        self.assertContains(resposta, 'bloquear o lote')

    def test_fechar_com_obrigatorio_pendente_e_recusado(self):
        analise = self._abrir()

        resposta = self.client.post(
            self._url('checklist_concluir', analise.pk), follow=True,
        )

        mensagens = [str(m) for m in resposta.context['messages']]
        self.assertTrue(any('obrigatórios' in m for m in mensagens), mensagens)
        analise.refresh_from_db()
        self.assertEqual(analise.resultado, ResultadoAnalise.PENDENTE)

    def test_analise_encerrada_nao_aceita_nova_gravacao(self):
        analise = self._abrir()
        self.client.post(self._url('checklist_salvar', analise.pk), {
            f'valor_{self._item(analise, "Brix").pk}': '13',
            f'valor_{self._item(analise, "Temperatura").pk}': '4',
            f'valor_{self._item(analise, "Aparência").pk}': 'Boa',
            f'situacao_{self._item(analise, "Aparência").pk}': SIT.CONFORME,
        })
        self.client.post(self._url('checklist_concluir', analise.pk))
        analise.refresh_from_db()
        self.assertEqual(analise.resultado, ResultadoAnalise.APROVADO)

        resposta = self.client.post(
            self._url('checklist_salvar', analise.pk),
            {f'valor_{self._item(analise, "Brix").pk}': '99'}, follow=True,
        )

        mensagens = [str(m) for m in resposta.context['messages']]
        self.assertTrue(any('já foi encerrada' in m for m in mensagens), mensagens)

    def test_nada_de_tag_vaza_para_a_tela(self):
        analise = self._abrir()

        corpo = self.client.get(
            self._url('checklist_detail', analise.pk),
        ).content.decode()

        for marca in ('{%', '%}', '{#', '#}', 'endcomment'):
            self.assertNotIn(marca, corpo, f'tag {marca} vazou para a tela')
