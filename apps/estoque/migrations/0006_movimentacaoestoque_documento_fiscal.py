from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    """
    Vincula a movimentacao de estoque a NF-e emitida (ex.: transferencia
    entre lojas), permitindo identificar transferencias ja concluidas que
    ainda nao tem nota fiscal — para oferecer "reemitir NF-e".
    """

    dependencies = [
        ('estoque', '0005_alter_alertavencimento_nivel_risco_and_more'),
        ('financeiro', '0011_alter_creditocliente'),
    ]

    operations = [
        migrations.AddField(
            model_name='movimentacaoestoque',
            name='documento_fiscal',
            field=models.ForeignKey(
                blank=True,
                help_text='NF-e vinculada (ex.: transferência entre lojas).',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='movimentacoes_estoque',
                to='financeiro.documentofiscal',
            ),
        ),
    ]
