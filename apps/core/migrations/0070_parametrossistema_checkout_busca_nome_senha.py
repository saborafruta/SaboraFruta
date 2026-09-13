from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0069_merge_analise_custo_compra'),
    ]

    operations = [
        migrations.AddField(
            model_name='parametrossistema',
            name='checkout_busca_nome_senha_hash',
            field=models.CharField(
                blank=True,
                editable=False,
                help_text='Hash da senha usada para liberar a busca de produtos por nome no checkout.',
                max_length=128,
                verbose_name='Senha da busca por nome no checkout',
            ),
        ),
    ]
