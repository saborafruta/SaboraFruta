from django.db import migrations


def encerrar_sessoes_agendadas(apps, schema_editor):
    ConversaWhatsApp = apps.get_model('whatsapp_agent', 'ConversaWhatsApp')
    ConversaWhatsApp.objects.filter(
        ativa=True,
        etapa_crm='agendado',
    ).update(
        ativa=False,
        atendimento_humano=False,
        etapa='encerrada',
    )


class Migration(migrations.Migration):

    dependencies = [
        ('whatsapp_agent', '0020_sessoes_conversa'),
    ]

    operations = [
        migrations.RunPython(
            encerrar_sessoes_agendadas,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
