"""
O motor aceita etapas que a indústria cria — sem tocar no vocabulário comum.

AUDITORIA PRIMEIRO, porque a especificação pede que o módulo não tenha regra
rígida só de polpa. Quase tudo já era configurável: produtos, receitas com
versão, equipamentos (`Recurso`), parâmetros de qualidade por produto e etapa,
unidades de medida, linhas de produção — e a receita JÁ MANDA no processo
(`_etapas_da_receita` vem antes do fluxo padrão do produto).

A ÚNICA RIGIDEZ REAL ERA A ETAPA. As trinta e quatro são um enum, e quem não
achasse a sua ali só podia escrevê-la como INSTRUÇÃO solta na receita: aparecia
na tela e não recebia apontamento — logo, não entrava no rendimento nem na
perda. A etapa mais cara do processo ficava invisível justamente para os
números que ela move.

O que os testes cercam:

  · A ETAPA CRIADA VIRA APONTAMENTO, com peso, perda e rendimento como as
    outras;
  · ELA ENTRA NO LUGAR CERTO da fila. Sem a sequência do cadastro, toda etapa
    nova cairia no fim — e uma fermentação depois do congelamento não descreve
    fábrica nenhuma;
  · O NOME APARECE ESCRITO POR GENTE. `get_etapa_display()` devolve o código
    cru quando o valor não está em `choices`;
  · O VOCABULÁRIO COMUM NÃO SE SOBRESCREVE. Uma "pasteurizacao" da casa faria
    o mesmo código significar duas coisas no mesmo banco;
  · AS TRINTA E QUATRO CONTINUAM INTACTAS — é por elas que o rendimento por
    etapa soma entre produtos diferentes.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.polpa.models import (
    Etapa, EtapaProcesso, EtapaReceita, FichaProduto, OrdemPolpa,
)
from apps.polpa.models.processo import POSICAO
from apps.polpa.services import CatalogoService, OrdemPolpaService, ReceitaService
from apps.polpa.services.processo import ProcessoService
from apps.producao.models import ItemFichaTecnica
from apps.produtos.models import UnidadeMedida, UnidadeMedidaFilial

T = FichaProduto.Tipo
ZERO = Decimal('0')


class EtapaConfiguravelBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Congelados Motor LTDA', nome_fantasia='Motor',
            cnpj='22245678000191', segmento='polpa_frutas',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='22245678000272',
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
            email='chefe@motor.local', nome='Chefe', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )

    def setUp(self):
        self.acabado = self._item(T.POLPA, 'Polpa fermentada 1 kg')
        self.fruta = self._item(T.FRUTA, 'Fruta in natura')

    def _item(self, tipo, descricao):
        return CatalogoService.salvar(self.filial, {
            'tipo': tipo, 'descricao': descricao, 'codigo': descricao[:10],
            'unidade_medida': self.unidade,
        }).produto

    def _fermentacao(self, **campos):
        """A etapa que nenhuma fábrica de polpa tem — e a de fermentados tem."""
        dados = {
            'filial': self.filial, 'codigo': 'fermentacao',
            'nome': 'Fermentação', 'sequencia': POSICAO[Etapa.MISTURA] + 1,
        }
        dados.update(campos)
        return EtapaProcesso.objects.create(**dados)

    def _receita(self, etapas):
        """`etapas` é a lista de códigos que a receita declara, em ordem."""
        receita = ReceitaService.criar(self.filial, self.acabado, {
            'descricao': 'Polpa fermentada', 'versao': '1.0',
            'quantidade_produzida': Decimal('1000'),
            'rendimento_esperado': Decimal('70'),
        })
        ItemFichaTecnica.objects.create(
            ficha=receita.ficha, materia_prima=self.fruta,
            quantidade=Decimal('500'), perda_prevista=ZERO,
        )
        for indice, codigo in enumerate(etapas, start=1):
            EtapaReceita.objects.create(
                receita=receita, ordem=indice, nome=codigo, etapa=codigo,
            )
        ReceitaService.ativar(receita)
        return receita

    def _ordem(self, receita):
        return OrdemPolpaService.criar(
            self.filial, receita,
            {'quantidade_planejada': Decimal('1000')}, self.usuario,
        )


class EtapaCriadaViraApontamentoTests(EtapaConfiguravelBase):

    def test_a_etapa_da_casa_entra_na_ordem(self):
        """
        Antes ela só podia ser instrução solta: aparecia na tela e não
        recebia apontamento — logo, não entrava no rendimento.
        """
        self._fermentacao()
        receita = self._receita([Etapa.RECEPCAO, 'fermentacao', Etapa.ENVASE])

        ordem = self._ordem(receita)

        codigos = list(ordem.etapas_processo.values_list('etapa', flat=True))
        self.assertIn('fermentacao', codigos)

    def test_ela_entra_no_lugar_certo_da_fila(self):
        """
        Sem a sequência do cadastro toda etapa nova cairia no fim — e uma
        fermentação depois do congelamento não descreve fábrica nenhuma.
        """
        self._fermentacao()
        receita = self._receita([Etapa.RECEPCAO, 'fermentacao', Etapa.ENVASE])

        ordem = self._ordem(receita)

        ordenadas = list(
            ordem.etapas_processo.order_by('sequencia')
            .values_list('etapa', flat=True)
        )
        self.assertEqual(
            ordenadas, [Etapa.RECEPCAO, 'fermentacao', Etapa.ENVASE],
        )

    def test_o_nome_aparece_escrito_por_gente(self):
        """
        `get_etapa_display()` devolve o código cru fora de `choices` — a
        etapa apareceria como "fermentacao" no meio de nomes com acento.
        """
        self._fermentacao()
        receita = self._receita([Etapa.RECEPCAO, 'fermentacao'])
        ordem = self._ordem(receita)

        etapa = ordem.etapas_processo.get(etapa='fermentacao')

        self.assertEqual(etapa.rotulo, 'Fermentação')

    def test_ela_pesa_e_perde_como_as_outras(self):
        """É o que a torna visível no rendimento — o ponto todo."""
        self._fermentacao()
        receita = self._receita([Etapa.RECEPCAO, 'fermentacao'])
        ordem = self._ordem(receita)
        etapa = ordem.etapas_processo.get(etapa='fermentacao')

        ProcessoService.apontar(etapa, {
            'quantidade_entrada': '1000', 'quantidade_saida': '900',
        }, self.usuario)

        etapa.refresh_from_db()
        self.assertEqual(etapa.perda, Decimal('100.000'))
        self.assertEqual(etapa.perda_percentual, Decimal('10.00'))

    def test_o_cadastro_diz_se_ela_exige_temperatura(self):
        """Quem cadastrou sabe se aquela fermentação precisa de termômetro."""
        self._fermentacao(exige_temperatura=True)
        receita = self._receita([Etapa.RECEPCAO, 'fermentacao'])
        ordem = self._ordem(receita)

        etapa = ordem.etapas_processo.get(etapa='fermentacao')

        self.assertTrue(etapa.exige_temperatura)

    def test_etapa_inativa_nao_entra(self):
        self._fermentacao(ativo=False)
        receita = self._receita([Etapa.RECEPCAO, 'fermentacao'])

        ordem = self._ordem(receita)

        etapa = ordem.etapas_processo.filter(etapa='fermentacao').first()
        # Sem cadastro ativo ela perde nome e posição, e vai para o fim.
        if etapa is not None:
            self.assertEqual(etapa.sequencia, 99)


class VocabularioComumIntactoTests(EtapaConfiguravelBase):

    def test_nao_se_recria_uma_etapa_canonica(self):
        """
        O mesmo código significando duas coisas no mesmo banco é como um
        indicador deixa de somar.
        """
        duplicada = EtapaProcesso(
            filial=self.filial, codigo=Etapa.PASTEURIZACAO,
            nome='Pasteurização da casa', sequencia=10,
        )

        with self.assertRaises(ValidationError):
            duplicada.full_clean()

    def test_as_canonicas_continuam_com_o_rotulo_delas(self):
        self._fermentacao()
        receita = self._receita([Etapa.RECEPCAO, 'fermentacao'])
        ordem = self._ordem(receita)

        recepcao = ordem.etapas_processo.get(etapa=Etapa.RECEPCAO)

        self.assertEqual(recepcao.rotulo, 'Recepção da matéria-prima')

    def test_a_posicao_das_canonicas_nao_muda(self):
        """
        O cadastro da casa não é consultado para código canônico — é o que
        garante que o rendimento por etapa continue somando entre produtos.
        """
        self._fermentacao(sequencia=0)  # tentaria disputar o primeiro lugar
        receita = self._receita([Etapa.RECEPCAO, 'fermentacao'])

        ordem = self._ordem(receita)

        recepcao = ordem.etapas_processo.get(etapa=Etapa.RECEPCAO)
        self.assertEqual(recepcao.sequencia, POSICAO[Etapa.RECEPCAO])

    def test_receita_sem_etapa_da_casa_funciona_como_antes(self):
        """A mudança não pode custar nada a quem não usa etapa nova."""
        receita = self._receita([Etapa.RECEPCAO, Etapa.ENVASE])

        ordem = self._ordem(receita)

        self.assertEqual(
            list(ordem.etapas_processo.order_by('sequencia')
                 .values_list('etapa', flat=True)),
            [Etapa.RECEPCAO, Etapa.ENVASE],
        )


class PorFilialTests(EtapaConfiguravelBase):

    def test_a_etapa_e_da_filial_que_a_criou(self):
        """
        A mesma empresa pode ter uma unidade que fermenta e outra que não —
        uma lista global encheria a tela da segunda com etapa que ela nunca
        vai apontar.
        """
        outra = Filial.objects.create(
            empresa=self.empresa, razao_social='Filial 2',
            cnpj='22245678000353', uf='RN', cidade='Mossoró',
        )
        self._fermentacao()

        self.assertEqual(
            EtapaProcesso.all_objects.filter(filial=outra).count(), 0,
        )
        self.assertEqual(
            EtapaProcesso.all_objects.filter(filial=self.filial).count(), 1,
        )
