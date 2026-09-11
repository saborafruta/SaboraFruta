"""
Fase 5: a entrada de NF-e escolhe o depósito de destino da mercadoria.

Campo opcional — vazio significa "depósito padrão da filial", que é como
toda entrada até aqui se comportou. Sem backfill: as entradas antigas
seguem com `deposito` nulo e o serviço resolve para o padrão.
"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('compras', '0017_remove_constraints_indexes'),
        ('estoque', '0014_deposito_local_estoque'),
    ]

    operations = [
        migrations.AddField(
            model_name='entradanf',
            name='deposito',
            field=models.ForeignKey(
                null=True, blank=True,
                help_text='Depósito de destino da mercadoria. Vazio = depósito padrão da filial.',
                on_delete=django.db.models.deletion.PROTECT,
                related_name='entradas_nf', to='estoque.deposito',
            ),
        ),
    ]
