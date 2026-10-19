from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0078_parametrossistema_checkout_descontos'),
    ]

    operations = [
        migrations.AddField(
            model_name='parametrossistema',
            name='checkout_permitir_estoque_negativo',
            field=models.BooleanField(
                default=True,
                help_text=(
                    'Quando ativado, o Checkout e os aplicativos Android e iOS podem '
                    'finalizar vendas mesmo quando o produto não possui saldo suficiente.'
                ),
                verbose_name='Permitir venda com estoque negativo no Checkout e aplicativos',
            ),
        ),
    ]
