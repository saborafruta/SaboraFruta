from django.db import migrations


MODELOS_EM_ORDEM = (
    'ProfissionalAgenda',
    'Agendamento',
    'AgendamentoItem',
    'BloqueioAgenda',
    'ProfissionalServico',
    'JornadaTrabalho',
)


def reparar_schema_tenant(apps, schema_editor):
    """Cria tabelas da agenda que ficaram ausentes em bancos de filiais.

    A primeira migração chegou a ser registrada em alguns bancos antes de o
    app ``agenda`` entrar no roteador multiempresa. Nesses bancos o registro
    da migração existe, mas as operações foram ignoradas pelo roteador. Esta
    reparação é idempotente e atua apenas nos bancos das empresas.
    """
    if schema_editor.connection.alias == 'default':
        return

    tabelas = set(schema_editor.connection.introspection.table_names())
    for nome_modelo in MODELOS_EM_ORDEM:
        modelo = apps.get_model('agenda', nome_modelo)
        if modelo._meta.db_table in tabelas:
            continue
        schema_editor.create_model(modelo)
        tabelas.add(modelo._meta.db_table)


class Migration(migrations.Migration):
    dependencies = [
        ('agenda', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(reparar_schema_tenant, migrations.RunPython.noop),
    ]
