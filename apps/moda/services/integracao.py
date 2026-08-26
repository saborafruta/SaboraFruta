"""
As duas pontes que faltavam entre o vertical e o resto do ERP.

O PRINCÍPIO É "DIGITAR UMA VEZ", e ele já valia da engenharia ao financeiro:
a ficha alimenta o custo, a OP lê o pedido, o WIP lê o fluxo, o dashboard lê
tudo. Faltavam dois pedaços onde a informação MORRIA e alguém precisava
redigitar do outro lado:

  1. COMPRAS — a requisição de material dizia o que falta e parava ali. O
     comprador abria o pedido de compra e redigitava linha por linha, com o
     risco de trocar quantidade ou produto no caminho.

  2. ESTOQUE — o corte gravava o consumo real do tecido e o saldo do estoque
     continuava o mesmo. Alguém tinha que dar baixa à mão, e até dar, o
     sistema achava que havia 200 m de um rolo que já virou peça.

O SEGUNDO É PIOR QUE REDIGITAÇÃO: é número errado. O painel de necessidade
lê esse saldo, e um saldo alto demais esconde a falta de material até o
corte parar.

A BAIXA ERA UM BOTÃO, e passou a ser automática ao marcar o corte como
cortado. A razão original — mexer no estoque de outro módulo é efeito
colateral grande para acontecer sem alguém mandar — perdia para a prática: o
botão era esquecido, e entre o corte e o clique o sistema achava que havia
200 m de um rolo que já tinha virado peça. Um saldo errado por horas custa
mais que o efeito colateral, porque o painel de necessidade lê esse saldo.

O botão continua existindo, para o que o automático não consegue: corte
marcado como cortado ANTES de alguém digitar o consumo real, ou tecido ainda
não ligado a um produto de estoque. Nesses casos a marcação passa e a baixa
fica pendente, avisando — travar a marcação puniria o chão de fábrica por um
cadastro que não é dele.

E continua reversível: o estorno devolve aos MESMOS lotes.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.services.exceptions import DomainError

logger = logging.getLogger(__name__)

ZERO = Decimal('0')


@dataclass
class BaixaDoCorte:
    """
    O que a baixa (ou o estorno) fez, com os lotes à vista.

    Devolve objeto, e não a tupla `(produto, quantidade)` de antes, porque
    agora há o que contar: de quais rolos saiu cada pedaço, e se sobrou consumo
    sem lote. O aviso é texto pronto para a tela — quem chama não remonta a
    frase a partir dos números, e todas as telas dizem a mesma coisa.
    """
    produto: object
    quantidade: Decimal
    consumos: list = field(default_factory=list)
    aviso: str = ''

    @property
    def rastreados(self) -> list:
        return [c for c in self.consumos if c.lote_id]


class IntegracaoService:

    # ── Requisição → Pedido de compra ────────────────────────────────────

    @classmethod
    @transaction.atomic
    def gerar_pedido_compra(cls, requisicao, fornecedor, usuario):
        """
        Transforma a requisição num pedido de compra de verdade.

        Só entram linhas LIGADAS a um produto de estoque: comprar exige um
        produto cadastrado, e material da ficha ainda sem vínculo não tem o
        que virar item. As linhas soltas ficam na requisição, visíveis, em
        vez de sumirem num pedido incompleto.

        O preço vem do custo do produto e é um PONTO DE PARTIDA — quem
        negocia é compras, e é lá que o valor final é acertado. Deixar zero
        seria pior: um pedido com valor zerado passa despercebido na
        aprovação.
        """
        from apps.compras.models import ItemPedidoCompra, PedidoCompra

        if requisicao.pedido_compra_id:
            raise DomainError(
                f'Esta requisição já virou o pedido de compra '
                f'{requisicao.pedido_compra.numero_pedido}.'
            )
        if requisicao.status == requisicao.Status.CANCELADA:
            raise DomainError('Requisição cancelada não gera pedido de compra.')

        linhas = [i for i in requisicao.itens.all() if i.produto_id]
        if not linhas:
            raise DomainError(
                'Nenhum item desta requisição está ligado a um produto de '
                'estoque. Ligue o material à ficha técnica antes de comprar.'
            )

        pedido = PedidoCompra.objects.create(
            filial=requisicao.filial,
            fornecedor=fornecedor,
            usuario=usuario,
            numero_pedido=cls._proximo_numero_compra(requisicao.filial),
            data_emissao=timezone.now(),
            status=PedidoCompra.Status.RASCUNHO,
            # `observacao`, singular: o campo do modelo. Com o plural o
            # `PedidoCompra()` recusava o kwarg e o botao estourava em
            # TypeError -- caminho sem teste, entao ninguem tinha visto.
            observacao=(
                f'Gerado da requisição de material #{requisicao.numero:04d} '
                f'do vertical Moda.'
            ),
        )

        itens = []
        for indice, linha in enumerate(linhas, start=1):
            unitario = (
                linha.produto.preco_custo_medio
                or linha.produto.preco_custo
                or ZERO
            )
            item = ItemPedidoCompra(
                pedido=pedido,
                produto=linha.produto,
                numero_item=indice,
                quantidade=linha.quantidade,
                valor_unitario=unitario,
                valor_bruto=ZERO,
                valor_total=ZERO,
                observacao=linha.observacao[:255],
            )
            item.calcular_totais()
            itens.append(item)

        ItemPedidoCompra.objects.bulk_create(itens)

        pedido.valor_produtos = sum((i.valor_total for i in itens), ZERO)
        pedido.valor_total = pedido.valor_produtos
        pedido.save(update_fields=['valor_produtos', 'valor_total'])

        requisicao.pedido_compra = pedido
        # A requisição NÃO vira "atendida" aqui.
        # Atendida é quando o material CHEGA: marcar na emissão faria o PCP
        # achar que o tecido está na prateleira porque alguém mandou comprar.
        requisicao.save(update_fields=['pedido_compra'])

        return pedido, len(linhas), requisicao.itens.count() - len(linhas)

    @staticmethod
    def _proximo_numero_compra(filial) -> str:
        from apps.compras.models import PedidoCompra

        ano = timezone.localdate().year
        prefixo = f'MOD{ano}'
        # `objects`: `PedidoCompra` nao declara `all_objects`, e a chamada
        # estourava AttributeError -- o segundo defeito deste caminho, que
        # nunca foi exercitado por teste nem por clique.
        ultimo = (
            PedidoCompra.objects
            .filter(filial=filial, numero_pedido__startswith=prefixo)
            .order_by('-numero_pedido')
            .values_list('numero_pedido', flat=True)
            .first()
        )
        sequencial = 1
        if ultimo:
            try:
                sequencial = int(ultimo[len(prefixo):]) + 1
            except ValueError:
                sequencial = 1
        return f'{prefixo}{sequencial:05d}'

    # ── Corte → baixa de estoque ─────────────────────────────────────────

    @classmethod
    def material_do_corte(cls, corte):
        """
        Qual produto de estoque este corte consome, e quanto.

        O tecido sai da FICHA TÉCNICA do produto, e não de um campo do
        corte: é o mesmo material que a necessidade calculou e que a
        requisição comprou. Ler de outro lugar aqui abriria a porta para o
        corte baixar um produto e o PCP ter reservado outro.
        """
        from apps.moda.models import MaterialFicha

        ficha = corte.ordem.ficha
        if ficha is None:
            return None, ZERO, 'Produto sem ficha técnica cadastrada.'

        material = (
            ficha.materiais
            .filter(tipo=MaterialFicha.Tipo.TECIDO_PRINCIPAL)
            .exclude(produto_estoque__isnull=True)
            .first()
        )
        if material is None:
            return None, ZERO, (
                'O tecido principal da ficha não está ligado a um produto de '
                'estoque.'
            )

        quantidade = corte.consumo_real or ZERO
        if quantidade <= 0:
            return material.produto_estoque, ZERO, (
                'O consumo real do corte ainda não foi informado.'
            )
        return material.produto_estoque, quantidade, ''

    @classmethod
    @transaction.atomic
    def baixar_estoque_do_corte(cls, corte, usuario) -> BaixaDoCorte:
        """
        Dá baixa no tecido que o corte consumiu, POR FEFO.

        Antes chamava `registrar_movimentacao(permitir_sem_lote=True)`: o saldo
        do produto caía e nenhum lote era tocado. Num tecido com lote,
        `Estoque.quantidade_atual` descia e `LoteProduto.quantidade_atual`
        ficava cheio — os dois números divergiam em silêncio, e quem fosse
        rastrear um defeito de tecido não tinha por onde começar.

        Agora a seleção é a mesma do resto do ERP (`selecionar_lotes_fifo`):
        vence primeiro, sai primeiro. Um corte pode comer vários rolos, então
        sai um lançamento por lote — e cada pedaço fica ligado ao rolo de onde
        veio.

        O QUE FALTAR NOS LOTES ainda é registrado, sem lote e com aviso. O
        tecido JÁ FOI CORTADO no mundo físico: recusar o lançamento não devolve
        o rolo, só deixa o sistema mais errado do que já estava. O aviso sobe
        para a tela para alguém acertar o cadastro do lote.

        Idempotente pelo carimbo: dois cliques não tiram o tecido duas vezes.
        """
        from apps.estoque.models.estoque import MovimentacaoEstoque
        from apps.estoque.services.movimentacao_service import MovimentacaoService
        from apps.moda.models import ConsumoLoteCorte

        if corte.estoque_baixado_em:
            raise DomainError('O estoque deste corte já foi baixado.')
        if corte.status != corte.Status.CORTADO:
            raise DomainError(
                'Só o corte concluído dá baixa: marcar como cortado é o que '
                'diz que o tecido saiu do rolo.'
            )

        produto, quantidade, problema = cls.material_do_corte(corte)
        if problema:
            raise DomainError(problema)

        consumos = MovimentacaoService.selecionar_lotes_fifo(
            produto.pk, corte.filial_id, quantidade, permitir_parcial=True,
        )
        coberto = sum((c.quantidade for c in consumos), ZERO)
        sem_lote = quantidade - coberto

        comum = {
            'produto_id': produto.pk,
            'filial_id': corte.filial_id,
            'tipo_operacao': MovimentacaoEstoque.TipoOperacao.PRODUCAO_SAIDA,
            'usuario_id': usuario.pk,
            'documento_tipo': MovimentacaoEstoque.DocumentoTipo.ORDEM_PRODUCAO,
            'documento_id': corte.ordem_id,
            'documento_numero': corte.ordem.numero,
        }

        for consumo in consumos:
            MovimentacaoService.registrar_movimentacao(
                quantidade=consumo.quantidade,
                lote_id=consumo.lote_id,
                valor_unitario=consumo.custo_unitario,
                observacao=(
                    f'Corte #{corte.numero:04d} — FEFO: lote {consumo.numero_lote}'
                ),
                **comum,
            )
            ConsumoLoteCorte.objects.create(
                corte=corte, lote_id=consumo.lote_id,
                quantidade=consumo.quantidade,
                custo_unitario=consumo.custo_unitario,
            )

        aviso = ''
        if sem_lote > ZERO:
            MovimentacaoService.registrar_movimentacao(
                quantidade=sem_lote,
                observacao=(
                    f'Corte #{corte.numero:04d} — consumo sem lote: os lotes '
                    f'vigentes não cobriram esta quantidade'
                ),
                permitir_sem_lote=True,
                **comum,
            )
            ConsumoLoteCorte.objects.create(
                corte=corte, lote=None, quantidade=sem_lote,
            )
            if consumos:
                aviso = (
                    f'{sem_lote} saiu SEM LOTE — os lotes vigentes cobriram '
                    f'apenas {coberto} de {quantidade}. Confira o cadastro de '
                    f'lotes deste tecido.'
                )
            else:
                aviso = (
                    f'{quantidade} saiu SEM LOTE: este tecido não tem lote '
                    f'vigente cadastrado, então não há o que rastrear.'
                )

        corte.estoque_baixado_em = timezone.now()
        corte.save(update_fields=['estoque_baixado_em'])
        return BaixaDoCorte(
            produto=produto,
            quantidade=quantidade,
            consumos=list(corte.consumos_lote.select_related('lote')),
            aviso=aviso,
        )

    @classmethod
    def baixar_ao_cortar(cls, corte, usuario) -> BaixaDoCorte | None:
        """
        A baixa automática, disparada ao marcar o corte como cortado.

        NÃO ESTOURA E NÃO TRAVA A MARCAÇÃO. Se o consumo real ainda não foi
        digitado, ou se o tecido não está ligado a um produto de estoque, a
        marcação passa e a baixa fica pendente — o botão continua na tela para
        quando o cadastro estiver resolvido. Travar o "cortado" por causa
        disso puniria o chão de fábrica por um cadastro que não é dele, e o
        registro do que aconteceu na mesa é mais valioso que o saldo em dia.

        Devolve `None` quando não havia o que baixar, para quem chama saber a
        diferença entre "baixou" e "não deu" sem inspecionar exceção.
        """
        if corte.estoque_baixado_em:
            return None
        if corte.status != corte.Status.CORTADO:
            return None

        _produto, _quantidade, problema = cls.material_do_corte(corte)
        if problema:
            return None

        try:
            return cls.baixar_estoque_do_corte(corte, usuario)
        except DomainError:
            logger.exception(
                'Falha na baixa automática do corte %s', corte.pk,
            )
            return None

    @classmethod
    @transaction.atomic
    def estornar_estoque_do_corte(cls, corte, usuario) -> BaixaDoCorte:
        """
        Devolve ao estoque o que a baixa tirou, PARA OS MESMOS LOTES.

        Voltar por FEFO seria errado no sentido contrário: FEFO escolhe de onde
        TIRAR, e devolver pelo mesmo critério jogaria o tecido no rolo que
        vence primeiro — que raramente é de onde ele saiu. Em duas rodadas de
        baixa e estorno o saldo total fecharia e os lotes estariam todos
        trocados, com cara de certo.

        Por isso a devolução lê o que ESTE corte alocou. Não dá para ler do
        razão: ele é indexado pelo DOCUMENTO, e uma ordem pode ter vários
        enfestos — estornar um devolveria tecido dos outros.

        Existe porque corte é cancelado de verdade: enfesto errado, tecido
        trocado. Sem estorno, a saída seria um ajuste manual — exatamente a
        redigitação que este trabalho veio eliminar.
        """
        from apps.estoque.models.estoque import MovimentacaoEstoque
        from apps.estoque.services.movimentacao_service import MovimentacaoService

        if not corte.estoque_baixado_em:
            raise DomainError('Este corte não tem baixa de estoque para estornar.')

        produto, quantidade, problema = cls.material_do_corte(corte)
        if problema:
            raise DomainError(problema)

        alocados = list(corte.consumos_lote.select_related('lote'))
        comum = {
            'produto_id': produto.pk,
            'filial_id': corte.filial_id,
            'tipo_operacao': MovimentacaoEstoque.TipoOperacao.PRODUCAO_ENTRADA,
            'usuario_id': usuario.pk,
            'documento_tipo': MovimentacaoEstoque.DocumentoTipo.ORDEM_PRODUCAO,
            'documento_id': corte.ordem_id,
            'documento_numero': corte.ordem.numero,
        }

        devolvido = ZERO
        for alocado in alocados:
            onde = alocado.lote.numero_lote if alocado.lote_id else 'sem lote'
            MovimentacaoService.registrar_movimentacao(
                quantidade=alocado.quantidade,
                lote_id=alocado.lote_id,
                valor_unitario=alocado.custo_unitario,
                observacao=(
                    f'Estorno do corte #{corte.numero:04d} — devolvido a {onde}'
                ),
                permitir_sem_lote=alocado.lote_id is None,
                **comum,
            )
            devolvido += alocado.quantidade

        if not alocados:
            # Baixa feita ANTES desta mudança não tem alocação gravada.
            # Devolver o total sem lote é o único caminho honesto: o sistema
            # nunca soube de que rolo aquilo saiu, e escolher um agora poria
            # tecido no lugar errado com cara de rastreio.
            MovimentacaoService.registrar_movimentacao(
                quantidade=quantidade,
                observacao=(
                    f'Estorno do corte #{corte.numero:04d} — baixa antiga, '
                    f'sem lote registrado'
                ),
                permitir_sem_lote=True,
                **comum,
            )
            devolvido = quantidade

        corte.consumos_lote.all().delete()
        corte.estoque_baixado_em = None
        corte.save(update_fields=['estoque_baixado_em'])
        return BaixaDoCorte(
            produto=produto, quantidade=devolvido, consumos=[], aviso='',
        )
