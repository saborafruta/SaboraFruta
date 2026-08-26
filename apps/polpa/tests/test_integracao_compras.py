"""
Integração com compras: produção → falta de insumo → sugestão de compra.

O cenário da especificação, com os números dela: 500 kg de morango
necessários, 200 no galpão, 300 de sugestão.

O QUE ESTE MÓDULO FAZ E A NECESSIDADE POR ORDEM NÃO FAZ é SOMAR ANTES DE
COMPARAR. `OrdemPolpaService.necessidade` olha uma batida por vez, e cada uma
vê o estoque inteiro como se fosse só dela: três batidas que precisam de 200 kg
cada, com 500 no galpão, aparecem as três cobertas. Somadas, faltam 100. E
comprar por ordem compraria morango três vezes, com três fretes.

"RESERVAS DE OUTRAS" É O DETALHE QUE INVERTE O SINAL. `quantidade_disponivel`
desconta TODAS as reservas, inclusive as que estas mesmas ordens fizeram ao
entrar em produção. Usá-lo aqui faria RESERVAR AUMENTAR O DÉFICIT — a ação que
resolve o problema pioraria o indicador, e o comprador compraria de novo o que
já está separado no galpão.

E A REQUISIÇÃO NÃO VIRA "ATENDIDA" na emissão do pedido: atendida é quando o
material CHEGA. Marcar antes faria o PCP achar que tem morango no galpão
enquanto ele ainda está na roça.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.cadastros.models import Fornecedor
from apps.compras.models import PedidoCompra
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import DomainError
from apps.estoque.models import Estoque, LoteProduto
from apps.polpa.models import (
    EtapaReceita, FichaProduto, OrdemPolpa, RequisicaoInsumo,
)
from apps.polpa.services import CatalogoService, OrdemPolpaService, ReceitaService
from apps.polpa.services.compra import CompraService
from apps.producao.models import ItemFichaTecnica
from apps.produtos.models import UnidadeMedida, UnidadeMedidaFilial

T = FichaProduto.Tipo
S = OrdemPolpa.Situacao
ZERO = Decimal('0')


class CompraBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Polpas Compras LTDA', nome_fantasia='Compras',
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
            email='chefe@compras.local', nome='Chefe', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.fornecedor = Fornecedor.objects.create(
            filial=cls.filial, razao_social='Sítio do Morango',
            cpf_cnpj='11122233344',
        )

    def setUp(self):
        self.acabado = self._item(
            T.POLPA, 'Polpa de morango', validade_dias=180,
            peso_liquido=Decimal('0.100'),
        )
        self.morango = self._item(
            T.FRUTA, 'Morango in natura', custo=Decimal('12'),
        )
        self.receita = self._receita()

    # ── Montagem ─────────────────────────────────────────────────────────

    def _item(self, tipo, descricao, custo=Decimal('0'), **extras):
        dados = {
            'tipo': tipo, 'descricao': descricao, 'codigo': descricao[:10],
            'unidade_medida': self.unidade, 'preco_custo': custo,
        }
        dados.update(extras)
        return CatalogoService.salvar(self.filial, dados).produto

    def _receita(self):
        """Cada 1.000 unidades consomem 500 kg de morango."""
        receita = ReceitaService.criar(self.filial, self.acabado, {
            'descricao': 'Polpa de morango', 'versao': '1.0',
            'quantidade_produzida': Decimal('1000'),
            'rendimento_esperado': Decimal('60'),
        })
        ItemFichaTecnica.objects.create(
            ficha=receita.ficha, materia_prima=self.morango,
            quantidade=Decimal('500'), perda_prevista=ZERO,
        )
        EtapaReceita.objects.create(receita=receita, ordem=1, nome='Despolpa')
        ReceitaService.ativar(receita)
        return receita

    def _estoque(self, quantidade, reservado='0'):
        estoque, _ = Estoque.objects.get_or_create(
            produto=self.morango, filial=self.filial,
        )
        estoque.quantidade_atual = Decimal(quantidade)
        estoque.quantidade_reservada = Decimal(reservado)
        estoque.atualizar_disponivel()
        estoque.save()
        LoteProduto.objects.get_or_create(
            filial=self.filial, produto=self.morango, numero_lote='L-MOR',
            defaults={
                'quantidade_inicial': Decimal(quantidade),
                'quantidade_atual': Decimal(quantidade),
                'custo_unitario': Decimal('12'),
                'data_validade': timezone.localdate() + timedelta(days=90),
                'status': LoteProduto.Status.ATIVO,
            },
        )

    def _op(self, quantidade='1000'):
        return OrdemPolpaService.criar(
            self.filial, self.receita,
            {'quantidade_planejada': Decimal(quantidade)}, self.usuario,
        )

    def _linha(self):
        linhas = CompraService.necessidade(self.filial)
        return next(l for l in linhas if l.produto.pk == self.morango.pk)


class OExemploDaEspecificacaoTests(CompraBase):

    def test_500_necessarios_200_em_estoque_sugerem_300(self):
        """O caminho da especificação, com os números dela."""
        self._op('1000')          # consome 500 kg de morango
        self._estoque('200')

        linha = self._linha()

        self.assertEqual(linha.necessario, Decimal('500.0000'))
        self.assertEqual(linha.livre, Decimal('200.000'))
        self.assertEqual(linha.deficit, Decimal('300.0000'))

    def test_a_requisicao_sai_com_a_quantidade_sugerida(self):
        self._op('1000')
        self._estoque('200')

        requisicao = CompraService.gerar_requisicao(
            self.filial, CompraService.necessidade(self.filial), self.usuario,
        )

        item = requisicao.itens.get()
        self.assertEqual(item.produto, self.morango)
        self.assertEqual(item.quantidade, Decimal('300.0000'))
        self.assertEqual(item.unidade, 'KG')

    def test_a_linha_guarda_os_numeros_que_a_explicam(self):
        """
        Para o comprador não ter de voltar ao PCP perguntar de onde saiu o
        pedido.
        """
        self._op('1000')
        self._estoque('200')

        requisicao = CompraService.gerar_requisicao(
            self.filial, CompraService.necessidade(self.filial), self.usuario,
        )

        item = requisicao.itens.get()
        self.assertEqual(item.necessario, Decimal('500.0000'))
        self.assertEqual(item.disponivel, Decimal('200.0000'))

    def test_estoque_cobrindo_tudo_nao_gera_requisicao(self):
        """
        Requisitar o que já existe faria compras negociar material que está no
        galpão — e o comprador para de confiar na lista inteira.
        """
        self._op('1000')
        self._estoque('900')

        with self.assertRaises(DomainError):
            CompraService.gerar_requisicao(
                self.filial, CompraService.necessidade(self.filial), self.usuario,
            )


class SomarAntesDeCompararTests(CompraBase):

    def test_tres_batidas_somam_a_necessidade(self):
        """
        Uma a uma, cada batida vê o estoque inteiro e parece coberta. Somadas,
        falta material.
        """
        self._estoque('900')
        for _ in range(3):
            self._op('1000')      # 500 kg cada = 1.500

        linha = self._linha()

        self.assertEqual(linha.necessario, Decimal('1500.0000'))
        self.assertEqual(linha.deficit, Decimal('600.0000'))

    def test_a_necessidade_por_ordem_veria_cada_uma_coberta(self):
        """
        A prova de que somar importa: pela conta por ordem, nenhuma das três
        acusa falta.
        """
        self._estoque('900')
        ordens = [self._op('1000') for _ in range(3)]

        for op in ordens:
            faltas = OrdemPolpaService.necessidade(op)['faltas']
            self.assertEqual(faltas, [])

        self.assertEqual(self._linha().deficit, Decimal('600.0000'))

    def test_uma_linha_por_insumo_e_nao_por_ordem(self):
        self._estoque('0')
        self._op('1000')
        self._op('1000')

        linhas = [
            l for l in CompraService.necessidade(self.filial)
            if l.produto.pk == self.morango.pk
        ]

        self.assertEqual(len(linhas), 1)
        self.assertEqual(len(linhas[0].ordens), 2)

    def test_ordem_produzida_nao_entra_na_conta(self):
        """Batida que já aconteceu não vai consumir mais nada."""
        self._estoque('5000')
        op = self._op('1000')
        OrdemPolpaService.mover(op, S.LIBERADA, self.usuario)
        OrdemPolpaService.mover(op, S.EM_PRODUCAO, self.usuario)
        OrdemPolpaService.concluir(op, self.usuario, quantidade=Decimal('1000'))

        linhas = CompraService.necessidade(self.filial)

        self.assertEqual(linhas, [])

    def test_ordem_cancelada_nao_entra_na_conta(self):
        self._estoque('0')
        op = self._op('1000')
        OrdemPolpaService.mover(
            op, S.CANCELADA, self.usuario, {'motivo': 'Sem fruta'},
        )

        self.assertEqual(CompraService.necessidade(self.filial), [])


class ReservaNaoAumentaODeficitTests(CompraBase):
    """O detalhe que inverte o sinal."""

    def test_o_que_estas_ordens_reservaram_volta_para_o_livre(self):
        """
        Reservar é a ação que RESOLVE o problema. Se ela aumentasse o déficit,
        o comprador compraria de novo o que já está separado no galpão.
        """
        self._estoque('500')
        op = self._op('1000')
        OrdemPolpaService.mover(op, S.LIBERADA, self.usuario)
        OrdemPolpaService.mover(op, S.EM_PRODUCAO, self.usuario)  # reserva 500

        linha = self._linha()

        self.assertEqual(linha.reservado_nosso, Decimal('500.0000'))
        self.assertEqual(linha.livre, Decimal('500.000'))
        self.assertEqual(linha.deficit, ZERO)

    def test_reserva_de_terceiros_reduz_o_livre(self):
        """
        Material separado para outra coisa não cobre esta produção — contá-lo
        seria prometer duas vezes o mesmo morango.
        """
        self._estoque('500', reservado='500')
        self._op('1000')

        linha = self._linha()

        self.assertEqual(linha.reservado_nosso, ZERO)
        self.assertEqual(linha.livre, ZERO)
        self.assertEqual(linha.deficit, Decimal('500.0000'))


class PedidoDeCompraTests(CompraBase):

    def _requisicao(self):
        self._op('1000')
        self._estoque('200')
        return CompraService.gerar_requisicao(
            self.filial, CompraService.necessidade(self.filial), self.usuario,
        )

    def test_a_requisicao_vira_pedido_de_compra(self):
        requisicao = self._requisicao()

        pedido = CompraService.gerar_pedido_compra(
            requisicao, self.fornecedor, self.usuario,
        )

        self.assertEqual(pedido.fornecedor, self.fornecedor)
        self.assertEqual(pedido.itens.count(), 1)
        self.assertEqual(pedido.itens.get().quantidade, Decimal('300.000'))

    def test_o_preco_e_ponto_de_partida_e_nao_zero(self):
        """
        Um pedido com valor zerado passa despercebido na aprovação — quem
        negocia é compras, mas partindo de um número.
        """
        requisicao = self._requisicao()

        pedido = CompraService.gerar_pedido_compra(
            requisicao, self.fornecedor, self.usuario,
        )

        self.assertEqual(pedido.itens.get().valor_unitario, Decimal('12.0000'))
        self.assertEqual(pedido.valor_total, Decimal('3600.00'))

    def test_a_requisicao_nao_vira_atendida_na_emissao(self):
        """
        Atendida é quando o material CHEGA. Marcar antes faria o PCP achar que
        tem morango no galpão enquanto ele ainda está na roça.
        """
        requisicao = self._requisicao()

        CompraService.gerar_pedido_compra(
            requisicao, self.fornecedor, self.usuario,
        )

        requisicao.refresh_from_db()
        self.assertEqual(requisicao.status, RequisicaoInsumo.Status.ABERTA)
        self.assertTrue(requisicao.virou_compra)

    def test_nao_gera_o_mesmo_pedido_duas_vezes(self):
        requisicao = self._requisicao()
        CompraService.gerar_pedido_compra(
            requisicao, self.fornecedor, self.usuario,
        )

        with self.assertRaises(DomainError):
            CompraService.gerar_pedido_compra(
                requisicao, self.fornecedor, self.usuario,
            )

        self.assertEqual(PedidoCompra.objects.count(), 1)

    def test_requisicao_cancelada_nao_vira_compra(self):
        requisicao = self._requisicao()
        requisicao.status = RequisicaoInsumo.Status.CANCELADA
        requisicao.save(update_fields=['status'])

        with self.assertRaises(DomainError):
            CompraService.gerar_pedido_compra(
                requisicao, self.fornecedor, self.usuario,
            )

    def test_o_pedido_diz_de_onde_veio(self):
        """Seis meses depois, alguém pergunta por que se comprou isso."""
        requisicao = self._requisicao()

        pedido = CompraService.gerar_pedido_compra(
            requisicao, self.fornecedor, self.usuario,
        )

        self.assertIn(f'#{requisicao.numero:04d}', pedido.observacao)
