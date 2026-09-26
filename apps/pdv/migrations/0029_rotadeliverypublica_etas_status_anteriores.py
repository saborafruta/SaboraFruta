from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pdv', '0028_rotadeliverypublica'),
    ]

    operations = [
        migrations.AddField(
            model_name='rotadeliverypublica',
            name='pedido_etas',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='rotadeliverypublica',
            name='pedido_status_anteriores',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
