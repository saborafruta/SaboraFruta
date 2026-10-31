from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('produtos', '0044_unidades_comerciais_e_catalogo_ited'),
        ('vendas', '0006_pedidovenda_idempotency_key_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='itempedidovenda',
            name='apresentacao',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='+', to='produtos.produtoapresentacao',
            ),
        ),
        migrations.AddField(
            model_name='itempedidovenda', name='apresentacao_descricao',
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name='itempedidovenda', name='apresentacao_unidade',
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name='itempedidovenda', name='apresentacao_fator_conversao',
            field=models.DecimalField(
                blank=True, decimal_places=6, max_digits=14, null=True,
            ),
        ),
        migrations.AddField(
            model_name='itempedidovenda', name='quantidade_comercial',
            field=models.DecimalField(
                blank=True, decimal_places=3, max_digits=12, null=True,
            ),
        ),
        migrations.AddField(
            model_name='itempedidovenda', name='valor_unitario_comercial',
            field=models.DecimalField(
                blank=True, decimal_places=4, max_digits=14, null=True,
            ),
        ),
    ]
