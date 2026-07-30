"""
Territórios comerciais (§11) — atribuição de clientes e indicadores.

Sem PostGIS o ponto-em-polígono roda em Python. A estratégia para isso escalar
é sempre a mesma: o banco recorta pela caixa envolvente (índice B-tree de
latitude/longitude), e só os candidatos sobreviventes passam pelo ray casting.
Num território que cobre um bairro, isso reduz milhares de clientes a dezenas.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from django.db import transaction

from apps.financeiro.services.receita import ajuste_total

from apps.mapas.managers import na_area

logger = logging.getLogger(__name__)


class TerritorioService:
    """Recalcula quais clientes pertencem a cada praça e apura resultados."""

    @staticmethod
    def _escopo_filiais(filial):
        from apps.mapas.services.proximidade import ProximidadeService

        return ProximidadeService._escopo_filiais(filial)

    # ------------------------------------------------------------ atribuição
    @classmethod
    @transaction.atomic
    def recalcular_praca(cls, praca) -> int:
        """
        Refaz a atribuição de clientes de uma praça. Devolve quantos entraram.

        Substitui o conjunto inteiro (delete + bulk_create) em vez de fazer
        diff: o polígono pode ter mudado de forma arbitrária, então descobrir
        quem entrou e quem saiu custaria mais que reconstruir.
        """
        from apps.cadastros.models import Cliente
        from apps.mapas.models import ClienteTerritorio

        ClienteTerritorio.objects.filter(praca=praca).delete()
        if not praca.tem_poligono:
            return 0

        candidatos = na_area(
            Cliente.objects.filter(
                filial__in=cls._escopo_filiais(praca.filial), ativo=True,
            ),
            praca.bbox_sul, praca.bbox_oeste, praca.bbox_norte, praca.bbox_leste,
        ).only('id', 'latitude', 'longitude')

        dentro = [
            ClienteTerritorio(praca=praca, cliente_id=cl.pk)
            for cl in candidatos.iterator(chunk_size=500)
            if praca.contem_ponto(cl.latitude, cl.longitude)
        ]
        ClienteTerritorio.objects.bulk_create(dentro, batch_size=500)
        return len(dentro)

    @classmethod
    def recalcular_todas(cls, filial) -> dict:
        """Recalcula todas as praças com polígono no escopo da filial."""
        from apps.cadastros.models import Praca

        resultado = {}
        pracas = Praca.objects.filter(
            filial__in=cls._escopo_filiais(filial), ativo=True,
        ).exclude(poligono__isnull=True)

        for praca in pracas:
            try:
                resultado[praca.pk] = cls.recalcular_praca(praca)
            except Exception:
                # Um território com polígono corrompido não pode impedir os outros.
                logger.exception('falha ao recalcular praca %s', praca.pk)
                resultado[praca.pk] = -1
        return resultado

    @classmethod
    def territorio_do_ponto(cls, filial, lat, lng):
        """
        Primeira praça cujo polígono contém o ponto.

        Usada ao cadastrar/geocodificar um cliente para saber em que
        território ele caiu, sem recalcular tudo.
        """
        from apps.cadastros.models import Praca

        candidatas = Praca.objects.filter(
            filial__in=cls._escopo_filiais(filial), ativo=True,
            bbox_sul__lte=lat, bbox_norte__gte=lat,
            bbox_oeste__lte=lng, bbox_leste__gte=lng,
        )
        for praca in candidatas:
            if praca.contem_ponto(lat, lng):
                return praca
        return None

    # ------------------------------------------------------------ indicadores
    @classmethod
    def indicadores(cls, praca, *, dias=30) -> dict:
        """
        Clientes, faturamento, pedidos, ticket médio e meta x realizado (§11).

        O faturamento vem do PDV somado sobre os clientes já atribuídos ao
        território — por isso a atribuição precisa estar recalculada.
        """
        from datetime import timedelta

        from django.db.models import Count, Sum
        from django.utils import timezone

        from apps.mapas.models import ClienteTerritorio
        from apps.pdv.models import VendaPDV

        cliente_ids = list(
            ClienteTerritorio.objects.filter(praca=praca).values_list('cliente_id', flat=True)
        )
        desde = timezone.localdate() - timedelta(days=dias)

        agg = {'faturamento': None, 'pedidos': 0}
        faturamento = Decimal('0')
        if cliente_ids:
            vendas = VendaPDV.objects.filter(
                cliente_id__in=cliente_ids,
                filial__in=cls._escopo_filiais(praca.filial),
                status='finalizada',
                data_venda__date__gte=desde,
            )
            agg = vendas.aggregate(
                faturamento=Sum('valor_total'),
                pedidos=Count('id'),
            )
            # Doacao/Permuta nao sao receita do territorio.
            faturamento = max(
                Decimal('0'),
                (agg['faturamento'] or Decimal('0')) - ajuste_total(vendas),
            )

        # Ticket medio derivado do faturamento ja liquido, e nao Avg(valor_total):
        # o Avg ignoraria o desconto e ficaria incoerente com o faturamento
        # exibido logo ao lado.
        pedidos = agg['pedidos'] or 0
        ticket = (faturamento / pedidos) if pedidos else Decimal('0')
        meta = praca.meta_mensal or Decimal('0')
        return {
            'clientes': len(cliente_ids),
            'faturamento': faturamento,
            'pedidos': pedidos,
            'ticket_medio': ticket,
            'meta': meta,
            'realizado_pct': (
                round(float(faturamento) / float(meta) * 100, 1) if meta else None
            ),
            'dias': dias,
        }
