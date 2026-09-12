from django.db import migrations, models

import apps.core.models.parametros


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0064_etiqueta_venda_opcional'),
    ]

    operations = [
        migrations.AddField(
            model_name='configuracaoetiquetavenda',
            name='layout_elementos',
            field=models.JSONField(
                blank=True,
                default=apps.core.models.parametros.layout_etiqueta_venda_padrao,
            ),
        ),
    ]
