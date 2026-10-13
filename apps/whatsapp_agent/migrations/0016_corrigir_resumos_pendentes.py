from django.db import migrations


PREFIXO_ANTIGO = '💰 *Entradas financeiras:*'


def corrigir_resumos_pendentes(apps, schema_editor):
    EnvioResumo = apps.get_model('whatsapp_agent', 'EnvioResumoWhatsApp')
    alias = schema_editor.connection.alias
    pendentes = (
        EnvioResumo.objects.using(alias)
        .filter(
            status__in=['pendente', 'erro'],
            mensagem__contains=PREFIXO_ANTIGO,
        )
        .iterator()
    )
    for envio in pendentes:
        metricas = envio.metricas or {}
        vendas = metricas.get('vendas', 'R$ 0,00')
        recebido = metricas.get('recebido') or metricas.get(
            'entradas_financeiras', 'R$ 0,00',
        )
        linhas = (envio.mensagem or '').replace('\r\n', '\n').split('\n')
        corrigidas = []
        for linha in linhas:
            if linha.startswith(PREFIXO_ANTIGO):
                corrigidas.extend([
                    f'💰 *Vendas:* {vendas}',
                    f'💵 *Recebido:* {recebido}',
                ])
            else:
                corrigidas.append(linha)
        envio.mensagem = '\n'.join(corrigidas)
        envio.save(using=alias, update_fields=['mensagem'])


class Migration(migrations.Migration):
    dependencies = [
        ('whatsapp_agent', '0015_restaurar_vendas_e_recebido'),
    ]

    operations = [
        migrations.RunPython(corrigir_resumos_pendentes, migrations.RunPython.noop),
    ]
