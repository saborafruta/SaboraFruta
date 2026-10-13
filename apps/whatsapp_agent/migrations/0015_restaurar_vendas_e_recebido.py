from django.db import migrations, models


TRECHO_ENTRADAS = '💰 *Entradas financeiras:* {entradas_financeiras}\n'
TRECHO_VENDAS_RECEBIDO = (
    '💰 *Vendas:* {vendas}\n'
    '💵 *Recebido:* {recebido}\n'
)


def restaurar_vendas_e_recebido(apps, schema_editor):
    Configuracao = apps.get_model('whatsapp_agent', 'ConfiguracaoWhatsAppCentral')
    alias = schema_editor.connection.alias
    for configuracao in Configuracao.objects.using(alias).all():
        mensagem = (configuracao.mensagem_resumo or '').replace('\r\n', '\n')
        atualizada = mensagem.replace(TRECHO_ENTRADAS, TRECHO_VENDAS_RECEBIDO)
        if atualizada != mensagem:
            configuracao.mensagem_resumo = atualizada
            configuracao.save(using=alias, update_fields=['mensagem_resumo'])


class Migration(migrations.Migration):
    dependencies = [
        ('whatsapp_agent', '0014_atualizar_modelos_resumo_legados'),
    ]

    operations = [
        migrations.AlterField(
            model_name='configuracaowhatsappcentral',
            name='mensagem_resumo',
            field=models.TextField(
                default=(
                    '📊 *Resumo diário — {filial}*\n'
                    '🗓️ {data}\n\n'
                    '💰 *Vendas:* {vendas}\n'
                    '💵 *Recebido:* {recebido}\n'
                    '🧾 *Quantidade de vendas:* {quantidade_vendas}\n'
                    '🎯 *Ticket médio:* {ticket_medio}\n'
                    '🏷️ *Descontos:* {descontos}\n'
                    '↩️ *Cancelamentos:* {cancelamentos}\n\n'
                    '⚠️ *Estoque crítico:* {estoque_critico}\n'
                    '⏳ *Produtos próximos do vencimento:* {vencimentos}\n'
                    '📌 *Contas a receber vencidas:* {contas_vencidas}\n'
                    '📈 *Comparação com o dia anterior:* {comparacao}'
                    '{agenda}'
                ),
                help_text=(
                    'Variáveis: {empresa}, {filial}, {data}, '
                    '{entradas_financeiras}, {vendas}, {recebido}, '
                    '{quantidade_vendas}, {ticket_medio}, {descontos}, '
                    '{cancelamentos}, {estoque_critico}, {vencimentos}, '
                    '{contas_vencidas}, {comparacao} e {agenda}.'
                ),
            ),
        ),
        migrations.RunPython(restaurar_vendas_e_recebido, migrations.RunPython.noop),
    ]
