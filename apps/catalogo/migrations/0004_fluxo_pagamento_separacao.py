from django.db import migrations


def pedidos_pagos_anteriores_estavam_prontos(apps, schema_editor):
    PedidoCatalogo = apps.get_model('catalogo', 'PedidoCatalogo')
    PedidoCatalogo.objects.filter(status='pago').update(status='pronto')


class Migration(migrations.Migration):
    dependencies = [
        ('catalogo', '0003_catalogo_horarios_cupons'),
    ]

    operations = [
        migrations.RunPython(
            pedidos_pagos_anteriores_estavam_prontos,
            migrations.RunPython.noop,
        ),
    ]
