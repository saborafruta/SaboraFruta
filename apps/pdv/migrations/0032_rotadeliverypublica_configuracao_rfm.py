from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pdv', '0031_rotadeliverypublica_configuracao_custos'),
    ]

    operations = [
        migrations.AddField(
            model_name='rotadeliverypublica',
            name='rfm_r2_dias',
            field=models.PositiveSmallIntegerField(default=180),
        ),
        migrations.AddField(
            model_name='rotadeliverypublica',
            name='rfm_r3_dias',
            field=models.PositiveSmallIntegerField(default=90),
        ),
        migrations.AddField(
            model_name='rotadeliverypublica',
            name='rfm_r4_dias',
            field=models.PositiveSmallIntegerField(default=60),
        ),
        migrations.AddField(
            model_name='rotadeliverypublica',
            name='rfm_r5_dias',
            field=models.PositiveSmallIntegerField(default=30),
        ),
    ]
