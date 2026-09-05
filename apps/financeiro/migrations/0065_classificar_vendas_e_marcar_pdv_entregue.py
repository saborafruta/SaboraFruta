from django.db import migrations


CODIGO_VENDAS_PRODUTOS = '3100100001'


def classificar_origens_comerciais(apps, schema_editor):
    ContaReceber = apps.get_model('financeiro', 'ContaReceber')
    PlanoContas = apps.get_model('financeiro', 'PlanoContas')

    ContaReceber.objects.filter(documento_tipo='venda_pdv').update(
        status_entrega='entregue',
        data_entrega_prevista=None,
        previsao_entrega_complemento='',
    )

    for categoria in PlanoContas.objects.filter(
        codigo=CODIGO_VENDAS_PRODUTOS,
        tipo='R',
        ativo=True,
    ):
        ContaReceber.objects.filter(
            filial__empresa_id=categoria.empresa_id,
            documento_tipo__in=['venda_pdv', 'pedido_moda'],
            plano_contas__isnull=True,
        ).update(
            plano_contas_id=categoria.pk,
            conta_contabil_id=categoria.conta_contabil_id,
        )


class Migration(migrations.Migration):
    dependencies = [('financeiro', '0064_exclusao_receber_e_categorias_operacionais')]

    operations = [
        migrations.RunPython(classificar_origens_comerciais, migrations.RunPython.noop),
    ]
