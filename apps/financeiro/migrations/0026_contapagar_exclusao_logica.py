from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('financeiro', '0025_formapagamento_conta_bancaria_padrao'),
    ]

    operations = [
        migrations.AddField(
            model_name='contapagar',
            name='excluido_em',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name='contapagar',
            name='excluido_por',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='contas_pagar_excluidas',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='contapagar',
            name='motivo_exclusao',
            field=models.CharField(blank=True, max_length=300),
        ),
    ]
