# Generated manually for OP 2.0 editable structure options.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0010_alter_politicareplicacaofilial_created_at_and_more'),
        ('moda', '0040_item_fluxo_entrega_parcial'),
    ]

    operations = [
        migrations.CreateModel(
            name='OpcaoEstruturaOP2',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('ativo', models.BooleanField(default=True, verbose_name='Ativo')),
                ('tipo_peca', models.CharField(db_index=True, max_length=40)),
                ('tipo_label', models.CharField(max_length=80)),
                ('campo', models.CharField(db_index=True, max_length=80)),
                ('valor', models.CharField(max_length=120)),
                ('ordem', models.PositiveIntegerField(default=0)),
                ('filial', models.ForeignKey(db_index=True, help_text='Filial proprietária do registro', on_delete=django.db.models.deletion.PROTECT, related_name='+', to='core.filial')),
            ],
            options={
                'verbose_name': 'Opção de estrutura da OP 2.0',
                'verbose_name_plural': 'Opções de estrutura da OP 2.0',
                'db_table': 'moda_op2_estrutura_opcoes',
                'ordering': ['tipo_label', 'campo', 'ordem', 'valor'],
                'unique_together': {('filial', 'tipo_peca', 'campo', 'valor')},
            },
        ),
        migrations.AddIndex(
            model_name='opcaoestruturaop2',
            index=models.Index(fields=['filial', 'tipo_peca', 'campo', 'ativo'], name='moda_op2_es_filial__b0208f_idx'),
        ),
    ]
