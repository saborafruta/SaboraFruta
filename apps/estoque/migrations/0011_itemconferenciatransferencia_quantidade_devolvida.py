from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('estoque', '0010_alter_itemconferenciatransferencia_ocorrencia'),
    ]

    operations = [
        migrations.AddField(
            model_name='itemconferenciatransferencia',
            name='quantidade_devolvida',
            field=models.DecimalField(
                decimal_places=3,
                default=0,
                max_digits=12,
            ),
        ),
    ]
