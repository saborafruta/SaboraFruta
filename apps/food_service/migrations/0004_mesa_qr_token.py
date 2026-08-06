import secrets

from django.db import migrations, models


def gerar_tokens(apps, schema_editor):
    """
    Mesas cadastradas antes deste campo existir ficam sem token -- gera um
    pra cada uma antes do AlterField seguinte exigir unicidade (que quebraria
    com várias linhas em branco colidindo).
    """
    Mesa = apps.get_model('food_service', 'Mesa')
    for mesa in Mesa.objects.filter(qr_token=''):
        mesa.qr_token = secrets.token_urlsafe(12)
        mesa.save(update_fields=['qr_token'])


class Migration(migrations.Migration):

    dependencies = [
        ("food_service", "0003_comanda_tipo_complementoitemcomanda"),
    ]

    operations = [
        migrations.AddField(
            model_name="mesa",
            name="qr_token",
            field=models.CharField(blank=True, editable=False, max_length=24, default=""),
            preserve_default=False,
        ),
        migrations.RunPython(gerar_tokens, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="mesa",
            name="qr_token",
            field=models.CharField(blank=True, editable=False, max_length=24, unique=True),
        ),
    ]
