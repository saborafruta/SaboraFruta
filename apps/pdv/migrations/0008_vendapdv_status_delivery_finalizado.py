from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pdv', '0007_vendapdv_observacao'),
    ]

    operations = [
        migrations.AlterField(
            model_name='vendapdv',
            name='status_delivery',
            field=models.CharField(
                blank=True,
                choices=[
                    ('novo', 'Novo Pedido'),
                    ('preparando', 'Em Preparo'),
                    ('em_entrega', 'Saiu para Entrega'),
                    ('entregue', 'Entregue'),
                    ('finalizado', 'Finalizado'),
                    ('cancelado', 'Cancelado'),
                ],
                default='novo',
                max_length=20,
            ),
        ),
    ]
