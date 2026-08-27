"""
Acentos nos rótulos de status e modal do manifesto.

Só muda o texto que aparece na tela: "Rodoviario" → "Rodoviário",
"Em transito" → "Em trânsito". Os valores gravados no banco continuam os
mesmos (`rodoviario`, `em_transito`), então esta migration não gera SQL —
ela existe só para o estado do Django acompanhar o modelo.

ESCRITA A MÃO, e não por `makemigrations`: o app tem renomeações de índice
pendentes de antes, e gerar automaticamente arrastaria esse DDL junto. São
mudanças de outra pessoa, e cabe a ela decidir quando aplicá-las.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('logistica', '0014_reconciliar_mdfe_2_autorizado'),
    ]

    operations = [
        migrations.AlterField(
            model_name='manifestocarga',
            name='status',
            field=models.CharField(
                choices=[
                    ('rascunho', 'Rascunho'),
                    ('emitido', 'Emitido'),
                    ('em_transito', 'Em trânsito'),
                    ('encerrado', 'Encerrado'),
                    ('cancelado', 'Cancelado'),
                ],
                db_index=True, default='rascunho', max_length=30,
            ),
        ),
        migrations.AlterField(
            model_name='manifestocarga',
            name='modal',
            field=models.CharField(
                choices=[
                    ('rodoviario', 'Rodoviário'),
                    ('aereo', 'Aéreo'),
                    ('aquaviario', 'Aquaviário'),
                    ('ferroviario', 'Ferroviário'),
                ],
                default='rodoviario', max_length=20,
            ),
        ),
    ]
