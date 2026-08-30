from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('pdv', '0015_itemvendapdv_oferta_contexto')]
    operations = [migrations.AddField(
        model_name='vendapdv', name='comprovante_token',
        field=models.CharField(max_length=64, unique=True, null=True, blank=True),
    )]
