"""Leitura comercial de produtos com estoque, mas sem venda recente.

Este relatorio e deliberadamente diferente do alerta operacional de
``ultima_saida``: transferencia, perda, producao e ajuste nao contam como
giro comercial. Somente vendas concluidas em PDV ou pedidos B2B faturados
reiniciam o contador.
"""
from __future__ import annotations

from decimal import Decimal

from django.db.models import DecimalField, ExpressionWrapper, F, Max, Sum
from django.utils import timezone

from apps.estoque.models import Estoque
from apps.pdv.models import ItemVendaPDV
from apps.vendas.models import ItemPedidoVenda, PedidoVenda


class ProdutosSemGiroService:
    DIAS_MINIMOS = 60
    DIAS_CRITICOS = 90

    STATUS_B2B_COM_VENDA = (
        PedidoVenda.Status.FATURADO,
        PedidoVenda.Status.PARCIALMENTE_FATURADO,
        PedidoVenda.Status.ENTREGUE,
    )
    TIPOS_B2B_COM_VENDA = (
        PedidoVenda.Tipo.PEDIDO,
        PedidoVenda.Tipo.VENDA_BALCAO,
    )

    @classmethod
    def analisar(cls, filial, *, dias_minimos: int | None = None) -> list[dict]:
        """Retorna produtos ativos com saldo positivo e sem venda no corte.

        O saldo e consolidado entre os depositos da filial. O resultado inclui
        produtos nunca vendidos, que sao classificados separadamente.
        """
        if not filial:
            return []

        dias_minimos = cls.DIAS_MINIMOS if dias_minimos is None else max(0, int(dias_minimos))
        hoje = timezone.localdate()
        campo_valor = DecimalField(max_digits=20, decimal_places=4)
        valor_custo = ExpressionWrapper(
            F('quantidade_atual') * F('custo_medio'),
            output_field=campo_valor,
        )

        saldos = list(
            Estoque.objects.filter(
                filial=filial,
                produto__ativo=True,
                produto__filiais_vinculo__filial=filial,
                produto__filiais_vinculo__ativo=True,
            )
            .values(
                'produto_id',
                'produto__codigo',
                'produto__descricao',
                'produto__categoria__nome',
                'produto__unidade_medida__sigla',
            )
            .annotate(
                estoque_atual=Sum('quantidade_atual'),
                valor_custo=Sum(valor_custo),
            )
            .filter(estoque_atual__gt=0)
        )
        if not saldos:
            return []

        produto_ids = [item['produto_id'] for item in saldos]
        b2b = {
            item['produto_id']: item['ultima_venda']
            for item in (
                ItemPedidoVenda.objects.filter(
                    produto_id__in=produto_ids,
                    pedido__filial=filial,
                    pedido__status__in=cls.STATUS_B2B_COM_VENDA,
                    pedido__tipo__in=cls.TIPOS_B2B_COM_VENDA,
                )
                .values('produto_id')
                .annotate(ultima_venda=Max('pedido__data_emissao'))
            )
        }
        pdv = {
            item['produto_id']: item['ultima_venda']
            for item in (
                ItemVendaPDV.objects.filter(
                    produto_id__in=produto_ids,
                    venda_pdv__filial=filial,
                    venda_pdv__status='finalizada',
                    venda_pdv__bonificacao=False,
                )
                .values('produto_id')
                .annotate(ultima_venda=Max('venda_pdv__data_venda'))
            )
        }

        resultado = []
        for saldo in saldos:
            produto_id = saldo['produto_id']
            venda_b2b = b2b.get(produto_id)
            venda_pdv = pdv.get(produto_id)
            datas = [data for data in (venda_b2b, venda_pdv) if data is not None]
            ultima_venda = max(datas) if datas else None
            dias_sem_venda = None
            if ultima_venda is not None:
                data_local = timezone.localtime(ultima_venda).date() if timezone.is_aware(ultima_venda) else ultima_venda.date()
                dias_sem_venda = (hoje - data_local).days
                if dias_sem_venda < dias_minimos:
                    continue

            if ultima_venda is None:
                faixa = 'nunca'
                faixa_label = 'Nunca vendido'
            elif dias_sem_venda >= cls.DIAS_CRITICOS:
                faixa = '90_mais'
                faixa_label = '90 dias ou mais'
            else:
                faixa = '60_89'
                faixa_label = '60 a 89 dias'

            resultado.append({
                'produto_id': produto_id,
                'codigo': saldo['produto__codigo'] or '',
                'descricao': saldo['produto__descricao'],
                'categoria': saldo['produto__categoria__nome'] or 'Sem categoria',
                'unidade': saldo['produto__unidade_medida__sigla'],
                'estoque_atual': saldo['estoque_atual'] or Decimal('0'),
                'valor_custo': saldo['valor_custo'] or Decimal('0'),
                'ultima_venda': ultima_venda,
                'dias_sem_venda': dias_sem_venda,
                'faixa': faixa,
                'faixa_label': faixa_label,
                'canal_ultima_venda': (
                    'PDV' if venda_pdv and (not venda_b2b or venda_pdv >= venda_b2b)
                    else ('Pedido B2B' if venda_b2b else '—')
                ),
            })

        # Nunca vendidos primeiro; depois, os que estao ha mais tempo parados.
        resultado.sort(key=lambda item: (
            0 if item['ultima_venda'] is None else 1,
            item['ultima_venda'] or timezone.now(),
            item['descricao'].casefold(),
        ))
        return resultado

    @classmethod
    def resumo(cls, itens: list[dict]) -> dict:
        return {
            'total': len(itens),
            'dias_60_89': sum(1 for item in itens if item['faixa'] == '60_89'),
            'dias_90_mais': sum(1 for item in itens if item['faixa'] == '90_mais'),
            'nunca': sum(1 for item in itens if item['faixa'] == 'nunca'),
            'estoque_atual': sum((item['estoque_atual'] for item in itens), Decimal('0')),
            'valor_custo': sum((item['valor_custo'] for item in itens), Decimal('0')),
        }

    @staticmethod
    def filtrar(itens: list[dict], *, faixa: str = '', busca: str = '') -> list[dict]:
        faixa = faixa if faixa in {'60_89', '90_mais', 'nunca'} else ''
        busca_normalizada = (busca or '').strip().casefold()
        resultado = itens
        if faixa:
            resultado = [item for item in resultado if item['faixa'] == faixa]
        if busca_normalizada:
            resultado = [
                item for item in resultado
                if busca_normalizada in item['descricao'].casefold()
                or busca_normalizada in item['codigo'].casefold()
                or busca_normalizada in item['categoria'].casefold()
            ]
        return resultado
