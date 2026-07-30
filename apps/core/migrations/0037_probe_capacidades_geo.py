"""Sondagem SOMENTE-LEITURA das capacidades geoespaciais do banco.

Não cria, altera nem remove nada: só consulta catálogos do Postgres e
imprime o resultado no stdout (que o Railway captura como log do deploy).
Serve para decidir a arquitetura do módulo de Mapas — se o PostGIS está
disponível na instância, ou se é preciso outra abordagem.

Pode ser removida depois de lida; é um no-op idempotente.
"""
from django.db import migrations


def _sondar(apps, schema_editor):
    conn = schema_editor.connection
    if conn.vendor != 'postgresql':
        print('[PROBE-GEO] banco nao-postgres, sondagem ignorada')
        return

    with conn.cursor() as cur:
        cur.execute('SELECT version()')
        print(f'[PROBE-GEO] versao = {cur.fetchone()[0]}')

        # Extensões geoespaciais/afins DISPONÍVEIS para instalação.
        cur.execute(
            """
            SELECT name, default_version, installed_version
              FROM pg_available_extensions
             WHERE name IN ('postgis','postgis_topology','postgis_raster',
                            'cube','earthdistance','pg_trgm','unaccent')
             ORDER BY name
            """
        )
        linhas = cur.fetchall()
        if linhas:
            for nome, disp, inst in linhas:
                estado = f'INSTALADA {inst}' if inst else 'disponivel (nao instalada)'
                print(f'[PROBE-GEO] ext {nome}: {estado} (default {disp})')
        else:
            print('[PROBE-GEO] nenhuma das extensoes procuradas esta disponivel')

        cur.execute('SELECT count(*) FROM pg_available_extensions')
        print(f'[PROBE-GEO] total de extensoes disponiveis = {cur.fetchone()[0]}')

    print('[PROBE-GEO] fim da sondagem (nada foi alterado)')


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0036_notificacao'),
    ]

    operations = [
        migrations.RunPython(_sondar, migrations.RunPython.noop),
    ]
