"""
Subprodutos e resíduos: registrar, creditar no estoque e somar o resultado.

O REGISTRO SOZINHO SERIA UM DIÁRIO. Anotar "500 kg de casca, reaproveitamento"
não muda nada no sistema: o almoxarifado continua sem saber que a casca
existe, ninguém a consome de lugar nenhum, e no mês seguinte se compra ração
que já estava no pátio. Por isso reaproveitamento e uso interno DÃO ENTRADA no
estoque quando há produto cadastrado.

VENDA E DOAÇÃO NÃO ENTRAM. O material saiu da casa no mesmo ato; creditar e
depois baixar seria inventar um saldo que nunca existiu, e por um instante o
estoque diria ter uma coisa que já está no caminhão do comprador.

DESCARTE TAMBÉM NÃO. Ele deixou de ser material e virou despesa de destinação
-- e é assim que ele precisa aparecer no custo, não como um saldo a zerar
depois.

O QUE O SUBPRODUTO NÃO FAZ é somar à perda. O peso dele já está dentro da
perda da etapa (entrada menos saída); esta tabela diz o que aquele peso ERA e
para onde foi. Somar seria contar a mesma casca duas vezes.
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.services.exceptions import DomainError
from apps.polpa.models import OrdemPolpa, Subproduto

ZERO = Decimal('0')
D = Subproduto.Destino


class SubprodutoService:

    # ── Registro ─────────────────────────────────────────────────────────

    @classmethod
    @transaction.atomic
    def registrar(cls, op: OrdemPolpa, dados: dict, usuario=None) -> Subproduto:
        """
        Grava o subproduto e, quando cabe, credita o estoque.

        A QUANTIDADE PRECISA SER POSITIVA: um subproduto de zero quilo não é
        um registro de que não houve casca — é uma linha que só polui a lista
        e distorce a contagem de "quantos destinos tivemos".
        """
        quantidade = cls._decimal(dados.get('quantidade'))
        if quantidade is None or quantidade <= ZERO:
            raise DomainError('Informe quanto de subproduto saiu.')

        destino = dados.get('destino')
        if destino not in D.values:
            raise DomainError('Escolha o destino do subproduto.')

        tipo = dados.get('tipo')
        if tipo not in Subproduto.Tipo.values:
            raise DomainError('Escolha o tipo do subproduto.')

        etapa = None
        if dados.get('etapa'):
            etapa = op.etapas_processo.filter(pk=dados['etapa']).first()

        subproduto = Subproduto.objects.create(
            filial=op.filial,
            ordem=op,
            etapa=etapa,
            tipo=tipo,
            descricao=(dados.get('descricao') or '').strip()[:120],
            quantidade=quantidade,
            unidade=(dados.get('unidade') or 'kg').strip()[:6],
            destino=destino,
            destinatario=(dados.get('destinatario') or '').strip()[:120],
            valor_recebido=cls._dinheiro(dados.get('valor_recebido'), destino, D.VENDA),
            custo_destinacao=cls._dinheiro(
                dados.get('custo_destinacao'), destino, D.DESCARTE, D.DOACAO,
            ),
            produto_estoque_id=dados.get('produto_estoque') or None,
            data=dados.get('data') or timezone.localdate(),
            observacao=(dados.get('observacao') or '').strip(),
            criado_por=usuario,
        )
        cls.creditar_estoque(subproduto, usuario)
        return subproduto

    @staticmethod
    def _dinheiro(bruto, destino, *destinos_validos) -> Decimal:
        """
        Valor só onde ele faz sentido.

        Guardar o que foi digitado num campo que o destino não usa deixaria um
        número invisível: alguém preenche "valor recebido", muda o destino
        para descarte, e o relatório soma uma venda que não houve.
        """
        if destino not in destinos_validos:
            return ZERO
        valor = SubprodutoService._decimal(bruto)
        return valor if valor and valor > ZERO else ZERO

    @staticmethod
    def _decimal(bruto):
        if bruto in (None, ''):
            return None
        try:
            return Decimal(str(bruto).replace(',', '.'))
        except (ArithmeticError, ValueError):
            return None

    # ── Entrada no estoque ───────────────────────────────────────────────

    @classmethod
    def creditar_estoque(cls, subproduto: Subproduto, usuario=None) -> bool:
        """
        Põe no estoque o que continua na casa. Devolve se creditou.

        Idempotente pelo carimbo: registrar de novo não duplica a casca.

        NÃO ESTOURA. Roda dentro do registro, e um problema de estoque não
        pode impedir a fábrica de anotar o que saiu da linha — o material
        existe no pátio de qualquer jeito, e o que se perde ao travar é o
        registro dele. Fica sem carimbo, e a tela mostra a pendência.
        """
        import logging

        from apps.estoque.models.estoque import MovimentacaoEstoque
        from apps.estoque.services.movimentacao_service import MovimentacaoService

        if not subproduto.pendente_de_credito:
            return False
        if usuario is None and subproduto.criado_por_id is None:
            return False

        try:
            MovimentacaoService.registrar_movimentacao(
                produto_id=subproduto.produto_estoque_id,
                filial_id=subproduto.filial_id,
                tipo_operacao=MovimentacaoEstoque.TipoOperacao.PRODUCAO_ENTRADA,
                quantidade=subproduto.quantidade,
                usuario_id=(usuario.pk if usuario else subproduto.criado_por_id),
                documento_tipo=MovimentacaoEstoque.DocumentoTipo.ORDEM_PRODUCAO,
                documento_id=subproduto.ordem.ordem_id,
                documento_numero=subproduto.ordem.numero,
                observacao=(
                    f'Subproduto: {subproduto.rotulo} '
                    f'({subproduto.get_destino_display()})'
                ),
                permitir_sem_lote=True,
            )
        except Exception:  # noqa: BLE001
            logging.getLogger(__name__).exception(
                'Falha ao creditar o subproduto %s no estoque', subproduto.pk,
            )
            return False

        subproduto.estoque_creditado_em = timezone.now()
        subproduto.save(update_fields=['estoque_creditado_em'])
        return True

    # ── Leitura ──────────────────────────────────────────────────────────

    @classmethod
    def resumo(cls, op: OrdemPolpa) -> dict:
        """
        O que a batida gerou além do produto, e o que isso deu de resultado.

        `explica_da_perda` compara o peso dos subprodutos com a perda medida
        nas etapas. Não é uma validação -- é a pergunta que a fábrica faz
        olhando os dois números: sobrou perda sem nome? Então há material
        saindo da linha que ninguém sabe para onde foi.
        """
        from apps.polpa.services.processo import ProcessoService

        itens = list(
            op.subprodutos.select_related('etapa', 'produto_estoque')
        )
        por_destino = {}
        for item in itens:
            registro = por_destino.setdefault(item.destino, {
                'destino': item.destino,
                'rotulo': item.get_destino_display(),
                'quantidade': ZERO,
                'resultado': ZERO,
                'itens': 0,
            })
            registro['quantidade'] += item.quantidade
            registro['resultado'] += item.resultado
            registro['itens'] += 1

        total = sum((i.quantidade for i in itens), ZERO)
        perda_medida = ProcessoService.resumo(op).get('perda_total')

        return {
            'itens': itens,
            'total': total,
            'resultado': sum((i.resultado for i in itens), ZERO),
            'recebido': sum((i.valor_recebido or ZERO for i in itens), ZERO),
            'custo': sum((i.custo_destinacao or ZERO for i in itens), ZERO),
            # Na ordem do enum, que é a do valor que cada destino devolve.
            'por_destino': [
                por_destino[d] for d in D.values if d in por_destino
            ],
            'perda_medida': perda_medida,
            'perda_sem_nome': (
                max(perda_medida - total, ZERO)
                if perda_medida is not None else None
            ),
            'pendentes_de_credito': [i for i in itens if i.pendente_de_credito],
        }
