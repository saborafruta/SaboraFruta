import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('moda', '0051_rascunho_op'),
    ]

    operations = [
        migrations.CreateModel(
            name='RascunhoItemOP',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('dados', models.JSONField(blank=True, default=dict)),
                ('filial', models.ForeignKey(help_text='Filial proprietária do registro', on_delete=django.db.models.deletion.PROTECT, related_name='+', to='core.filial')),
                ('pedido', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='rascunho_item', to='moda.pedidoproducao')),
                ('usuario', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='rascunhos_item_op', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'rascunho de item da OP',
                'verbose_name_plural': 'rascunhos de item da OP',
            },
        ),
    ]
