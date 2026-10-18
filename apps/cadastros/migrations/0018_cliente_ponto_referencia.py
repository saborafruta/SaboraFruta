from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cadastros', '0017_funcionario_foto'),
    ]

    operations = [
        migrations.AddField(
            model_name='cliente',
            name='ponto_referencia',
            field=models.CharField(blank=True, max_length=120),
        ),
    ]
