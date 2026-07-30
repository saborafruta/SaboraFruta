"""
Extensões geoespaciais + índices GIST por expressão.

Contexto da decisão: o Postgres desta instância NÃO tem o binário do PostGIS
(`postgis` não consta em `pg_available_extensions`), mas tem `cube` e
`earthdistance` — que juntos entregam distância geodésica e, o que mais
importa, um predicado de contenção indexável por GIST.

Cada índice é PARCIAL (`WHERE latitude IS NOT NULL`): num cadastro em que boa
parte dos registros ainda não foi geocodificada, indexar as linhas nulas só
gastaria espaço, e todas as consultas do módulo já filtram por coordenada
presente.
"""
from django.db import migrations

#: tabelas que ganham coordenada (ver apps.core.models.base.CoordenadaMixin)
TABELAS = [
    'clientes',
    'clientes_enderecos',
    'fornecedores',
    'transportadoras',
    'motoristas',
    'filiais',
]


def _sql_criar():
    partes = [
        'CREATE EXTENSION IF NOT EXISTS cube;',
        'CREATE EXTENSION IF NOT EXISTS earthdistance;',
    ]
    for tabela in TABELAS:
        partes.append(
            f'CREATE INDEX IF NOT EXISTS {tabela}_geo_gist_idx '
            f'ON {tabela} USING gist (ll_to_earth(latitude, longitude)) '
            f'WHERE latitude IS NOT NULL;'
        )
        # B-tree composto para o recorte por viewport (na_area), que é range
        # em duas colunas e não se beneficia do GIST.
        partes.append(
            f'CREATE INDEX IF NOT EXISTS {tabela}_geo_bbox_idx '
            f'ON {tabela} (latitude, longitude) '
            f'WHERE latitude IS NOT NULL;'
        )
    return '\n'.join(partes)


def _sql_remover():
    partes = []
    for tabela in TABELAS:
        partes.append(f'DROP INDEX IF EXISTS {tabela}_geo_gist_idx;')
        partes.append(f'DROP INDEX IF EXISTS {tabela}_geo_bbox_idx;')
    # As extensões ficam: podem estar em uso por outra coisa, e removê-las
    # derrubaria os índices de quem estiver usando.
    return '\n'.join(partes)


class Migration(migrations.Migration):

    dependencies = [
        ('mapas', '0001_initial'),
        # Os índices referenciam as colunas criadas por estas migrations.
        ('cadastros', '0012_coordenadas_geo'),
        ('core', '0038_filial_coordenadas_geo'),
    ]

    operations = [
        migrations.RunSQL(sql=_sql_criar(), reverse_sql=_sql_remover()),
    ]
