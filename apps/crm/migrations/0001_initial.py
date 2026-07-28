import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('cadastros', '0010_praca_rota'),
        ('core', '0033_permissao_modulo_crm'),
    ]

    operations = [
        migrations.CreateModel(
            name='RecompraControle',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('ultima_execucao', models.DateTimeField(blank=True, null=True)),
                ('empresa', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='recompra_controle', to='core.empresa',
                )),
            ],
            options={
                'verbose_name': 'Controle de Recálculo de Recompra',
                'verbose_name_plural': 'Controles de Recálculo de Recompra',
                'db_table': 'crm_recompra_controle',
            },
        ),
        migrations.CreateModel(
            name='RecompraCliente',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('media_intervalo_dias', models.DecimalField(decimal_places=2, default=0, max_digits=7)),
                ('desvio_padrao_dias', models.DecimalField(decimal_places=2, default=0, max_digits=7)),
                ('qtd_compras', models.PositiveIntegerField(default=0)),
                ('frequencia', models.CharField(
                    choices=[
                        ('semanal', 'Compra semanal'), ('quinzenal', 'Compra quinzenal'),
                        ('mensal', 'Compra mensal'), ('personalizada', 'Compra personalizada'),
                        ('sem_padrao', 'Padrão insuficiente'),
                    ],
                    db_index=True, default='sem_padrao', max_length=20,
                )),
                ('primeira_compra', models.DateField(blank=True, null=True)),
                ('ultima_compra', models.DateField(blank=True, null=True)),
                ('proxima_compra_prevista', models.DateField(blank=True, null=True)),
                ('dias_restantes', models.IntegerField(
                    blank=True, help_text='Negativo = já passou da data prevista.', null=True,
                )),
                ('status', models.CharField(
                    choices=[
                        ('verde', 'Em dia'), ('amarelo', 'Próximo da recompra'),
                        ('vermelho', 'Em atraso'), ('cinza', 'Sem histórico suficiente'),
                    ],
                    db_index=True, default='cinza', max_length=10,
                )),
                ('valor_medio', models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ('valor_total_periodo', models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ('score', models.PositiveSmallIntegerField(
                    default=0, help_text='0-100. Quanto maior, mais vale a pena contatar agora.',
                )),
                ('nivel_confianca', models.DecimalField(
                    decimal_places=3, default=0,
                    help_text='0-1. Quão regular é o cliente (baixo desvio = alta confiança).',
                    max_digits=4,
                )),
                ('ultima_atualizacao', models.DateTimeField(auto_now=True, db_index=True)),
                ('cliente', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='recompras', to='cadastros.cliente',
                )),
                ('filial', models.ForeignKey(
                    db_index=True, help_text='Filial proprietária do registro',
                    on_delete=django.db.models.deletion.PROTECT, related_name='+', to='core.filial',
                )),
                ('representante', models.ForeignKey(
                    blank=True, help_text='Representante do pedido mais recente do cliente.',
                    null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='recompras_clientes', to='cadastros.representante',
                )),
            ],
            options={
                'verbose_name': 'Recompra do Cliente',
                'verbose_name_plural': 'Recompras dos Clientes',
                'db_table': 'crm_recompra_cliente',
                'ordering': ['-score'],
            },
        ),
        migrations.AddIndex(
            model_name='recompracliente',
            index=models.Index(fields=['filial', 'status', '-score'], name='crm_recompr_filial__f3a1c2_idx'),
        ),
        migrations.AddIndex(
            model_name='recompracliente',
            index=models.Index(fields=['filial', 'proxima_compra_prevista'], name='crm_recompr_filial__8b7e4d_idx'),
        ),
        migrations.AddIndex(
            model_name='recompracliente',
            index=models.Index(fields=['filial', 'frequencia'], name='crm_recompr_filial__2d9a06_idx'),
        ),
        migrations.AddConstraint(
            model_name='recompracliente',
            constraint=models.UniqueConstraint(
                fields=('cliente', 'filial'), name='uniq_recompra_cliente_filial',
            ),
        ),
    ]
