import os

from django.db import migrations, models
import django.db.models.deletion


def cadastrar_pool_principal(apps, schema_editor):
    if schema_editor.connection.alias != 'default':
        return
    enabled = os.environ.get('TENANT_DATABASE_ROUTING_ENABLED', '').lower()
    if enabled not in {'1', 'true', 'yes', 'on'}:
        return
    project_id = os.environ.get('RAILWAY_PROJECT_ID', '').strip()
    environment_id = os.environ.get('RAILWAY_ENVIRONMENT_ID', '').strip()
    if not project_id or not environment_id:
        return

    RailwayProjectPool = apps.get_model('core', 'RailwayProjectPool')
    EmpresaBanco = apps.get_model('core', 'EmpresaBanco')
    pool, _ = RailwayProjectPool.objects.using(schema_editor.connection.alias).get_or_create(
        railway_project_id=project_id,
        defaults={
            'nome': 'Projeto Principal iTed',
            'railway_environment_id': environment_id,
            'token_env_var': 'RAILWAY_PROJECT_TOKEN',
            'connection_mode': 'private',
            'prioridade': 10,
            'max_volumes': 10,
            'volumes_reservados': 1,
            # A migration nunca libera provisionamento externo sozinha.
            # O operador precisa validar credencial, ambiente e capacidade.
            'status': 'manutencao',
            'ativo': False,
        },
    )
    EmpresaBanco.objects.using(schema_editor.connection.alias).filter(
        railway_project_pool__isnull=True,
    ).update(railway_project_pool=pool)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0059_tenant_public_link_index'),
    ]

    operations = [
        migrations.CreateModel(
            name='RailwayProjectPool',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('nome', models.CharField(max_length=120, unique=True)),
                ('railway_project_id', models.CharField(max_length=120, unique=True)),
                ('railway_environment_id', models.CharField(max_length=120)),
                ('token_env_var', models.CharField(help_text='Nome da variável protegida que contém o Project Token deste projeto.', max_length=120)),
                ('connection_mode', models.CharField(choices=[('private', 'Rede privada (mesmo projeto)'), ('public', 'TCP Proxy com SSL (outro projeto)')], default='public', max_length=16)),
                ('prioridade', models.PositiveSmallIntegerField(db_index=True, default=100)),
                ('max_volumes', models.PositiveSmallIntegerField(default=10)),
                ('volumes_reservados', models.PositiveSmallIntegerField(default=1, help_text='Vagas preservadas para restauração ou manutenção emergencial.')),
                ('ultimo_total_volumes', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('ultima_verificacao_em', models.DateTimeField(blank=True, null=True)),
                ('ultimo_erro', models.TextField(blank=True)),
                ('status', models.CharField(choices=[('ativo', 'Ativo'), ('lotado', 'Lotado'), ('manutencao', 'Manutenção'), ('erro', 'Erro')], db_index=True, default='manutencao', max_length=16)),
                ('ativo', models.BooleanField(db_index=True, default=False)),
            ],
            options={
                'verbose_name': 'Projeto Railway de bancos',
                'verbose_name_plural': 'Projetos Railway de bancos',
                'db_table': 'railway_project_pools',
                'ordering': ['prioridade', 'nome'],
            },
        ),
        migrations.AddField(
            model_name='empresabanco',
            name='railway_project_pool',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='bancos', to='core.railwayprojectpool'),
        ),
        migrations.AddField(
            model_name='empresabanco',
            name='railway_tcp_proxy_domain',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name='empresabanco',
            name='railway_tcp_proxy_id',
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name='empresabanco',
            name='railway_tcp_proxy_port',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='empresabanco',
            name='railway_volume_id',
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.RunPython(cadastrar_pool_principal, migrations.RunPython.noop),
    ]
