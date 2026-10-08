import datetime
import django.core.validators
import django.db.models.deletion
from decimal import Decimal

from django.db import migrations, models

import apps.catalogo.models


class Migration(migrations.Migration):

    dependencies = [
        ('catalogo', '0002_pedidocatalogo_venda_pdv_alter_pedidocatalogo_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='catalogoconfiguracao',
            name='dias_funcionamento',
            field=models.JSONField(blank=True, default=apps.catalogo.models.dias_funcionamento_padrao),
        ),
        migrations.AddField(
            model_name='catalogoconfiguracao',
            name='horario_abertura',
            field=models.TimeField(default=datetime.time(8, 0)),
        ),
        migrations.AddField(
            model_name='catalogoconfiguracao',
            name='horario_fechamento',
            field=models.TimeField(default=datetime.time(18, 0)),
        ),
        migrations.CreateModel(
            name='CupomCatalogo',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('codigo', models.CharField(max_length=30)),
                ('tipo', models.CharField(choices=[('percentual', 'Percentual'), ('valor', 'Valor fixo')], default='percentual', max_length=12)),
                ('valor', models.DecimalField(decimal_places=2, max_digits=12, validators=[django.core.validators.MinValueValidator(Decimal('0.01'))])),
                ('pedido_minimo', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('valido_de', models.DateField(blank=True, null=True)),
                ('valido_ate', models.DateField(blank=True, null=True)),
                ('ativo', models.BooleanField(default=True)),
                ('filial', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='cupons_catalogo', to='core.filial')),
            ],
            options={
                'db_table': 'catalogo_cupons',
                'ordering': ['-ativo', 'codigo'],
            },
        ),
        migrations.AddConstraint(
            model_name='cupomcatalogo',
            constraint=models.UniqueConstraint(fields=('filial', 'codigo'), name='catalogo_cupom_filial_codigo_uniq'),
        ),
        migrations.AddField(
            model_name='pedidocatalogo',
            name='codigo_cupom',
            field=models.CharField(blank=True, max_length=30),
        ),
        migrations.AddField(
            model_name='pedidocatalogo',
            name='cupom',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='pedidos', to='catalogo.cupomcatalogo'),
        ),
        migrations.AddField(
            model_name='pedidocatalogo',
            name='valor_desconto',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
    ]
