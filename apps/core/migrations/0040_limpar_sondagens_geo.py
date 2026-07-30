"""
Remove da tabela de controle do Django o registro das três sondagens de
diagnóstico já apagadas do código.

As sondagens (`core.0037_probe_capacidades_geo`, `mapas.0004_probe_geocoder`,
`mapas.0005_probe_cobertura`) eram RunPython no-op criadas para inspecionar o
banco de produção — disponibilidade do PostGIS, configuração do geocoder e
cobertura pós-backfill. Cumpriram o papel e os arquivos foram removidos.

Apagar só os arquivos deixaria linhas em `django_migrations` apontando para
migrations inexistentes. O Django tolera isso (o grafo é montado a partir do
disco e registros desconhecidos são ignorados), mas fica um risco latente:
se algum dia surgir uma migration com exatamente um desses nomes, ela seria
considerada já aplicada e silenciosamente pulada.

Só remove os três nomes exatos. Em banco novo os registros nunca existiram e o
DELETE simplesmente não afeta nada.
"""
from django.db import migrations

SONDAGENS = [
    ('core', '0037_probe_capacidades_geo'),
    ('mapas', '0004_probe_geocoder'),
    ('mapas', '0005_probe_cobertura'),
]


def limpar(apps, schema_editor):
    with schema_editor.connection.cursor() as cur:
        for app_label, nome in SONDAGENS:
            cur.execute(
                'DELETE FROM django_migrations WHERE app = %s AND name = %s',
                [app_label, nome],
            )
            if cur.rowcount:
                print(f'  [limpeza] registro removido: {app_label}.{nome}')


def reverter(apps, schema_editor):
    """
    No-op deliberado.

    Reinserir os registros não faria sentido: os arquivos das sondagens não
    existem mais, então o Django trataria os registros como órfãos de novo —
    exatamente o estado que esta migration veio corrigir.
    """


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0039_permissao_modulo_mapas'),
        # Garante que o app mapas já esteja migrado antes de mexer nos
        # registros dele.
        ('mapas', '0003_cliente_territorio'),
    ]

    operations = [
        migrations.RunPython(limpar, reverter),
    ]
