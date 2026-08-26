"""
Desativar o vínculo de fornecedor libera os itens de entrada abertos.

O BLOCO EXISTIA E NUNCA FUNCIONOU. Ele filtrava
`ItemEntradaNF.fornecedor_cnpj` — campo que não existe; o fornecedor está na
ENTRADA, não no item. Levantava `FieldError` em toda execução, e um
`except: pass` mudo engolia. Resultado: o vínculo era desativado, os itens
continuavam presos ao produto, e ninguém ficava sabendo.

POR QUE NINGUÉM PEGOU: o arquivo que testaria isto,
`test_produto_fornecedor_vinculos.py`, NÃO IMPORTA — pede
`EntradaNFDesvincularItemView` de `compras.views.entrada`, view que não existe.
O módulo inteiro (dezoito testes) aparece como um erro de carga na suíte, e
some no meio dela. Este arquivo fica separado justamente para não depender
daquele.

O PAR CNPJ↔CNPJ É EXATO. `vinculo.fornecedor_cnpj_xml` é gravado A PARTIR de
`entrada.emitente_cnpj_xml` — mesma origem, mesmo formato, nada a normalizar.
É o mesmo pareamento que `entrada_produto_service` já faz no sentido inverso.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase
from django.utils import timezone

from apps.cadastros.models import Fornecedor
from apps.compras.models import EntradaNF, ItemEntradaNF
from apps.compras.services.entrada_produto_service import (
    MARCADOR_VINCULO_REMOVIDO,
)
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.produtos.models import (
    Produto, ProdutoFornecedorEquivalencia, UnidadeMedida,
)
from apps.produtos.views.produto import (
    ENTRADAS_ABERTAS, ProdutoFornecedorVinculoDeleteView,
)

CNPJ_XML = '12.345.678/0001-90'
ZERO = Decimal('0')


class VinculoLiberaItemBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Vinculos LTDA', nome_fantasia='Vinculos',
            cnpj='66645678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='66645678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='UN', descricao='Unidade',
        )
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='chefe@vinc.local', nome='Chefe', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.fornecedor = Fornecedor.objects.create(
            filial=cls.filial, razao_social='Distribuidora Alfa',
            cpf_cnpj='12345678000190',
        )
        cls.outro_fornecedor = Fornecedor.objects.create(
            filial=cls.filial, razao_social='Distribuidora Beta',
            cpf_cnpj='98765432000110',
        )

    # ── Montagem ─────────────────────────────────────────────────────────

    def _produto(self, codigo='P1'):
        return Produto.objects.create(
            filial=self.filial, codigo=codigo, descricao=f'Produto {codigo}',
            unidade_medida=self.unidade,
        )

    def _vinculo(self, produto, com_cnpj=True, com_fk=False):
        return ProdutoFornecedorEquivalencia.objects.create(
            produto=produto,
            fornecedor=self.fornecedor if com_fk else None,
            fornecedor_cnpj_xml=CNPJ_XML if com_cnpj else '',
            codigo_fornecedor='COD-1',
            ativo=True,
        )

    def _entrada(self, status=EntradaNF.Status.AGUARDANDO_CONFERENCIA,
                 cnpj=CNPJ_XML, fornecedor=None, numero='1'):
        return EntradaNF.objects.create(
            filial=self.filial,
            fornecedor=fornecedor or self.fornecedor,
            numero_nf=numero, serie_nf='1',
            chave_acesso_nf=f'{numero:0>44}',
            emitente_cnpj_xml=cnpj,
            data_emissao_nf=timezone.localdate(),
            data_entrada=timezone.localdate(),
            status=status,
            usuario=self.usuario,
        )

    def _item(self, entrada, produto, observacao=''):
        return ItemEntradaNF.objects.create(
            entrada=entrada, produto=produto, numero_item=1,
            quantidade=Decimal('10'), valor_unitario=Decimal('5'),
            valor_bruto=Decimal('50'), valor_total=Decimal('50'),
            observacao=observacao,
        )

    def _desativar(self, produto, vinculo):
        requisicao = RequestFactory().post('/')
        requisicao.user = self.usuario
        requisicao.filial_ativa = self.filial
        # `RequestFactory` não passa pelos middlewares: sem sessão e sem
        # armazenamento de mensagens, o `messages.success` da view estoura.
        requisicao.session = {}
        requisicao._messages = FallbackStorage(requisicao)
        return ProdutoFornecedorVinculoDeleteView.as_view()(
            requisicao, pk=produto.pk, vinculo_pk=vinculo.pk,
        )


class LiberaOQueDeveTests(VinculoLiberaItemBase):

    def test_o_item_de_entrada_aberta_e_liberado(self):
        """O que o bloco quebrado prometia e nunca fez."""
        produto = self._produto()
        vinculo = self._vinculo(produto)
        item = self._item(self._entrada(), produto)

        self._desativar(produto, vinculo)

        item.refresh_from_db()
        self.assertIsNone(item.produto_id)

    def test_o_item_recebe_o_marcador_que_impede_revinculo(self):
        """
        Desvincular não é só zerar o produto. Sem o marcador, o próximo
        reprocessamento reataria o item ao mesmo produto — e a desativação do
        vínculo seria desfeita sozinha.
        """
        produto = self._produto()
        vinculo = self._vinculo(produto)
        item = self._item(self._entrada(), produto)

        self._desativar(produto, vinculo)

        item.refresh_from_db()
        self.assertIn(MARCADOR_VINCULO_REMOVIDO, item.observacao)

    def test_o_vinculo_e_desativado_de_qualquer_jeito(self):
        produto = self._produto()
        vinculo = self._vinculo(produto)

        resposta = self._desativar(produto, vinculo)

        vinculo.refresh_from_db()
        self.assertEqual(resposta.status_code, 302)
        self.assertFalse(vinculo.ativo)

    def test_libera_pela_chave_estrangeira_quando_nao_ha_cnpj(self):
        """
        Vínculo criado à mão não tem CNPJ do XML — tem o fornecedor. As duas
        portas valem, como no `entrada_produto_service`.
        """
        produto = self._produto()
        vinculo = self._vinculo(produto, com_cnpj=False, com_fk=True)
        item = self._item(self._entrada(), produto)

        self._desativar(produto, vinculo)

        item.refresh_from_db()
        self.assertIsNone(item.produto_id)


class NaoLiberaOQueNaoDeveTests(VinculoLiberaItemBase):

    def test_entrada_efetivada_nao_e_mexida(self):
        """
        O item já virou estoque e movimentação. Soltá-lo deixaria um
        lançamento sem produto, e o custo daquela compra pararia de bater.
        """
        produto = self._produto()
        vinculo = self._vinculo(produto)
        item = self._item(
            self._entrada(status=EntradaNF.Status.EFETIVADA), produto,
        )

        self._desativar(produto, vinculo)

        item.refresh_from_db()
        self.assertEqual(item.produto_id, produto.pk)

    def test_item_de_outro_fornecedor_fica_onde_esta(self):
        """"Via este vínculo" — e não "de todos os fornecedores"."""
        produto = self._produto()
        vinculo = self._vinculo(produto)
        alheio = self._item(
            self._entrada(
                cnpj='98.765.432/0001-10',
                fornecedor=self.outro_fornecedor, numero='2',
            ),
            produto,
        )

        self._desativar(produto, vinculo)

        alheio.refresh_from_db()
        self.assertEqual(alheio.produto_id, produto.pk)

    def test_item_de_outro_produto_fica_onde_esta(self):
        produto = self._produto()
        outro_produto = self._produto(codigo='P2')
        vinculo = self._vinculo(produto)
        alheio = self._item(self._entrada(), outro_produto)

        self._desativar(produto, vinculo)

        alheio.refresh_from_db()
        self.assertEqual(alheio.produto_id, outro_produto.pk)

    def test_vinculo_sem_cnpj_e_sem_fornecedor_nao_mexe_em_nada(self):
        """
        Ele não diz de quem é. Filtrar só por produto soltaria itens de TODOS
        os fornecedores, que é o oposto de "via este vínculo".
        """
        produto = self._produto()
        vinculo = self._vinculo(produto, com_cnpj=False, com_fk=False)
        item = self._item(self._entrada(), produto)

        self._desativar(produto, vinculo)

        item.refresh_from_db()
        self.assertEqual(item.produto_id, produto.pk)


class OErroNaoDerrubaADesativacaoTests(VinculoLiberaItemBase):

    def test_falha_ao_liberar_nao_impede_desativar_o_vinculo(self):
        """
        O vínculo já foi desativado quando a liberação roda; um erro ali não
        pode desfazer aquilo. Mas agora vai para o log — foi um `except: pass`
        mudo que escondeu o defeito anterior por semanas.
        """
        from unittest.mock import patch

        produto = self._produto()
        vinculo = self._vinculo(produto)
        self._item(self._entrada(), produto)

        alvo = (
            'apps.compras.services.entrada_produto_service'
            '.desvincular_item_de_produto'
        )
        with patch(alvo, side_effect=RuntimeError('banco fora')):
            resposta = self._desativar(produto, vinculo)

        vinculo.refresh_from_db()
        self.assertEqual(resposta.status_code, 302)
        self.assertFalse(vinculo.ativo)


class LiteraisDeStatusTests(VinculoLiberaItemBase):

    def test_entradas_abertas_sao_status_que_existem(self):
        """
        `ENTRADAS_ABERTAS` guarda strings soltas, e não o enum, para `produtos`
        não importar `compras` em tempo de módulo. O preço disso é que um
        rename em `EntradaNF.Status` não quebraria nada de forma visível: o
        filtro simplesmente pararia de casar, e voltaríamos a não liberar item
        nenhum — o mesmo silêncio de antes, por outro caminho. Este teste é o
        que faz o rename estourar.
        """
        validos = {s.value for s in EntradaNF.Status}
        self.assertEqual(set(ENTRADAS_ABERTAS) - validos, set())

    def test_efetivada_esta_fora(self):
        """A regra que protege o estoque, dita como asserção."""
        self.assertNotIn(EntradaNF.Status.EFETIVADA.value, ENTRADAS_ABERTAS)
