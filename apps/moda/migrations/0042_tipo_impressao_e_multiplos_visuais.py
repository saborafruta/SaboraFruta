from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('moda', '0041_opcao_estrutura_op2'),
    ]

    operations = [
        migrations.AddField(
            model_name='produtomoda',
            name='tipo_impressao',
            field=models.CharField(
                blank=True,
                choices=[
                    ('sublimacao', 'Sublimação'),
                    ('silk', 'Silk'),
                    ('bordado', 'Bordado'),
                    ('dtf', 'DTF'),
                    ('dtg', 'DTG'),
                    ('transfer', 'Transfer'),
                    ('patch', 'Patch'),
                    ('sem_impressao', 'Sem impressão'),
                    ('outro', 'Outro'),
                ],
                help_text=(
                    'Padrão de impressão carregado automaticamente ao usar '
                    'este modelo na OP.'
                ),
                max_length=20,
                verbose_name='Tipo de impressão',
            ),
        ),
        migrations.AlterUniqueTogether(
            name='visualitempedido',
            unique_together=set(),
        ),
        migrations.AlterModelOptions(
            name='visualitempedido',
            options={
                'ordering': ['posicao', 'id'],
                'verbose_name': 'Visual do item',
                'verbose_name_plural': 'Visuais do item',
            },
        ),
        migrations.RenameIndex(
            model_name='opcaoestruturaop2',
            new_name='moda_op2_es_filial__8b4269_idx',
            old_name='moda_op2_es_filial__b0208f_idx',
        ),
    ]
