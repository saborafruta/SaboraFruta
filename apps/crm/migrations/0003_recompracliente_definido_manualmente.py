from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('crm', '0002_configuracaofaixasrecompra'),
    ]

    operations = [
        migrations.AddField(
            model_name='recompracliente',
            name='definido_manualmente',
            field=models.BooleanField(
                db_index=True, default=False,
                help_text=(
                    'Padrão informado à mão pelo usuário. O recálculo automático não '
                    'sobrescreve estes registros.'
                ),
            ),
        ),
    ]
