from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pdv', '0014_corrigir_liquidacao_d0_recente'),
    ]

    operations = [
        migrations.AddField(
            model_name='itemvendapdv',
            name='oferta_contexto',
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text='Regra comercial escolhida no PDV para retomar pendentes e orçamentos.',
            ),
        ),
    ]
