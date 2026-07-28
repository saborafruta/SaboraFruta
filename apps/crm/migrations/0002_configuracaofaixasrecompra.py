import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0033_permissao_modulo_crm'),
        ('crm', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='ConfiguracaoFaixasRecompra',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('faixa_5_dias', models.PositiveIntegerField(default=90)),
                ('faixa_6_dias', models.PositiveIntegerField(default=120)),
                ('faixa_7_dias', models.PositiveIntegerField(default=360)),
                ('filial', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='faixas_recompra', to='core.filial',
                )),
            ],
            options={
                'verbose_name': 'Faixas de Recompra da Filial',
                'verbose_name_plural': 'Faixas de Recompra das Filiais',
                'db_table': 'crm_faixas_recompra',
            },
        ),
    ]
