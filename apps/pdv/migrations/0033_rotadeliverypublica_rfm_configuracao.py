from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pdv', '0032_rotadeliverypublica_configuracao_rfm'),
    ]

    operations = [
        migrations.AddField(
            model_name='rotadeliverypublica',
            name='rfm_configuracao',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
