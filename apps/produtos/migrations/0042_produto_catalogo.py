import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('produtos', '0041_produto_agendavel_produto_duracao_servico_minutos_and_more')]
    operations = [
        migrations.AddField(model_name='produto', name='exibir_catalogo', field=models.BooleanField(db_index=True, default=False, help_text='Disponibiliza o produto no link público de pedidos desta filial.', verbose_name='Exibir no catálogo digital')),
        migrations.AddField(model_name='produto', name='catalogo_destaque', field=models.BooleanField(default=False, verbose_name='Destacar no catálogo')),
        migrations.AddField(model_name='produto', name='catalogo_descricao', field=models.CharField(blank=True, max_length=240, verbose_name='Descrição no catálogo')),
        migrations.AddField(model_name='produto', name='catalogo_ordem', field=models.PositiveSmallIntegerField(default=0, verbose_name='Ordem no catálogo')),
        migrations.AddField(model_name='produto', name='catalogo_quantidade_maxima', field=models.PositiveSmallIntegerField(default=99, validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(999)], verbose_name='Quantidade máxima por pedido')),
    ]
