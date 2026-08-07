from __future__ import annotations

from decimal import Decimal

from django.db.models import Sum

from apps.food_service.models import Comanda, ItemComanda
from apps.food_service.services.ficha_tecnica_service import FichaTecnicaService

_LIMIAR_AUMENTO_CUSTO_PERCENTUAL = Decimal('5')


class CmvService:
    """
    CMV por prato/categoria/período, margem de contribuição e lucro, a
    partir das comandas fechadas -- reaproveita `FichaTecnicaService.
    custo_estimado` (mesma fonte usada na tela da comanda) em vez de
    recalcular custo por outro caminho.
    """

    @classmethod
    def resumo(cls, filial, data_inicio, data_fim, meta_cmv_percentual=None):
        itens = (
            ItemComanda.objects
            .filter(
                comanda__filial=filial,
                comanda__status=Comanda.Status.FECHADA,
                comanda__fechada_em__date__gte=data_inicio,
                comanda__fechada_em__date__lte=data_fim,
            )
            .select_related('produto', 'produto__categoria')
            .prefetch_related('complementos')
        )

        linhas = []
        for item in itens:
            receita = item.valor_total_com_complementos
            custo = FichaTecnicaService.custo_estimado(item.produto, item.quantidade)
            linhas.append({
                'item': item,
                'receita': receita,
                'custo': custo or Decimal('0'),
                'tem_ficha': custo is not None,
            })

        receita_total = sum((l['receita'] for l in linhas), Decimal('0'))
        custo_total = sum((l['custo'] for l in linhas), Decimal('0'))
        margem_contribuicao = receita_total - custo_total
        cmv_percentual = (custo_total / receita_total * 100) if receita_total else None

        despesas_operacionais = cls._despesas_operacionais(filial, data_inicio, data_fim)
        lucro_bruto = margem_contribuicao
        lucro_liquido = lucro_bruto - despesas_operacionais

        por_prato = cls._agrupar(linhas, chave=lambda l: l['item'].produto.descricao)
        por_categoria = cls._agrupar(
            linhas,
            chave=lambda l: l['item'].produto.categoria.nome if l['item'].produto.categoria_id else 'Outros',
        )

        alertas = cls._montar_alertas(
            filial=filial,
            data_inicio=data_inicio,
            cmv_percentual=cmv_percentual,
            meta_cmv_percentual=meta_cmv_percentual,
            por_prato=por_prato,
        )

        return {
            'receita_total': receita_total,
            'custo_total': custo_total,
            'cmv_percentual': cmv_percentual,
            'margem_contribuicao': margem_contribuicao,
            'lucro_bruto': lucro_bruto,
            'despesas_operacionais': despesas_operacionais,
            'lucro_liquido': lucro_liquido,
            'por_prato': por_prato,
            'por_categoria': por_categoria,
            'alertas': alertas,
        }

    @staticmethod
    def _agrupar(linhas, *, chave):
        grupos = {}
        for linha in linhas:
            nome = chave(linha)
            grupo = grupos.setdefault(nome, {
                'nome': nome, 'receita': Decimal('0'), 'custo': Decimal('0'), 'tem_ficha': False,
            })
            grupo['receita'] += linha['receita']
            grupo['custo'] += linha['custo']
            grupo['tem_ficha'] = grupo['tem_ficha'] or linha['tem_ficha']

        resultado = []
        for grupo in grupos.values():
            cmv_pct = (grupo['custo'] / grupo['receita'] * 100) if grupo['receita'] else None
            resultado.append({
                **grupo,
                'margem': grupo['receita'] - grupo['custo'],
                'cmv_percentual': round(cmv_pct, 1) if cmv_pct is not None else None,
            })
        resultado.sort(key=lambda g: g['cmv_percentual'] or 0, reverse=True)
        return resultado

    @staticmethod
    def _despesas_operacionais(filial, data_inicio, data_fim):
        """
        Despesas operacionais pagas no período (aluguel, energia, salários
        etc.) -- filtra pelo plano de contas tipo "D" pra não misturar com
        compra de mercadoria (que já entra no CMV por outro caminho). Depende
        do plano de contas da empresa estar categorizado corretamente; sem
        isso o número pode ficar impreciso, por isso aparece com nota na tela.
        """
        from apps.financeiro.models import ContaPagar

        total = ContaPagar.objects.filter(
            filial=filial,
            status='pago',
            data_pagamento__date__gte=data_inicio,
            data_pagamento__date__lte=data_fim,
            plano_contas__tipo='D',
        ).aggregate(total=Sum('valor_pago'))['total']
        return total or Decimal('0')

    @classmethod
    def _montar_alertas(cls, *, filial, data_inicio, cmv_percentual, meta_cmv_percentual, por_prato):
        alertas = []

        if meta_cmv_percentual is not None and cmv_percentual is not None and cmv_percentual > meta_cmv_percentual:
            alertas.append({
                'tipo': 'cmv_meta',
                'mensagem': f'CMV geral em {cmv_percentual:.1f}%, acima da meta de {meta_cmv_percentual}%.',
            })

        for grupo in por_prato:
            if not grupo['tem_ficha'] or grupo['cmv_percentual'] is None:
                continue
            if meta_cmv_percentual is not None and grupo['cmv_percentual'] > meta_cmv_percentual:
                alertas.append({
                    'tipo': 'cmv_prato',
                    'mensagem': f'{grupo["nome"]}: CMV em {grupo["cmv_percentual"]}%, acima da meta.',
                })
            if grupo['margem'] < 0:
                alertas.append({
                    'tipo': 'margem_negativa',
                    'mensagem': f'{grupo["nome"]}: margem negativa no período (custo maior que a receita).',
                })

        alertas.extend(cls._alertas_aumento_custo(filial, data_inicio))
        return alertas

    @staticmethod
    def _alertas_aumento_custo(filial, data_inicio):
        """
        Compara o custo médio atual do ingrediente com o custo da última
        movimentação registrada antes do período -- um jeito simples de
        flagrar "esse insumo subiu de preço" sem precisar guardar um
        histórico de custo à parte.
        """
        from apps.estoque.models import MovimentacaoEstoque
        from apps.produtos.models import Produto

        ids_ingredientes = set(
            MovimentacaoEstoque.objects
            .filter(filial=filial, tipo_operacao=MovimentacaoEstoque.TipoOperacao.PRODUCAO_SAIDA)
            .values_list('produto_id', flat=True)
            .distinct()
        )
        if not ids_ingredientes:
            return []

        alertas = []
        for produto in Produto.objects.filter(pk__in=ids_ingredientes):
            custo_anterior = (
                MovimentacaoEstoque.objects
                .filter(produto=produto, filial=filial, created_at__date__lt=data_inicio, valor_unitario__gt=0)
                .order_by('-created_at')
                .values_list('valor_unitario', flat=True)
                .first()
            )
            if not custo_anterior:
                continue
            custo_atual = produto.preco_custo_medio or Decimal('0')
            if custo_atual <= custo_anterior:
                continue
            aumento_percentual = (custo_atual - custo_anterior) / custo_anterior * 100
            if aumento_percentual >= _LIMIAR_AUMENTO_CUSTO_PERCENTUAL:
                alertas.append({
                    'tipo': 'aumento_ingrediente',
                    'mensagem': (
                        f'{produto.descricao}: custo subiu {aumento_percentual:.1f}% '
                        f'(de R$ {custo_anterior:.4f} para R$ {custo_atual:.4f}) desde o início do período.'
                    ),
                })
        return alertas
