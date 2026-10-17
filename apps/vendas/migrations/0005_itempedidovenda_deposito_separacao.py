from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('estoque', '0023_remove_conferenciatransferencia_etapa'),
        ('vendas', '0004_itempedidovenda_item_substituido_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='itempedidovenda',
            name='deposito_separacao',
            field=models.ForeignKey(
                blank=True,
                help_text='Depósito físico escolhido pelo estoquista para retirar o item.',
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='itens_pedido_separados',
                to='estoque.deposito',
            ),
        ),
    ]
