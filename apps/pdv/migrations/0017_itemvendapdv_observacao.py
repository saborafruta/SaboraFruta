from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('pdv', '0016_vendapdv_comprovante_token')]
    operations = [migrations.AddField(
        model_name='itemvendapdv', name='observacao',
        field=models.TextField(blank=True, default=''),
    )]
