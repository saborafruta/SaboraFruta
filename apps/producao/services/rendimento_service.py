"""
Indicadores de rendimento por linha de produção.

A OP NÃO TEM `linha_producao`. Este módulo filtrava por esse campo, por
`quantidade_perdida`, por `rendimento_percentual` e por `data_encerramento` --
e nenhum dos quatro existe em `OrdemProducao`. Toda chamada estourava
`FieldError`, e o dashboard operacional (o único chamador) caía junto.

O CAMINHO REAL ATÉ A LINHA é pelo produto: `produto_acabado__linha_producao`.
E a perda não é campo da OP, é a soma das `PerdaProducao` penduradas nela --
que é o certo, porque perda tem tipo, motivo e custo, e um total no cabeçalho
da OP não teria onde guardar isso.

O RENDIMENTO DESTE MÓDULO é `produzida / planejada`: a ordem entregou o que
prometeu. NÃO é quanto da matéria-prima virou produto -- essa é outra conta,
sobre peso, e vive no vertical que a mede. Somá-las ou compará-las entre si
não significa nada.
"""
from django.db.models import Avg, Sum
from apps.producao.models import OrdemProducao, PerdaProducao
from apps.producao.constants.enums import StatusOP


class RendimentoService:

    METAS_POR_PREFIXO = {"PF": 75, "MA": 85, "EB": 92}

    @staticmethod
    def rendimento_medio(linha_producao, filial, dias=30):
        from django.utils import timezone
        from datetime import timedelta
        ate = timezone.now()
        de = ate - timedelta(days=dias)
        qs = OrdemProducao.objects.filter(
            produto_acabado__linha_producao=linha_producao, filial=filial,
            status=StatusOP.ENCERRADA,
            data_fim_real__range=(de, ate),
        )
        agg = qs.aggregate(
            total_planejado=Sum("quantidade_planejada"),
            total_produzido=Sum("quantidade_produzida"),
            media_rendimento=Avg("rendimento"),
        )
        # A perda vem das linhas de perda, não de um campo da OP: é lá que ela
        # tem tipo, motivo e custo.
        agg["total_perdido"] = PerdaProducao.objects.filter(
            ordem_producao__in=qs,
        ).aggregate(total=Sum("quantidade"))["total"]
        meta = RendimentoService.METAS_POR_PREFIXO.get(
            linha_producao.prefixo_lote,
            float(linha_producao.meta_rendimento_percentual or 0),
        )
        media = float(agg["media_rendimento"] or 0)
        return {
            "linha": linha_producao.nome,
            "media_rendimento_percentual": round(media, 2),
            "meta_percentual": meta,
            "atinge_meta": media >= meta,
            "ops_consideradas": qs.count(),
            **agg,
        }

    @staticmethod
    def perdas_por_categoria(linha_producao, filial, dias=30):
        from django.utils import timezone
        from datetime import timedelta
        ate = timezone.now()
        de = ate - timedelta(days=dias)
        return list(
            PerdaProducao.objects
            .filter(
                ordem_producao__produto_acabado__linha_producao=linha_producao,
                ordem_producao__filial=filial,
                created_at__range=(de, ate),
            )
            .values("tipo_perda")
            .annotate(
                total_quantidade=Sum("quantidade"),
                total_custo=Sum("impacto_custo"),
            )
            .order_by("-total_quantidade")
        )
