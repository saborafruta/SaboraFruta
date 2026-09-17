from django.core.validators import FileExtensionValidator
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('core', '0073_filialfavorita')]

    operations = [
        migrations.AddField(
            model_name='parametrossistema',
            name='nfce_contingencia_automatica',
            field=models.BooleanField(
                default=True,
                help_text=(
                    'Permite que a Focus alterne para contingencia offline quando a SEFAZ '
                    'estiver indisponivel. Nao cobre queda total da internet da loja.'
                ),
                verbose_name='Ativar contingencia automatica da NFC-e na Focus',
            ),
        ),
        migrations.AddField(
            model_name='parametrossistema',
            name='comunicador_offline_instalador',
            field=models.FileField(
                blank=True,
                help_text='Instalador oficial fornecido pela Focus NFe (.exe, .msi ou .zip).',
                max_length=500,
                null=True,
                upload_to='fiscal/comunicador-offline/',
                validators=[FileExtensionValidator(allowed_extensions=['exe', 'msi', 'zip'])],
            ),
        ),
        migrations.AddField(
            model_name='parametrossistema',
            name='comunicador_offline_sha256',
            field=models.CharField(
                blank=True,
                editable=False,
                help_text='Assinatura SHA-256 calculada no upload para conferir a integridade.',
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name='parametrossistema',
            name='comunicador_offline_versao',
            field=models.CharField(
                blank=True,
                help_text='Versao informada pela Focus para este instalador.',
                max_length=40,
            ),
        ),
    ]
