from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('whatsapp_agent', '0011_configuracaowhatsapp_pedido_status_notificados'),
    ]

    operations = [
        migrations.AddField(
            model_name='configuracaowhatsapp',
            name='mensagem_pedido_pronto_retirada',
            field=models.TextField(
                default=(
                    '✅ *Pedido {numero} pronto para retirada!*\n\n'
                    'Olá, {nome}! Seu pedido já foi separado e pode ser retirado na loja.\n'
                    'Total: {total}'
                ),
                help_text='Variáveis disponíveis: {numero}, {nome} e {total}.',
            ),
        ),
    ]
