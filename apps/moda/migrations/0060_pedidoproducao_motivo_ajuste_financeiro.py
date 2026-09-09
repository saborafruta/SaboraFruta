from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('moda', '0059_alter_aviamento_observacao_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='pedidoproducao',
            name='motivo_ajuste_financeiro',
            field=models.TextField(
                blank=True,
                help_text=(
                    'Motivo comercial obrigatório quando o pedido recebe desconto '
                    'ou acréscimo. As alterações ficam disponíveis no histórico da OP.'
                ),
                verbose_name='Justificativa do ajuste financeiro',
            ),
        ),
    ]
