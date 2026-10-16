from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('whatsapp_agent', '0019_configuracaowhatsapp_ia_mensagem_erro'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='conversawhatsapp',
            name='whatsapp_conversa_remota_unica',
        ),
        migrations.AddConstraint(
            model_name='conversawhatsapp',
            constraint=models.UniqueConstraint(
                condition=models.Q(ativa=True),
                fields=('configuracao', 'remote_jid'),
                name='whatsapp_conversa_remota_ativa_unica',
            ),
        ),
    ]
