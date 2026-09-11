"""
Fase 5: inventário passa a contar UM depósito.

Antes o inventário fotografava `(produto, filial)` — com dois depósitos
na filial isso comparava a contagem contra um saldo qualquer. Agora cada
inventário aponta um depósito. Os inventários já existentes recebem o
depósito padrão da filial (era o único quando foram feitos).

Campo entra `null=True`, RunPython preenche, `AlterField` torna
obrigatório — o caminho seguro para FK NOT NULL em tabela com dados.
"""
from django.db import migrations, models
import django.db.models.deletion


def apontar_deposito_padrao(apps, schema_editor):
    Inventario = apps.get_model('estoque', 'Inventario')
    Deposito = apps.get_model('estoque', 'Deposito')
    db = schema_editor.connection.alias

    padrao_por_filial = {}
    for inv in Inventario.objects.using(db).filter(deposito__isnull=True):
        dep_id = padrao_por_filial.get(inv.filial_id)
        if dep_id is None:
            dep = (
                Deposito.objects.using(db)
                .filter(filial_id=inv.filial_id, is_padrao=True)
                .first()
            )
            if dep is None:
                dep, _ = Deposito.objects.using(db).get_or_create(
                    filial_id=inv.filial_id, nome='Estoque Geral',
                    defaults={'is_padrao': True, 'tipo': 'geral'},
                )
            dep_id = dep.pk
            padrao_por_filial[inv.filial_id] = dep_id
        inv.deposito_id = dep_id
        inv.save(update_fields=['deposito'])


class Migration(migrations.Migration):

    dependencies = [
        ('estoque', '0014_deposito_local_estoque'),
    ]

    operations = [
        migrations.AddField(
            model_name='inventario',
            name='deposito',
            field=models.ForeignKey(
                null=True, help_text='Depósito contado neste inventário.',
                on_delete=django.db.models.deletion.PROTECT,
                related_name='inventarios', to='estoque.deposito',
            ),
        ),
        migrations.RunPython(apontar_deposito_padrao, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='inventario',
            name='deposito',
            field=models.ForeignKey(
                help_text='Depósito contado neste inventário.',
                on_delete=django.db.models.deletion.PROTECT,
                related_name='inventarios', to='estoque.deposito',
            ),
        ),
    ]
