import django.db.models.deletion
from django.db import migrations, models


def vincular_categorias_e_titulos(apps, schema_editor):
    PlanoContas = apps.get_model("financeiro", "PlanoContas")
    PlanoContabil = apps.get_model("financeiro", "PlanoContabil")
    ContaPagar = apps.get_model("financeiro", "ContaPagar")
    ContaReceber = apps.get_model("financeiro", "ContaReceber")

    contas_contabeis = {
        (conta.empresa_id, conta.classificacao): conta.id
        for conta in PlanoContabil.objects.filter(tipo_conta="A").only(
            "id", "empresa_id", "classificacao"
        )
    }

    categorias_atualizadas = []
    for categoria in PlanoContas.objects.filter(
        nivel=3,
        aceita_lancamento=True,
    ).only("id", "empresa_id", "codigo", "conta_contabil_id"):
        conta_contabil_id = contas_contabeis.get(
            (categoria.empresa_id, categoria.codigo)
        )
        if conta_contabil_id and categoria.conta_contabil_id != conta_contabil_id:
            categoria.conta_contabil_id = conta_contabil_id
            categorias_atualizadas.append(categoria)

    if categorias_atualizadas:
        PlanoContas.objects.bulk_update(
            categorias_atualizadas,
            ["conta_contabil"],
        )

    vinculos = {
        categoria.id: categoria.conta_contabil_id
        for categoria in PlanoContas.objects.exclude(
            conta_contabil_id__isnull=True
        ).only("id", "conta_contabil_id")
    }
    for ModeloTitulo in (ContaPagar, ContaReceber):
        titulos_atualizados = []
        for titulo in ModeloTitulo.objects.filter(
            plano_contas_id__in=vinculos
        ).only("id", "plano_contas_id", "conta_contabil_id"):
            conta_contabil_id = vinculos[titulo.plano_contas_id]
            if titulo.conta_contabil_id != conta_contabil_id:
                titulo.conta_contabil_id = conta_contabil_id
                titulos_atualizados.append(titulo)
        if titulos_atualizados:
            ModeloTitulo.objects.bulk_update(
                titulos_atualizados,
                ["conta_contabil"],
            )


class Migration(migrations.Migration):
    dependencies = [
        ("financeiro", "0016_plano_contabil"),
    ]

    operations = [
        migrations.AddField(
            model_name="planocontas",
            name="conta_contabil",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="categorias_financeiras",
                to="financeiro.planocontabil",
            ),
        ),
        migrations.AddField(
            model_name="contapagar",
            name="conta_contabil",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="contas_pagar_classificadas",
                to="financeiro.planocontabil",
            ),
        ),
        migrations.AddField(
            model_name="contareceber",
            name="conta_contabil",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="contas_receber_classificadas",
                to="financeiro.planocontabil",
            ),
        ),
        migrations.AlterModelOptions(
            name="planocontas",
            options={
                "ordering": ["codigo"],
                "verbose_name": "Categoria financeira",
                "verbose_name_plural": "Categorias financeiras",
            },
        ),
        migrations.RunPython(
            vincular_categorias_e_titulos,
            migrations.RunPython.noop,
        ),
    ]
