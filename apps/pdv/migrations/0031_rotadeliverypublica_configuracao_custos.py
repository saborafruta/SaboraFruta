from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pdv', '0030_rotadeliverypublica_paradas_extras'),
    ]

    operations = [
        migrations.AddField(
            model_name='rotadeliverypublica',
            name='autonomia_km_l',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=8),
        ),
        migrations.AddField(
            model_name='rotadeliverypublica',
            name='combustivel_preco',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=8),
        ),
        migrations.AddField(
            model_name='rotadeliverypublica',
            name='minutos_por_parada',
            field=models.PositiveSmallIntegerField(default=5),
        ),
    ]
