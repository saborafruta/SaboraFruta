from django.db import migrations


TRECHO_ANTERIOR = (
    '💰 *Vendas:* {vendas}\n'
    '💵 *Recebido:* {recebido}\n'
)
TRECHO_ENTRADAS = '💰 *Entradas financeiras:* {entradas_financeiras}\n'


def atualizar_modelos_legados(apps, schema_editor):
    Configuracao = apps.get_model('whatsapp_agent', 'ConfiguracaoWhatsAppCentral')
    alias = schema_editor.connection.alias
    for configuracao in Configuracao.objects.using(alias).all():
        mensagem = (configuracao.mensagem_resumo or '').replace('\r\n', '\n')
        atualizada = mensagem.replace(TRECHO_ANTERIOR, TRECHO_ENTRADAS)
        if atualizada != mensagem:
            configuracao.mensagem_resumo = atualizada
            configuracao.save(using=alias, update_fields=['mensagem_resumo'])


class Migration(migrations.Migration):
    dependencies = [
        ('whatsapp_agent', '0013_ativar_resumo_automatico_e_entradas_financeiras'),
    ]

    operations = [
        migrations.RunPython(atualizar_modelos_legados, migrations.RunPython.noop),
    ]
