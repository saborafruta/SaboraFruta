from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0052_alerta_rendimento_polpa'),
    ]

    operations = [
        migrations.AddField(
            model_name='usuario',
            name='menu_favoritos',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
