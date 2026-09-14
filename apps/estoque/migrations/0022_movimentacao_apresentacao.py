# Migration escrita a mao (mesmo motivo documentado em
# apps/compras/migrations/0021_item_entrada_apresentacao.py e
# apps/vendas/migrations/0003_item_devolucao_apresentacao.py): o
# autodetector do makemigrations tambem capturaria diffs pre-existentes e
# nao relacionados devido a divergencia de versao do Django entre o
# ambiente local (6.0.4) e producao (5.2 via requirements.txt). Esta
# migration contem so os tres campos de snapshot de apresentacao que esta
# fase adiciona em MovimentacaoEstoque.
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("estoque", "0021_nivelaprovacaotransferencia_solicitacaotransferencia_and_more"),
        ("produtos", "0039_apresentacao_filial_e_preco"),
    ]

    operations = [
        migrations.AddField(
            model_name="movimentacaoestoque",
            name="apresentacao",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="movimentacoes_estoque",
                to="produtos.produtoapresentacao",
            ),
        ),
        migrations.AddField(
            model_name="movimentacaoestoque",
            name="apresentacao_fator_conversao",
            field=models.DecimalField(
                blank=True,
                decimal_places=6,
                max_digits=14,
                null=True,
                help_text="Fator de conversao da apresentacao NO MOMENTO da movimentacao -- nao o atual.",
            ),
        ),
        migrations.AddField(
            model_name="movimentacaoestoque",
            name="quantidade_comercial",
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                max_digits=12,
                null=True,
                help_text=(
                    "Quantidade na unidade da apresentacao (ex: 2 caixas). "
                    "`quantidade` continua sendo a mesma coisa convertida pra unidade base."
                ),
            ),
        ),
    ]
