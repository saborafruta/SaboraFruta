"""
Mapa de calor de vendas (§10).

O ponto de calor é a **coordenada do cliente**, não a do endereço de entrega.
Motivo: a coordenada do cadastro é a que o backfill geocodifica e mantém
atualizada; o endereço de entrega é um JSON por venda que na maioria das vezes
não traz lat/lng. Usar o cliente dá uma leitura estável de "onde meu dinheiro
vem", que é o que o mapa de calor responde.

As vendas saem de duas tabelas — `PedidoVenda` (B2B) e `VendaPDV` (balcão e
delivery) —, somadas por cliente. É o mesmo par que o dashboard e a Curva ABC
já combinam; usar só uma delas mostraria metade do faturamento.

A receita desconta as formas com `movimenta_caixa=False` (Doação, Permuta) via
`apps.financeiro.services.receita`. Sem isso o mapa acenderia bairros onde não
entrou dinheiro nenhum, e divergiria do faturamento mostrado no resto do ERP.
"""
from __future__ import annotations

import datetime
from decimal import Decimal

from django.db.models import Count, DecimalField, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

# rotulo, unidade exibida
METRICAS = {
    'receita':  ('Receita', 'R$'),
    'pedidos':  ('Quantidade de pedidos', 'pedidos'),
    'volume':   ('Volume vendido', 'itens'),
    'clientes': ('Número de clientes', 'clientes'),
}

JANELA_PADRAO_DIAS = 365

# Quadrantes geograficos, calculados a partir da coordenada — nao existe campo
# de zona no cadastro, e pedir preenchimento manual de 450 clientes seria pior
# que derivar do que ja esta geocodificado.
ZONAS = {
    'norte':  'Norte',
    'sul':    'Sul',
    'leste':  'Leste',
    'oeste':  'Oeste',
}


