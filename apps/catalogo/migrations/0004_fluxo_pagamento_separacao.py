from django.db import migrations


def pedidos_pagos_anteriores_estavam_prontos(apps, schema_editor):
    PedidoCatalogo = apps.get_model('catalogo', 'PedidoCatalogo')
    PedidoCatalogo.objects.filter(status='pago').update(status='pronto')


class Migration(migrations.Migration):
    dependencies = [
        ('catalogo', '0003_catalogoconfiguracao_frete_gratis_acima_and_more'),
    ]

    operations = [
        migrations.RunPython(
            pedidos_pagos_anteriores_estavam_prontos,
            migrations.RunPython.noop,
        ),
    ]
