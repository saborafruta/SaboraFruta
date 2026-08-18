"""
Operações sobre a grade do pedido.

Tudo que mexe em quantidade passa por aqui, porque toda alteração precisa
recalcular o total do item na mesma transação. Espalhar isso pelas views
deixaria um caminho sem recálculo — e é exatamente aí que a divergência
entre grade e total apareceria.
"""
from django.db import transaction
from django.db.models import Max, Sum

from apps.core.services.exceptions import DadosInvalidosError

from ..models import ItemGradePedido, ItemPedidoProducao, Tamanho


class GradePedidoService:

    # ── Recálculo ────────────────────────────────────────────────────────

    @staticmethod
    def _sincronizar_total(item) -> int:
        """
        Alinha `item.quantidade` com a soma da grade.

        Item sem grade nenhuma mantém a quantidade digitada — é o caso de
        quem pede "50 camisetas" sem detalhar tamanho ainda. A regra de não
        divergir vale quando existe grade para divergir dela.
        """
        agregado = item.grade.aggregate(total=Sum('quantidade'))
        if not item.grade.exists():
            return item.quantidade
        total = agregado['total'] or 0
        if item.quantidade != total:
            item.quantidade = total
            item.save(update_fields=['quantidade'])
        return total

    @classmethod
    def recalcular_pedido(cls, pedido) -> int:
        """Ressincroniza todos os itens e devolve o total geral do pedido."""
        total = 0
        for item in pedido.itens.prefetch_related('grade'):
            total += cls._sincronizar_total(item)
        return total

    # ── Edição ───────────────────────────────────────────────────────────

    @classmethod
    @transaction.atomic
    def salvar_quantidades(cls, pedido, quantidades: dict) -> int:
        """
        Grava a tabela inteira de uma vez.

        `quantidades` chega como {(item_id, tamanho_id): qtd}. Salvar tudo
        junto, e não célula a célula, evita o estado intermediário em que o
        total do pedido não corresponde a nenhuma versão da tabela.
        """
        itens = {i.id: i for i in pedido.itens.all()}
        for (item_id, tamanho_id), qtd in quantidades.items():
            if item_id not in itens:
                continue
            if qtd < 0:
                raise DadosInvalidosError('Quantidade não pode ser negativa.')
            ItemGradePedido.objects.update_or_create(
                item_id=item_id, tamanho_id=tamanho_id,
                defaults={'quantidade': qtd},
            )
        return cls.recalcular_pedido(pedido)

    @classmethod
    @transaction.atomic
    def adicionar_tamanho(cls, pedido, tamanho) -> int:
        """
        Acrescenta uma coluna à tabela.

        Cria a célula em TODOS os itens, com zero. Sem isso a tabela ficaria
        irregular — um item com a coluna e outro sem, o que não é uma tabela.
        """
        criadas = 0
        for item in pedido.itens.all():
            _, criou = ItemGradePedido.objects.get_or_create(
                item=item, tamanho=tamanho, defaults={'quantidade': 0},
            )
            criadas += int(criou)
        return criadas

    @classmethod
    @transaction.atomic
    def remover_tamanho(cls, pedido, tamanho) -> int:
        """Remove a coluna do pedido inteiro e ressincroniza os totais."""
        apagadas, _ = ItemGradePedido.objects.filter(
            item__pedido=pedido, tamanho=tamanho,
        ).delete()
        cls.recalcular_pedido(pedido)
        return apagadas

    @classmethod
    @transaction.atomic
    def aplicar_grade_do_produto(cls, item) -> int:
        """
        Monta as colunas a partir da grade cadastrada do produto.

        Atalho para o caso comum: o produto já tem grade Adulto, então a
        tabela nasce com PP..XGG zerados em vez de o usuário adicionar
        tamanho por tamanho.
        """
        produto = item.produto
        if produto is None or produto.grade_id is None:
            raise DadosInvalidosError(
                'Este item não tem produto de catálogo com grade definida. '
                'Adicione os tamanhos manualmente.'
            )
        criadas = 0
        for tamanho in produto.grade.tamanhos_ordenados():
            _, criou = ItemGradePedido.objects.get_or_create(
                item=item, tamanho=tamanho, defaults={'quantidade': 0},
            )
            criadas += int(criou)
        return criadas

    # ── Cópia e duplicação ───────────────────────────────────────────────

    @classmethod
    @transaction.atomic
    def copiar_grade(cls, origem, destino) -> int:
        """
        Copia as quantidades de um item para outro.

        Caso real: camisa e calção do mesmo conjunto saem na mesma grade.
        Sobrescreve o destino inteiro — copiar pela metade deixaria uma
        mistura das duas grades, que não é nem uma nem outra.
        """
        if origem.pedido_id != destino.pedido_id:
            raise DadosInvalidosError('Só dá para copiar entre itens do mesmo pedido.')
        if origem.pk == destino.pk:
            raise DadosInvalidosError('Origem e destino são o mesmo item.')

        destino.grade.all().delete()
        ItemGradePedido.objects.bulk_create([
            ItemGradePedido(item=destino, tamanho_id=g.tamanho_id, quantidade=g.quantidade)
            for g in origem.grade.all()
        ])
        cls._sincronizar_total(destino)
        return destino.grade.count()

    @classmethod
    @transaction.atomic
    def duplicar_item(cls, item):
        """
        Clona o item com as especificações e a grade.

        Não clona arte nem visual: são arquivos que quase sempre mudam entre
        peças, e copiá-los faria o usuário apagar dois anexos errados a cada
        duplicação.
        """
        ultima = item.pedido.itens.aggregate(m=Max('ordem'))['m'] or 0

        copia = ItemPedidoProducao.objects.create(
            pedido=item.pedido,
            produto=item.produto,
            descricao=item.descricao,
            referencia=item.referencia,
            modelo=item.modelo,
            cor=item.cor,
            tecido=item.tecido,
            gola=item.gola,
            manga=item.manga,
            acabamento=item.acabamento,
            quantidade=item.quantidade,
            observacoes=item.observacoes,
            ordem=ultima + 10,
        )
        ItemGradePedido.objects.bulk_create([
            ItemGradePedido(item=copia, tamanho_id=g.tamanho_id, quantidade=g.quantidade)
            for g in item.grade.all()
        ])
        cls._sincronizar_total(copia)
        return copia

    # ── Leitura para a tela ──────────────────────────────────────────────

    @staticmethod
    def montar_tabela(pedido) -> dict:
        """
        A tabela pronta para o template: colunas, linhas e os três totais.

        As colunas são os tamanhos que aparecem em qualquer item do pedido,
        na ordem da grade. Montar aqui evita o template ter de cruzar item ×
        tamanho, que o Django template não faz sem filtro customizado.
        """
        itens = list(
            pedido.itens.prefetch_related('grade__tamanho').select_related('produto')
        )

        colunas: list[Tamanho] = []
        vistos = set()
        for item in itens:
            for celula in item.grade.all():
                if celula.tamanho_id not in vistos:
                    vistos.add(celula.tamanho_id)
                    colunas.append(celula.tamanho)
        colunas.sort(key=lambda t: (t.ordem, t.sigla))

        linhas = []
        total_por_tamanho = {t.id: 0 for t in colunas}
        total_geral = 0

        for item in itens:
            por_tamanho = {g.tamanho_id: g.quantidade for g in item.grade.all()}
            celulas = []
            soma_linha = 0
            for t in colunas:
                qtd = por_tamanho.get(t.id)
                celulas.append({'tamanho': t, 'quantidade': qtd, 'existe': qtd is not None})
                if qtd:
                    soma_linha += qtd
                    total_por_tamanho[t.id] += qtd
            linhas.append({'item': item, 'celulas': celulas, 'total': soma_linha})
            total_geral += soma_linha

        return {
            'colunas': colunas,
            'linhas': linhas,
            'totais_tamanho': [
                {'tamanho': t, 'total': total_por_tamanho[t.id]} for t in colunas
            ],
            'total_geral': total_geral,
        }
