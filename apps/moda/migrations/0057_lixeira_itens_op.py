from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('moda', '0056_peso_por_tamanho_e_grade'),
    ]

    operations = [
        migrations.AddField(
            model_name='itempedidoproducao',
            name='excluido_em',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name='itempedidoproducao',
            name='excluido_por',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='itens_op_excluidos',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
