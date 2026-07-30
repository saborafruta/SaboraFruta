"""
Sondagem da cobertura de geocodificação após o backfill.

Somente leitura (COUNT). Reaproveita o mesmo `resumo_geocodificacao` do comando
`cobertura_geo`, para não existirem duas contagens que podem divergir.

Pode ser removida junto com as outras sondagens; é um no-op idempotente.
"""
from django.db import migrations


def _sondar(apps, schema_editor):
    try:
        from apps.mapas.services.cobertura import formatar_resumo, resumo_geocodificacao

        for linha in formatar_resumo(resumo_geocodificacao(), prefixo='[PROBE-COBERTURA] '):
            print(linha)
    except Exception as exc:  # pragma: no cover - diagnostico nao pode quebrar deploy
        print(f'[PROBE-COBERTURA] falhou: {type(exc).__name__}: {exc}')


class Migration(migrations.Migration):

    dependencies = [
        ('mapas', '0004_probe_geocoder'),
    ]

    operations = [
        migrations.RunPython(_sondar, migrations.RunPython.noop),
    ]
