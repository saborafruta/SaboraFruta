"""
A bonificação passa a dizer POR QUE ela foi dada, e a que pedido responde.

MOTIVO EM LISTA FECHADA, e não texto livre: "por que demos 20 caixas?" é
pergunta que a auditoria faz e que o comercial precisa responder por cliente
e por período — e isso não se faz agrupando frases digitadas à mão.

Nasce VAZIO para tudo que já existe. Escolher um motivo retroativo seria
inventar a intenção de quem registrou a entrega — e o campo em branco já diz
a verdade: aquelas não tinham motivo registrado.
"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('logistica', '0021_vinculo_venda_remessa'),
        ('vendas', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='vendaviagem',
            name='motivo',
            field=models.CharField(
                blank=True,
                choices=[
                    ('comercial', 'Bonificação comercial'),
                    ('brinde', 'Brinde'),
                    ('campanha', 'Campanha promocional'),
                    ('acao', 'Ação comercial'),
                    ('relacionamento', 'Relacionamento'),
                    ('compensacao', 'Compensação'),
                    ('outro', 'Outro'),
                ],
                db_index=True,
                help_text='Por que a bonificação foi dada. Vazio em venda.',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='vendaviagem',
            name='pedido_venda',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='entregas_viagem', to='vendas.pedidovenda',
                help_text='Pedido relacionado, quando a entrega responde a um.',
            ),
        ),
    ]
