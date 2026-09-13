# Generated manually to keep the quotation history immutable.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('compras', '0019_analise_custo_compra'),
    ]

    operations = [
        migrations.AddField(
            model_name='cotacaocompraitem',
            name='ultima_compra_snapshot',
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text='Ultima compra efetivada conhecida no momento da analise.',
            ),
        ),
    ]
