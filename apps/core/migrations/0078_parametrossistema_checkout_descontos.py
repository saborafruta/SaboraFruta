from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0077_alter_parametrossistema_resumo_whatsapp_ativo'),
    ]

    operations = [
        migrations.AddField(
            model_name='parametrossistema',
            name='checkout_arredondamento_maximo',
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal('0.00'),
                help_text='Valor máximo que o operador pode descontar para arredondar o total para baixo, sem solicitar autorização.',
                max_digits=8,
                validators=[MinValueValidator(Decimal('0'))],
                verbose_name='Arredondamento máximo',
            ),
        ),
        migrations.AddField(
            model_name='parametrossistema',
            name='checkout_desconto_percentual_1',
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal('0.00'),
                help_text='Percentual disponível no Checkout sem solicitar autorização.',
                max_digits=5,
                validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('100'))],
                verbose_name='Desconto rápido 1',
            ),
        ),
        migrations.AddField(
            model_name='parametrossistema',
            name='checkout_desconto_percentual_2',
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal('0.00'),
                help_text='Percentual disponível no Checkout sem solicitar autorização.',
                max_digits=5,
                validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('100'))],
                verbose_name='Desconto rápido 2',
            ),
        ),
        migrations.AddField(
            model_name='parametrossistema',
            name='checkout_desconto_percentual_3',
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal('0.00'),
                help_text='Percentual disponível no Checkout sem solicitar autorização.',
                max_digits=5,
                validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('100'))],
                verbose_name='Desconto rápido 3',
            ),
        ),
    ]
