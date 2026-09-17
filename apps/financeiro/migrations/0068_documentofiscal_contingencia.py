from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('financeiro', '0067_reaplicar_data_entradas_manuais')]

    operations = [
        migrations.AddField(
            model_name='documentofiscal',
            name='em_contingencia',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='documentofiscal',
            name='data_entrada_contingencia',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='documentofiscal',
            name='resultado_envio_incerto',
            field=models.BooleanField(default=False),
        ),
    ]
