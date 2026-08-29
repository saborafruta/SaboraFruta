from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cadastros', '0001_initial'),
        ('moda', '0045_personalizacao_individual_por_tamanho'),
    ]

    operations = [
        migrations.AddField(
            model_name='pedidoproducao',
            name='clientes_adicionais',
            field=models.ManyToManyField(
                blank=True,
                help_text='Outros clientes que participam da mesma OP/orçamento.',
                related_name='pedidos_moda_compartilhados',
                to='cadastros.cliente',
            ),
        ),
    ]
