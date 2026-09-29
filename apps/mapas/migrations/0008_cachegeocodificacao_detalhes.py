from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mapas', '0007_posicaomotorista_destinos_pendentes'),
    ]

    operations = [
        migrations.AddField(
            model_name='cachegeocodificacao',
            name='detalhes',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
