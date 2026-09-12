from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0065_layout_elementos_etiqueta_venda'),
    ]

    operations = [
        migrations.AddField(
            model_name='configuracaoetiquetavenda',
            name='alta_nitidez',
            field=models.BooleanField(default=False, help_text='Reforça contraste, contornos do texto e renderização da logo.'),
        ),
        migrations.AddField(
            model_name='configuracaoetiquetavenda',
            name='deslocamento_horizontal_mm',
            field=models.DecimalField(decimal_places=2, default=Decimal('-1.00'), help_text='Use valor negativo para levar toda a impressão para a esquerda.', max_digits=5, validators=[MinValueValidator(Decimal('-10')), MaxValueValidator(Decimal('10'))]),
        ),
        migrations.AddField(
            model_name='configuracaoetiquetavenda',
            name='deslocamento_vertical_mm',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), help_text='Use valor negativo para subir toda a impressão.', max_digits=5, validators=[MinValueValidator(Decimal('-10')), MaxValueValidator(Decimal('10'))]),
        ),
        migrations.AddField(
            model_name='configuracaoetiquetavenda',
            name='margem_interna_mm',
            field=models.DecimalField(decimal_places=2, default=Decimal('1.00'), help_text='Área de segurança entre o conteúdo e as bordas da etiqueta.', max_digits=4, validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('10'))]),
        ),
    ]
