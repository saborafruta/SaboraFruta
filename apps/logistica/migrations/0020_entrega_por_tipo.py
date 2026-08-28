"""
A entrega feita na rua passa a dizer o que ela é: venda ou bonificação.

O CAMPO NASCE COMO `venda` para todo mundo que já existe. É o que essas
entregas sempre foram — inventar bonificação retroativa mudaria a
conciliação de viagens já encerradas, e o valor "vendido" delas passaria a
não bater com o financeiro.

As renomeações de índice que o `makemigrations` sugere junto neste app são
pendência antiga e sem relação com isto; carregá-las aqui misturaria
alteração de índice em tabela de MDF-e com um campo de venda.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('logistica', '0019_vendas_durante_a_viagem'),
    ]

    operations = [
        migrations.AddField(
            model_name='vendaviagem',
            name='tipo',
            field=models.CharField(
                choices=[('venda', 'Venda'), ('bonificacao', 'Bonificação')],
                db_index=True,
                default='venda',
                help_text='Venda cobra; bonificação entrega sem cobrar.',
                max_length=15,
            ),
        ),
    ]
