from django.db import migrations, models

import apps.whatsapp_agent.models


class Migration(migrations.Migration):

    dependencies = [
        ('whatsapp_agent', '0010_resumo_final_pedido'),
    ]

    operations = [
        migrations.AddField(
            model_name='configuracaowhatsapp',
            name='pedido_status_notificados',
            field=models.JSONField(
                blank=True,
                default=apps.whatsapp_agent.models.status_pedido_notificados_padrao,
                help_text='Etapas do pedido que enviam uma atualização automática ao cliente.',
            ),
        ),
    ]
