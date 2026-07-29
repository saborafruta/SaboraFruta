from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pdv', '0008_vendapdv_status_delivery_finalizado'),
    ]

    operations = [
        migrations.AddField(
            model_name='vendapdv',
            name='delivery_encerrado_em',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
