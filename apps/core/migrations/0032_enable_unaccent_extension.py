from django.contrib.postgres.operations import UnaccentExtension
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0031_alter_parametrossistema_certificado_base64_and_more'),
    ]

    operations = [
        UnaccentExtension(),
    ]
