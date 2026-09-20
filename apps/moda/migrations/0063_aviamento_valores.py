from decimal import Decimal

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("moda", "0062_aviamento_tipo_personalizado"),
    ]

    operations = [
        migrations.AddField(
            model_name="aviamento",
            name="valor_unidade",
            field=models.DecimalField(
                blank=True,
                decimal_places=4,
                help_text="Preço de UMA unidade (um metro, um botão...). Vai para o custo da ficha técnica.",
                max_digits=12,
                null=True,
                validators=[django.core.validators.MinValueValidator(Decimal("0"))],
                verbose_name="Valor da unidade (R$)",
            ),
        ),
        migrations.AddField(
            model_name="aviamento",
            name="quantidade_embalagem",
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                help_text="Quantas unidades vêm na caixa ou no rolo. Ex.: 100 botões, 50 metros.",
                max_digits=12,
                null=True,
                validators=[django.core.validators.MinValueValidator(Decimal("0"))],
                verbose_name="Unidades por caixa/rolo",
            ),
        ),
        migrations.AddField(
            model_name="aviamento",
            name="valor_embalagem",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text="Quanto custa a caixa ou o rolo inteiro.",
                max_digits=12,
                null=True,
                validators=[django.core.validators.MinValueValidator(Decimal("0"))],
                verbose_name="Valor da caixa/rolo (R$)",
            ),
        ),
    ]
