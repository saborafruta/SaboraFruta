from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    """
    Campos para suportar o estorno (cancelamento) de transferências entre
    lojas: marca quando uma movimentação foi cancelada, por quem e quando.
    """

    dependencies = [
        ('estoque', '0006_movimentacaoestoque_documento_fiscal'),
        ('core', '0030_criar_tabela_registros_auditoria'),
    ]

    operations = [
        migrations.AddField(
            model_name='movimentacaoestoque',
            name='transferencia_cancelada',
            field=models.BooleanField(default=False, db_index=True),
        ),
        migrations.AddField(
            model_name='movimentacaoestoque',
            name='transferencia_cancelada_em',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='movimentacaoestoque',
            name='transferencia_cancelada_por',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='+', to='core.usuario',
            ),
        ),
    ]
