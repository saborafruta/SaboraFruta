from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0056_entrega_contas_receber'),
    ]

    operations = [
        migrations.AddField(
            model_name='usuario',
            name='preferencias_tabelas',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
