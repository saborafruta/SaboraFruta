"""
Token público do PDF do pedido, em três passos.

`AddField` com `unique=True` de uma vez quebraria: todo pedido já existente
receberia string vazia, e a segunda linha violaria a restrição de unicidade
no meio do `migrate` — que roda como release command, então o container não
sobe e o site fica em 502.

A sequência é a mesma que o `qr_token` da Mesa usou: cria sem unique,
preenche linha a linha, e só então aplica a restrição.
"""
import secrets

from django.db import migrations, models


def gerar_tokens(apps, schema_editor):
    Pedido = apps.get_model('moda', 'PedidoProducao')

    # Um token por vez, com `update` direto: `bulk_update` carregaria os
    # objetos inteiros à toa, e esta migration roda uma vez só.
    for pk in Pedido.objects.filter(token_publico='').values_list('pk', flat=True):
        Pedido.objects.filter(pk=pk).update(token_publico=secrets.token_urlsafe(16))


def limpar(apps, schema_editor):
    """Reverso: o campo some junto com a coluna no passo seguinte."""


class Migration(migrations.Migration):

    dependencies = [
        ('moda', '0019_expedicao'),
    ]

    operations = [
        migrations.AddField(
            model_name='pedidoproducao',
            name='token_publico',
            field=models.CharField(blank=True, default='', editable=False, max_length=32),
        ),
        migrations.RunPython(gerar_tokens, limpar),
        migrations.AlterField(
            model_name='pedidoproducao',
            name='token_publico',
            field=models.CharField(
                blank=True, db_index=True, editable=False, max_length=32, unique=True,
            ),
        ),
    ]
