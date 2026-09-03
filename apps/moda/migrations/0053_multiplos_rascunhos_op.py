from uuid import uuid4

from django.db import migrations, models


def preencher_chaves(apps, schema_editor):
    RascunhoOP = apps.get_model('moda', 'RascunhoOP')
    for rascunho in RascunhoOP.objects.filter(chave__isnull=True).iterator():
        rascunho.chave = uuid4()
        rascunho.save(update_fields=['chave'])


class Migration(migrations.Migration):

    dependencies = [('moda', '0052_rascunho_item_op')]

    operations = [
        migrations.RemoveConstraint(
            model_name='rascunhoop',
            name='moda_rascunho_op_filial_usuario_unico',
        ),
        migrations.AddField(
            model_name='rascunhoop',
            name='chave',
            field=models.UUIDField(null=True, editable=False),
        ),
        migrations.RunPython(preencher_chaves, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='rascunhoop',
            name='chave',
            field=models.UUIDField(default=uuid4, editable=False, unique=True),
        ),
    ]