class HeatmapService:
    """Agrega vendas por cliente e devolve pontos ponderados para o Leaflet."""

    @staticmethod
    def _escopo_filiais(filial, filial_id=None):
        """
        Filiais visíveis, opcionalmente estreitadas para uma só.

        O `filial_id` do filtro é sempre validado contra o escopo do usuário —
        aceitá-lo direto deixaria qualquer um ler o faturamento de outra
        empresa trocando um número na URL.
        """
        from apps.mapas.services import ProximidadeService

        filiais = ProximidadeService._escopo_filiais(filial)
        if filial_id:
            filiais = filiais.filter(pk=filial_id)
        return filiais

    @classmethod
    def _clientes(cls, filiais, cidade='', uf='', bairro='', zona='', praca_id=None):
        """Clientes geocodificados do escopo, já filtrados."""
        from apps.cadastros.models import Cliente

        qs = Cliente.objects.filter(
            filial__in=filiais, latitude__isnull=False, longitude__isnull=False,
        )
        if cidade:
            qs = qs.filter(cidade__iexact=cidade)
        if uf:
            qs = qs.filter(uf__iexact=uf)
        if bairro:
            qs = qs.filter(bairro__iexact=bairro)
        if praca_id:
            # Território desenhado (§11): limite exato, definido pelo usuário.
            qs = qs.filter(territorios__praca_id=praca_id,
                           territorios__praca__filial__in=filiais)
        if zona:
            qs = cls._filtrar_zona(qs, zona, filiais, cidade, uf)
        return qs

    @classmethod
    def _centro_da_base(cls, filiais, cidade='', uf=''):
        """
        Ponto de referência das zonas: o centro médio dos clientes do escopo.

        Não é a filial de propósito. Uma filial na borda da cidade jogaria
        quase todo mundo para uma única zona; o centro da própria carteira
        divide a base em quatro partes que significam alguma coisa.

        O centro considera cidade/UF mas **não** o bairro nem o território:
        se movesse a cada filtro, "Zona Norte" mudaria de lugar conforme o
        recorte e dois relatórios não seriam comparáveis.
        """
        from django.db.models import Avg

        from apps.cadastros.models import Cliente

        qs = Cliente.objects.filter(
            filial__in=filiais, latitude__isnull=False, longitude__isnull=False,
        )
        if cidade:
            qs = qs.filter(cidade__iexact=cidade)
        if uf:
            qs = qs.filter(uf__iexact=uf)

        agg = qs.aggregate(lat=Avg('latitude'), lng=Avg('longitude'))
        return agg['lat'], agg['lng']

    @classmethod
    def _filtrar_zona(cls, qs, zona, filiais, cidade, uf):
        """
        Recorta por quadrante geográfico em relação ao centro da base.

        A divisão é em quatro cunhas, não em metades: um cliente vai para
        Norte/Sul quando se afasta mais em latitude, e para Leste/Oeste
        quando se afasta mais em longitude. Com metades simples, cada ponto
        cairia em duas zonas ao mesmo tempo e a soma das quatro daria o dobro
        da base.

        Isto é geometria, não a divisão administrativa da prefeitura — os
        limites não vão bater exatamente com o que o pessoal da rua chama de
        "Zona Norte". Para limite exato existe o filtro de Território, que usa
        o polígono desenhado no mapa.
        """
        from django.db.models import F, FloatField, Q
        from django.db.models.functions import Abs, Cast

        zona = (zona or '').lower()
        if zona not in ZONAS:
            return qs

        centro_lat, centro_lng = cls._centro_da_base(filiais, cidade, uf)
        if centro_lat is None:
            return qs

        qs = qs.annotate(
            _dlat=Cast(F('latitude') - centro_lat, FloatField()),
            _dlng=Cast(F('longitude') - centro_lng, FloatField()),
        ).annotate(
            _vertical=Q(_dlat__gte=Abs(F('_dlng'))) | Q(_dlat__lte=-Abs(F('_dlng'))),
        )

        if zona == 'norte':
            return qs.filter(_vertical=True, _dlat__gt=0)
        if zona == 'sul':
            return qs.filter(_vertical=True, _dlat__lte=0)
        if zona == 'leste':
            return qs.filter(_vertical=False, _dlng__gt=0)
        return qs.filter(_vertical=False, _dlng__lte=0)   # oeste

    @staticmethod
    def _periodo(inicio, fim):
        hoje = timezone.localdate()
        fim = fim or hoje
        inicio = inicio or (fim - datetime.timedelta(days=JANELA_PADRAO_DIAS))
        return inicio, fim

    @classmethod
    def _pedidos_b2b(cls, filiais, inicio, fim, representante_id, metrica):
        """Soma de `PedidoVenda` por cliente."""
        from apps.vendas.models import PedidoVenda

        status = [
            PedidoVenda.Status.CONFIRMADO, PedidoVenda.Status.EM_SEPARACAO,
            PedidoVenda.Status.FATURADO, PedidoVenda.Status.PARCIALMENTE_FATURADO,
            PedidoVenda.Status.ENTREGUE,
        ]
        qs = PedidoVenda.objects.filter(
            filial__in=filiais, status__in=status,
            data_emissao__date__gte=inicio, data_emissao__date__lte=fim,
        )
        if representante_id:
            qs = qs.filter(representante_id=representante_id)

        if metrica == 'volume':
            linhas = qs.values('cliente_id').annotate(
                v=Coalesce(Sum('itens__quantidade'),
                           Decimal('0'), output_field=DecimalField()))
        elif metrica == 'pedidos':
            linhas = qs.values('cliente_id').annotate(v=Count('id'))
        else:  # receita (e clientes, que ignora o peso)
            linhas = qs.values('cliente_id').annotate(
                v=Coalesce(Sum('valor_total'),
                           Decimal('0'), output_field=DecimalField()))

        return {l['cliente_id']: Decimal(str(l['v'] or 0)) for l in linhas}

    @classmethod
    def _vendas_pdv(cls, filiais, inicio, fim, representante_id, metrica):
        """
        Soma de `VendaPDV` por cliente.

        Com filtro de representante o PDV fica **de fora**: a venda de balcão
        não guarda representante, então incluí-la atribuiria a um vendedor
        faturamento que não é dele. Devolver vazio é o comportamento honesto —
        e a tela avisa que o recorte passou a ser só dos pedidos B2B.
        """
        from apps.financeiro.services import receita as receita_svc
        from apps.pdv.models import VendaPDV

        if representante_id:
            return {}

        qs = VendaPDV.objects.filter(
            filial__in=filiais, status='finalizada',
            data_venda__date__gte=inicio, data_venda__date__lte=fim,
        )

        if metrica == 'volume':
            linhas = qs.values('cliente_id').annotate(
                v=Coalesce(Sum('itens__quantidade'),
                           Decimal('0'), output_field=DecimalField()))
        elif metrica == 'pedidos':
            linhas = qs.values('cliente_id').annotate(v=Count('id'))
        else:
            linhas = qs.values('cliente_id').annotate(
                v=Coalesce(Sum('valor_total'),
                           Decimal('0'), output_field=DecimalField()))

        totais = {l['cliente_id']: Decimal(str(l['v'] or 0)) for l in linhas}

        # Doação e Permuta dão baixa no estoque mas não são receita. O ajuste
        # só cabe no dinheiro: descontá-lo de "pedidos" ou "volume" mudaria a
        # contagem de coisas que de fato aconteceram.
        if metrica == 'receita':
            for cliente_id, desconto in receita_svc.ajuste_por_cliente(qs).items():
                if cliente_id in totais:
                    totais[cliente_id] = max(
                        Decimal('0'), totais[cliente_id] - desconto)

        return totais

    @classmethod
    def pontos(cls, *, filial, metrica='receita', inicio=None, fim=None,
               cidade='', uf='', bairro='', zona='', praca_id=None,
               representante_id=None, filial_id=None):
        """
        Pontos `[lat, lng, peso]` para o Leaflet.heat.

        O peso vai **normalizado de 0 a 1** contra o maior valor do recorte:
        o plugin satura qualquer intensidade acima de 1, então mandar reais
        crus pintaria o mapa inteiro de vermelho. Os valores absolutos voltam
        em `total` e `maximo`, para a legenda dizer o que a cor significa.
        """
        if metrica not in METRICAS:
            metrica = 'receita'

        filiais = cls._escopo_filiais(filial, filial_id)
        inicio, fim = cls._periodo(inicio, fim)
        clientes = cls._clientes(filiais, cidade, uf, bairro, zona, praca_id)

        b2b = cls._pedidos_b2b(filiais, inicio, fim, representante_id, metrica)
        pdv = cls._vendas_pdv(filiais, inicio, fim, representante_id, metrica)

        # "Número de clientes" é densidade de cadastros que compraram: cada um
        # pesa 1, independentemente de quanto comprou.
        if metrica == 'clientes':
            valores = {cid: Decimal('1') for cid in set(b2b) | set(pdv)}
        else:
            valores = {}
            for origem in (b2b, pdv):
                for cid, v in origem.items():
                    valores[cid] = valores.get(cid, Decimal('0')) + v

        coordenadas = clientes.values_list('id', 'latitude', 'longitude')

        brutos = [
            (lat, lng, valores[cid])
            for cid, lat, lng in coordenadas
            if valores.get(cid, Decimal('0')) > 0
        ]
        maximo = max((v for _, _, v in brutos), default=Decimal('0'))

        return {
            'metrica': metrica,
            'rotulo': METRICAS[metrica][0],
            'unidade': METRICAS[metrica][1],
            'inicio': inicio.isoformat(),
            'fim': fim.isoformat(),
            'pontos': [
                [lat, lng, round(float(v / maximo), 4)] for lat, lng, v in brutos
            ] if maximo else [],
            'total': float(sum((v for _, _, v in brutos), Decimal('0'))),
            'maximo': float(maximo),
            'locais': len(brutos),
            # Sem coordenada o cliente não entra no mapa. Contar quantos ficaram
            # de fora evita ler um mapa incompleto como se fosse o todo.
            'sem_coordenada': cls._sem_coordenada(
                filiais, valores, cidade, uf, bairro),
        }

    @classmethod
    def _sem_coordenada(cls, filiais, valores, cidade, uf, bairro=''):
        """
        Quantos clientes com venda no recorte não puderam ser plotados.

        Zona e território não entram aqui de propósito: os dois se decidem
        pela coordenada, e quem não tem coordenada não pertence a nenhum
        deles — o número viraria sempre zero e esconderia o problema.
        """
        from apps.cadastros.models import Cliente

        if not valores:
            return 0
        qs = Cliente.objects.filter(
            filial__in=filiais, pk__in=list(valores), latitude__isnull=True,
        )
        if cidade:
            qs = qs.filter(cidade__iexact=cidade)
        if uf:
            qs = qs.filter(uf__iexact=uf)
        if bairro:
            qs = qs.filter(bairro__iexact=bairro)
        return qs.count()

    @classmethod
    def opcoes_de_filtro(cls, filial):
        """Cidades, UFs, representantes e filiais que existem no escopo."""
        from apps.cadastros.models import Praca, Representante

        filiais = cls._escopo_filiais(filial)
        clientes = cls._clientes(filiais)

        cidades = sorted(
            {c for c in clientes.values_list('cidade', flat=True).distinct() if c}
        )
        bairros = sorted(
            {b for b in clientes.values_list('bairro', flat=True).distinct() if b}
        )
        ufs = sorted(
            {u for u in clientes.values_list('uf', flat=True).distinct() if u}
        )
        representantes = [
            {'id': r.pk, 'nome': r.nome}
            for r in Representante.objects.filter(
                filial__in=filiais,
            ).order_by('nome')[:200]
        ]
        return {
            'cidades': cidades,
            'bairros': bairros,
            'ufs': ufs,
            'zonas': [{'chave': k, 'rotulo': v} for k, v in ZONAS.items()],
            # Só praças com polígono desenhado: sem polígono não há como dizer
            # quem está dentro, e a opção viraria um filtro que zera tudo.
            'territorios': [
                {'id': p.pk, 'nome': p.nome}
                for p in Praca.objects.filter(
                    filial__in=filiais, clientes_territorio__isnull=False,
                ).distinct().order_by('nome')[:200]
            ],
            'representantes': representantes,
            'filiais': [
                {'id': f.pk, 'nome': f.nome_fantasia or f.razao_social}
                for f in filiais.order_by('nome_fantasia')
            ],
        }
