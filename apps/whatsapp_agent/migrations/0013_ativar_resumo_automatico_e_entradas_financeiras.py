from django.db import migrations, models


MODELO_ANTERIOR = (
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
)

MODELO_ENTRADAS = (
    '📊 *Resumo diário — {filial}*\n'
    '🗓️ {data}\n\n'
    '💰 *Entradas financeiras:* {entradas_financeiras}\n'
    '🧾 *Quantidade de vendas:* {quantidade_vendas}\n'
    '🎯 *Ticket médio:* {ticket_medio}\n'
    '🏷️ *Descontos:* {descontos}\n'
    '↩️ *Cancelamentos:* {cancelamentos}\n\n'
    '⚠️ *Estoque crítico:* {estoque_critico}\n'
    '⏳ *Produtos próximos do vencimento:* {vencimentos}\n'
    '📌 *Contas a receber vencidas:* {contas_vencidas}\n'
    '📈 *Comparação com o dia anterior:* {comparacao}'
    '{agenda}'
)


def ativar_resumos_automaticos(apps, schema_editor):
    Configuracao = apps.get_model('whatsapp_agent', 'ConfiguracaoWhatsAppCentral')
    Parametros = apps.get_model('core', 'ParametrosSistema')
    Destinatario = apps.get_model('core', 'DestinatarioResumoWhatsApp')
    alias = schema_editor.connection.alias

    configuracao, _ = Configuracao.objects.using(alias).get_or_create(pk=1)
    campos = []
    if not configuracao.resumos_ativos:
        configuracao.resumos_ativos = True
        campos.append('resumos_ativos')
    if configuracao.mensagem_resumo == MODELO_ANTERIOR:
        configuracao.mensagem_resumo = MODELO_ENTRADAS
        campos.append('mensagem_resumo')
    if campos:
        configuracao.save(using=alias, update_fields=campos)

    parametros_com_destinatarios = Destinatario.objects.using(alias).filter(
        ativo=True,
    ).exclude(
        telefone='',
    ).values_list('parametros_id', flat=True)
    Parametros.objects.using(alias).filter(
        pk__in=parametros_com_destinatarios,
    ).update(resumo_whatsapp_ativo=True)


class Migration(migrations.Migration):
    dependencies = [
        ('whatsapp_agent', '0012_configuracaowhatsapp_mensagem_pedido_pronto_retirada'),
    ]

    operations = [
        migrations.AlterField(
            model_name='configuracaowhatsappcentral',
            name='resumos_ativos',
            field=models.BooleanField(
                default=True,
                help_text='Ativa a preparação automática dos resumos diários.',
            ),
        ),
        migrations.AlterField(
            model_name='configuracaowhatsappcentral',
            name='mensagem_resumo',
            field=models.TextField(
                default=MODELO_ENTRADAS,
                help_text=(
                    'Variáveis: {empresa}, {filial}, {data}, {entradas_financeiras}, '
                    '{vendas}, {recebido}, {quantidade_vendas}, {ticket_medio}, '
                    '{descontos}, {cancelamentos}, {estoque_critico}, {vencimentos}, '
                    '{contas_vencidas}, {comparacao} e {agenda}.'
                ),
            ),
        ),
        migrations.RunPython(ativar_resumos_automaticos, migrations.RunPython.noop),
    ]
