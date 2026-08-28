"""
O razão passa a dizer PARA QUEM a mercadoria saiu.

Ele já sabia o QUE saiu, QUANDO e por qual DOCUMENTO — não sabia o
destinatário. Na venda isso se descobre pelo pedido; na bonificação não
havia como: uma viagem que entrega cortesia a dois clientes gerava dois
movimentos indistinguíveis, e "para quem demos as 20 caixas?" só se
respondia por conferência manual.

O campo é opcional porque a maior parte das movimentações não tem
destinatário — produção, inventário, transferência entre filiais. Preenchê-lo
à força inventaria um cliente onde não existe um.
"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('estoque', '0012_alter_movimentacaoestoque_documento_tipo'),
        ('cadastros', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='movimentacaoestoque',
            name='cliente',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='movimentacoes_estoque',
                to='cadastros.cliente',
                help_text='Destinatário da saída, quando existe.',
            ),
        ),
    ]
