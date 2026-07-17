from django.db import migrations, models

import apps.core.constants.choices


class Migration(migrations.Migration):

    dependencies = [
        ('fiscal', '0006_rename_regras_fisc_uf_aa0e88_idx_regras_fisc_uf_390d95_idx_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='AliquotaIBPT',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('ncm', models.CharField(db_index=True, max_length=8)),
                ('uf', models.CharField(choices=apps.core.constants.choices.UF.choices, db_index=True, max_length=2)),
                ('descricao', models.CharField(blank=True, max_length=500)),
                ('federal_nacional', models.DecimalField(decimal_places=4, max_digits=7)),
                ('federal_importado', models.DecimalField(decimal_places=4, max_digits=7)),
                ('estadual', models.DecimalField(decimal_places=4, max_digits=7)),
                ('municipal', models.DecimalField(decimal_places=4, max_digits=7)),
                ('fonte', models.CharField(max_length=120)),
                ('versao', models.CharField(max_length=20)),
                ('vigencia_inicio', models.DateField(db_index=True)),
                ('vigencia_fim', models.DateField(db_index=True)),
            ],
            options={
                'verbose_name': 'Aliquota IBPT',
                'verbose_name_plural': 'Aliquotas IBPT',
                'db_table': 'aliquotas_ibpt',
                'ordering': ['uf', 'ncm', '-vigencia_inicio'],
            },
        ),
        migrations.AddConstraint(
            model_name='aliquotaibpt',
            constraint=models.UniqueConstraint(fields=('uf', 'ncm', 'versao'), name='uq_ibpt_uf_ncm_versao'),
        ),
        migrations.AddIndex(
            model_name='aliquotaibpt',
            index=models.Index(fields=['uf', 'ncm', 'vigencia_inicio', 'vigencia_fim'], name='ibpt_uf_ncm_vigencia_idx'),
        ),
    ]
