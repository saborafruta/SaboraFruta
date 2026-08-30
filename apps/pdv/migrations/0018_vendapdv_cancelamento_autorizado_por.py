from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('pdv', '0017_itemvendapdv_observacao'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [migrations.AddField(
        model_name='vendapdv', name='cancelamento_autorizado_por',
        field=models.ForeignKey(blank=True, null=True,
            on_delete=django.db.models.deletion.SET_NULL,
            related_name='cancelamentos_pdv_autorizados', to=settings.AUTH_USER_MODEL),
    )]
