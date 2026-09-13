from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0066_ajustes_impressao_etiqueta_venda'),
    ]

    operations = [
        migrations.AddField(
            model_name='configuracaoetiquetavenda',
            name='tamanho_fonte_mensagem_mm',
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal('2.70'),
                help_text='Tamanho máximo da fonte da mensagem na etiqueta, em milímetros.',
                max_digits=4,
                validators=[
                    MinValueValidator(Decimal('1')),
                    MaxValueValidator(Decimal('8')),
                ],
            ),
        ),
    ]
