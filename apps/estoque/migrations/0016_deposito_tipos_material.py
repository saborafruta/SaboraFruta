"""
Depósito de produção passa a poder se declarar dono de um ou mais tipos de
material da ficha técnica (moda) -- tecido sai de "Tecidos", aviamento de
"Aviamentos", em vez de tudo disputar um único depósito de produção por
nome. Campo opcional, sem backfill: vazio continua com o comportamento de
antes.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('estoque', '0015_inventario_deposito'),
    ]

    operations = [
        migrations.AddField(
            model_name='deposito',
            name='tipos_material',
            field=models.JSONField(
                blank=True, default=list,
                help_text=(
                    'Tipos de material da ficha técnica (moda) que este depósito '
                    'recebe automaticamente na baixa e na reserva da produção -- '
                    'ex.: só "Tecido principal". Vazio: entra na disputa genérica '
                    'de depósito de produção (o primeiro por nome), sem '
                    'distinguir tipo.'
                ),
            ),
        ),
    ]
