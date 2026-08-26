from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('moda', '0039_pedido_conta_bancaria_entrada')]

    operations = [
        migrations.AddField(
            model_name='itempedidoproducao',
            name='status_fluxo',
            field=models.CharField(
                choices=[
                    ('orcamento', 'Orçamento'), ('aprovado', 'Pedido aprovado'),
                    ('producao', 'Produção'), ('pronto', 'Pronto para retirada'),
                    ('entregue', 'Entregue'),
                ],
                db_index=True, default='orcamento', max_length=15,
            ),
        ),
        migrations.AddField(
            model_name='itempedidoproducao',
            name='quantidade_entregue',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AlterField(
            model_name='pedidoproducao',
            name='status',
            field=models.CharField(
                choices=[
                    ('orcamento', 'Orçamento'), ('confirmado', 'Pedido aprovado'),
                    ('aguardando_arte', 'Aguardando Arte'),
                    ('aguardando_aprovacao', 'Aguardando Aprovação'),
                    ('aguardando_material', 'Aguardando Material'),
                    ('liberado_producao', 'Liberado para Produção'),
                    ('em_producao', 'Em Produção'), ('em_acabamento', 'Em Acabamento'),
                    ('pronto', 'Pronto para retirada'), ('entregue', 'Entregue'),
                    ('cancelado', 'Cancelado'),
                ], default='orcamento', max_length=25, db_index=True,
            ),
        ),
    ]
