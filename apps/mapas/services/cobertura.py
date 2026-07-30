"""
Cobertura de geocodificação (§14).

Responde "o mapa é confiável?" — quantos registros de cada entidade já têm
coordenada, e o que aconteceu com os que não têm. É a informação que separa
"o mapa está vazio porque não há clientes" de "está vazio porque o backfill
não rodou" ou "o provider recusou os endereços".
"""
from __future__ import annotations

#: apelido -> (app_label, ModelName, rótulo)
ENTIDADES = [
    ('cliente', 'cadastros', 'Cliente', 'Clientes'),
    ('cliente_endereco', 'cadastros', 'ClienteEndereco', 'Endereços de entrega'),
    ('fornecedor', 'cadastros', 'Fornecedor', 'Fornecedores'),
    ('transportadora', 'cadastros', 'Transportadora', 'Transportadoras'),
    ('motorista', 'cadastros', 'Motorista', 'Motoristas'),
    ('filial', 'core', 'Filial', 'Filiais'),
]


def resumo_geocodificacao(limite_erros: int = 5) -> dict:
    """
    Contagens por entidade + estatísticas do cache.

    Um `aggregate` com `Count(filter=...)` por entidade em vez de duas queries
    cada: são 6 entidades, e o dobro de idas ao banco não se justifica para um
    número que é só informativo.
    """
    from django.apps import apps as django_apps
    from django.db.models import Count, Q

    entidades = []
    for _, app_label, model_name, rotulo in ENTIDADES:
        try:
            model = django_apps.get_model(app_label, model_name)
        except LookupError:  # pragma: no cover
            continue

        agg = model.objects.aggregate(
            total=Count('id'),
            com=Count('id', filter=Q(latitude__isnull=False)),
            fixados=Count('id', filter=Q(geo_fixado=True)),
            com_erro=Count('id', filter=~Q(geo_erro='')),
        )
        total = agg['total'] or 0
        com = agg['com'] or 0
        entidades.append({
            'rotulo': rotulo,
            'total': total,
            'com_coordenada': com,
            'sem_coordenada': total - com,
            'fixados': agg['fixados'] or 0,
            'com_erro': agg['com_erro'] or 0,
            'percentual': round(com / total * 100, 1) if total else 0.0,
        })

    return {'entidades': entidades, 'cache': _resumo_cache(limite_erros)}


def _resumo_cache(limite_erros: int) -> dict:
    """Estado do cache de geocodificação, com os erros mais frequentes."""
    from django.db.models import Count

    from apps.mapas.models import CacheGeocodificacao

    total = CacheGeocodificacao.objects.count()
    encontrados = CacheGeocodificacao.objects.filter(encontrado=True).count()

    # Agrupar por mensagem revela padrão: "endereco nao encontrado" em massa é
    # problema de qualidade de cadastro; erro de provider é quota/credencial.
    erros = list(
        CacheGeocodificacao.objects.filter(encontrado=False)
        .values('erro')
        .annotate(qtd=Count('endereco_hash'))
        .order_by('-qtd')[:limite_erros]
    )
    providers = list(
        CacheGeocodificacao.objects.values('provider')
        .annotate(qtd=Count('endereco_hash'))
        .order_by('-qtd')
    )
    return {
        'total': total,
        'encontrados': encontrados,
        'falhas': total - encontrados,
        'erros_frequentes': erros,
        'providers': providers,
    }


def formatar_resumo(resumo: dict, prefixo: str = '') -> list[str]:
    """Resumo em linhas de texto, para o comando e para os logs de deploy."""
    linhas = []
    for e in resumo['entidades']:
        extra = ''
        if e['com_erro']:
            extra += f" · {e['com_erro']} com erro"
        if e['fixados']:
            extra += f" · {e['fixados']} fixado(s) a mao"
        linhas.append(
            f"{prefixo}{e['rotulo']:22} {e['com_coordenada']:>6}/{e['total']:<6} "
            f"({e['percentual']:>5.1f}%) · {e['sem_coordenada']} sem coordenada{extra}"
        )

    c = resumo['cache']
    linhas.append(
        f"{prefixo}cache: {c['total']} endereco(s), "
        f"{c['encontrados']} resolvido(s), {c['falhas']} falha(s)"
    )
    for p in c['providers']:
        linhas.append(f"{prefixo}  provider {p['provider'] or '(vazio)'}: {p['qtd']}")
    for e in c['erros_frequentes']:
        linhas.append(f"{prefixo}  erro {e['erro'][:70]!r}: {e['qtd']}")
    return linhas
