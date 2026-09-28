from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('pdv', '0034_rotadelivery')]

    operations = [
        migrations.AddField(
            model_name='rotadelivery',
            name='conclusoes_pedidos',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='rotadeliverypublica',
            name='conclusoes_pedidos',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
