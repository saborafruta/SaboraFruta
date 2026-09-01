from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [('moda', '0049_alter_personalizacao_tecnica_and_more')]

    operations = [
        migrations.AddField(
            model_name='itempedidoproducao',
            name='configuracao_conjunto',
            field=models.JSONField(
                blank=True, default=dict,
                help_text=(
                    'Ficha interna do conjunto esportivo: estrutura e grades '
                    'independentes da camisa e do calção.'
                ),
            ),
        ),
        migrations.AddField(
            model_name='personalizacaoindividual',
            name='nome_calcao',
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.AddField(
            model_name='personalizacaoindividual',
            name='numero_calcao',
            field=models.CharField(blank=True, max_length=10),
        ),
        migrations.AddField(
            model_name='personalizacaoindividual',
            name='tamanho_calcao',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name='individuais_calcao', to='moda.tamanho',
                verbose_name='Tamanho do calção',
            ),
        ),
    ]
