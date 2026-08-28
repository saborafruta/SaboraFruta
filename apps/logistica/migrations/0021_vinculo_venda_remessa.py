"""
Cada item vendido na rua passa a registrar a remessa que o amparou.

A REMESSA JA' ERA DESCOBRIVEL pela viagem -- mas descobrir nao e' registrar.
Cancelada e reemitida a remessa depois de vendas feitas, a busca passaria a
apontar a nota NOVA para vendas que sairam sob a ANTIGA.

O BACKFILL LIGA O QUE JA' EXISTE a' remessa viva da viagem, que e' o que a
consulta responderia hoje. Deixar nulo seria pior: nulo se le' como "saiu sem
amparo", e essa mercadoria saiu amparada.
"""
from django.db import migrations, models
import django.db.models.deletion


def ligar_a_remessa(apps, schema_editor):
    ItemVendaViagem = apps.get_model('logistica', 'ItemVendaViagem')
    DocumentoFiscal = apps.get_model('financeiro', 'DocumentoFiscal')

    mortos = ('cancelada', 'rejeitada', 'denegada')
    remessas = {}
    for documento in (
        DocumentoFiscal.objects
        .filter(origem_tipo='viagem_remessa')
        .exclude(status__in=mortos)
        .order_by('id')
    ):
        remessas[documento.origem_id] = documento.pk

    if not remessas:
        return

    for item in ItemVendaViagem.objects.select_related('venda').iterator():
        remessa_id = remessas.get(item.venda.viagem_id)
        if remessa_id and not item.remessa_id:
            item.remessa_id = remessa_id
            item.save(update_fields=['remessa'])


def desligar(apps, schema_editor):
    """O reverso desfaz só o que esta migração ligou — o campo some junto."""


class Migration(migrations.Migration):

    dependencies = [
        ('logistica', '0020_entrega_por_tipo'),
        ('financeiro', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='itemvendaviagem',
            name='remessa',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='itens_venda_viagem',
                to='financeiro.documentofiscal',
                help_text='NF-e de remessa sob a qual esta mercadoria saiu.',
            ),
        ),
        migrations.RunPython(ligar_a_remessa, desligar),
    ]
