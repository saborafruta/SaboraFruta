from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('whatsapp_agent', '0006_configuracaowhatsappcentral_envioresumowhatsapp')]
    operations = [
        migrations.AddField(model_name='configuracaowhatsapp', name='mensagem_resumo_pedido', field=models.TextField(default='🛒 *Revise seu pedido {numero}*\n\n{itens}\n\n*Subtotal:* {subtotal}\n*Frete:* {frete}\n*Total:* {total}\n*Recebimento:* {entrega}\n*Pagamento:* {pagamento}\n*Observação:* {observacao}\n\n*1.* Confirmar pedido\n*2.* Refazer pedido\n*3.* Cancelar', help_text='Variáveis: {numero}, {nome}, {itens}, {subtotal}, {frete}, {total}, {entrega}, {pagamento} e {observacao}.')),
        migrations.AddField(model_name='configuracaowhatsapp', name='mensagem_pedido_recebido', field=models.TextField(default='Pedido {numero} confirmado! Agora a loja fará a aprovação e iniciará a separação.', help_text='Variável disponível: {numero}.')),
        migrations.AddField(model_name='configuracaowhatsapp', name='mensagem_atualizacao_pedido', field=models.TextField(default='📦 *Atualização do pedido {numero}*\n\nNovo status: *{status}*\nTotal: {total}', help_text='Variáveis disponíveis: {numero}, {status}, {nome} e {total}.')),
        migrations.AddField(model_name='configuracaowhatsapp', name='pedido_resposta_confirmar', field=models.CharField(default='1', max_length=20)),
        migrations.AddField(model_name='configuracaowhatsapp', name='pedido_resposta_alterar', field=models.CharField(default='2', max_length=20)),
        migrations.AddField(model_name='configuracaowhatsapp', name='pedido_resposta_cancelar', field=models.CharField(default='3', max_length=20)),
        migrations.AlterField(model_name='opcaomenuwhatsapp', name='acao', field=models.CharField(choices=[('agenda', 'Enviar link da agenda'), ('catalogo', 'Enviar link do catálogo'), ('atendimento_humano', 'Transferir para atendente'), ('mensagem', 'Enviar uma mensagem'), ('abrir_menu', 'Abrir outro menu'), ('menu_principal', 'Voltar ao menu principal'), ('encerrar', 'Encerrar conversa')], max_length=32)),
    ]
