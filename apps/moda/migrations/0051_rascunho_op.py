import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('moda', '0050_conjunto_integrado'),
    ]

    operations = [
        migrations.CreateModel(
            name='RascunhoOP',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('dados', models.JSONField(blank=True, default=dict)),
                ('filial', models.ForeignKey(help_text='Filial proprietária do registro', on_delete=django.db.models.deletion.PROTECT, related_name='+', to='core.filial')),
                ('usuario', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='rascunhos_op', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'rascunho de OP',
                'verbose_name_plural': 'rascunhos de OP',
                'constraints': [models.UniqueConstraint(fields=('filial', 'usuario'), name='moda_rascunho_op_filial_usuario_unico')],
            },
        ),
    ]
