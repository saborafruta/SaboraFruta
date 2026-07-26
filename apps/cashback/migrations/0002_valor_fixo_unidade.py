from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cashback', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='regracashbackproduto',
            name='valor_fixo_unidade',
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=10, null=True,
                help_text='Valor fixo de cashback por unidade vendida (R$). Se informado, tem prioridade sobre o percentual.',
            ),
        ),
        migrations.AddField(
            model_name='regracashbackcategoria',
            name='valor_fixo_unidade',
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=10, null=True,
                help_text='Valor fixo de cashback por unidade vendida (R$). Se informado, tem prioridade sobre o percentual.',
            ),
        ),
        migrations.AddField(
            model_name='regracashbackfilial',
            name='valor_fixo_unidade',
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=10, null=True,
                help_text='Valor fixo de cashback por unidade vendida (R$). Se informado, tem prioridade sobre o percentual.',
            ),
        ),
        migrations.AddField(
            model_name='regracashbackempresa',
            name='valor_fixo_unidade',
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=10, null=True,
                help_text='Valor fixo de cashback por unidade vendida (R$). Se informado, tem prioridade sobre o percentual.',
            ),
        ),
    ]
