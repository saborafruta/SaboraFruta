# Migration escrita a mao: o autodetector do makemigrations tambem
# capturou varias diffs pre-existentes e nao relacionadas em EntradaNF/
# ItemEntradaNF (mesmo ruido de divergencia de versao do Django ja visto
# em outras fases -- o ambiente local tem Django 6.0.4, producao usa
# Django 5.2 via requirements.txt). Esta migration contem so' o campo
# `apresentacao` que esta fase realmente adiciona.
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("compras", "0020_cotacaocompraitem_ultima_compra_snapshot"),
        ("produtos", "0039_apresentacao_filial_e_preco"),
    ]

    operations = [
        migrations.AddField(
            model_name="itementradanf",
            name="apresentacao",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="itens_entrada_nf",
                to="produtos.produtoapresentacao",
            ),
        ),
    ]
