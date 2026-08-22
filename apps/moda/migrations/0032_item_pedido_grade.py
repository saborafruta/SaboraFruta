"""
`ItemPedidoProducao.grade`: a grade daquele item do pedido.

Serve para o mesmo produto entrar no pedido em mais de uma grade -- a
mesma camisa em Adulto e em OverSize, por exemplo.

Tem de ser uma linha por grade, e não uma linha só com as duas: a
quantidade mora em `ItemGradePedido`, com `unique_together
('item','tamanho')`, e as grades compartilham os MESMOS registros de
Tamanho (a sigla é única por filial). Num item só, "Adulto G = 5" e
"OverSize G = 3" cairiam na mesma chave e um apagaria o outro.

O nome é `grade_tamanho`, e não `grade`, porque `grade` já é o acessor
reverso de `ItemGradePedido.item` (`related_name='grade'`) -- é por ali que
o sistema lê as quantidades do item, inclusive no prefetch
`itens__grade__tamanho`. Chamar o campo de `grade` derruba o Django no
system check (fields.E302/E303), antes mesmo de a migration rodar.

Campo opcional: item sem grade é o caso de sempre e continua válido.
"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('moda', '0031_diagnostico_tamanhos'),
    ]

    operations = [
        migrations.AddField(
            model_name='itempedidoproducao',
            name='grade_tamanho',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='itens_pedido', to='moda.grade',
                verbose_name='Grade de tamanho',
            ),
        ),
    ]
