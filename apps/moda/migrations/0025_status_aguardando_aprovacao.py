"""
Novo status do pedido: Aguardando Aprovação.

Só mexe em `choices` — nenhuma coluna muda de forma no banco, e nenhum
pedido existente troca de status. É a migration que o Django exige para
o estado dele bater com o modelo.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('moda', '0024_produto_erp'),
    ]

    operations = [
        migrations.AlterField(
            model_name='pedidoproducao',
            name='status',
            field=models.CharField(
                choices=[
                    ('orcamento', 'Orçamento'),
                    ('confirmado', 'Pedido Confirmado'),
                    ('aguardando_arte', 'Aguardando Arte'),
                    ('aguardando_aprovacao', 'Aguardando Aprovação'),
                    ('aguardando_material', 'Aguardando Material'),
                    ('liberado_producao', 'Liberado para Produção'),
                    ('em_producao', 'Em Produção'),
                    ('em_acabamento', 'Em Acabamento'),
                    ('pronto', 'Pronto'),
                    ('entregue', 'Entregue'),
                    ('cancelado', 'Cancelado'),
                ],
                db_index=True, default='orcamento', max_length=25,
            ),
        ),
    ]
