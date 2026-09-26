from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pdv', '0029_rotadeliverypublica_etas_status_anteriores'),
    ]

    operations = [
        migrations.AddField(
            model_name='rotadeliverypublica',
            name='ordem_paradas',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='rotadeliverypublica',
            name='paradas_extras',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='rotadeliverypublica',
            name='paradas_extras_concluidas',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
