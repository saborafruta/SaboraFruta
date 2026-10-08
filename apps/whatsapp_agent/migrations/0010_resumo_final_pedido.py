from django.db import migrations, models


MENSAGEM_ANTIGA = (
    '✅ *Pedido confirmado {numero}*\n\n'
    '{itens}\n\n'
    '*Subtotal:* {subtotal}\n'
    '*Frete:* {frete}\n'
    '*Total:* {total}\n'
    '*Recebimento:* {entrega}\n'
    '*Pagamento:* {pagamento}\n'
    '*Observação:* {observacao}'
)
MENSAGEM_NOVA = (
    '🧾 *Resumo do pedido {numero}*\n\n'
    '{itens}\n\n'
    '*Subtotal:* {subtotal}\n'
    '*Frete:* {frete}\n'
    '*Total:* {total}\n'
    '*Recebimento:* {entrega}\n'
    '*Pagamento:* {pagamento}\n'
    '*Observação:* {observacao}'
)


def atualizar_resumo_padrao(apps, schema_editor):
    ConfiguracaoWhatsApp = apps.get_model('whatsapp_agent', 'ConfiguracaoWhatsApp')
    ConfiguracaoWhatsApp.objects.filter(
        mensagem_resumo_pedido=MENSAGEM_ANTIGA,
    ).update(mensagem_resumo_pedido=MENSAGEM_NOVA)


class Migration(migrations.Migration):

    dependencies = [
        ('whatsapp_agent', '0009_catalogo_confirma_no_site'),
    ]

    operations = [
        migrations.RunPython(atualizar_resumo_padrao, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='configuracaowhatsapp',
            name='mensagem_resumo_pedido',
            field=models.TextField(
                default=MENSAGEM_NOVA,
                help_text=(
                    'Variáveis: {numero}, {nome}, {itens}, {subtotal}, {frete}, '
                    '{total}, {entrega}, {pagamento} e {observacao}.'
                ),
            ),
        ),
    ]
