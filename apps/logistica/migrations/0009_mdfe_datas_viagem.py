from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('logistica', '0008_mdfe_integracao_focus'),
    ]

    operations = [
        migrations.AddField(
            model_name='mdfe',
            name='data_hora_inicio_viagem',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='mdfe',
            name='data_hora_previsao_fim',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
