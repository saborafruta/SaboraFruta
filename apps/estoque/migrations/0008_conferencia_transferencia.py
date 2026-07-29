from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0036_notificacao'),
        ('estoque', '0007_movimentacaoestoque_cancelamento'),
        ('produtos', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='ConferenciaTransferencia',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('documento_numero', models.CharField(max_length=20, unique=True)),
                ('status', models.CharField(choices=[('aguardando', 'Aguardando conferencia'), ('em_conferencia', 'Em conferencia'), ('conferida', 'Conferida'), ('com_divergencia', 'Conferida com divergencia'), ('cancelada', 'Cancelada')], db_index=True, default='aguardando', max_length=24)),
                ('observacao_origem', models.TextField(blank=True)),
                ('observacao_conferencia', models.TextField(blank=True)),
                ('conferida_em', models.DateTimeField(blank=True, null=True)),
                ('conferida_por', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='transferencias_conferidas', to='core.usuario')),
                ('criada_por', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='transferencias_criadas_conferencia', to='core.usuario')),
                ('filial_destino', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='transferencias_recebidas_conferencia', to='core.filial')),
                ('filial_origem', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='transferencias_enviadas_conferencia', to='core.filial')),
            ],
            options={'db_table': 'transferencias_conferencias', 'ordering': ['-created_at']},
        ),
        migrations.CreateModel(
            name='ItemConferenciaTransferencia',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('quantidade_enviada', models.DecimalField(decimal_places=3, max_digits=12)),
                ('quantidade_recebida', models.DecimalField(decimal_places=3, max_digits=12)),
                ('ocorrencia', models.CharField(choices=[('ok', 'Recebido corretamente'), ('faltante', 'Quantidade faltante'), ('trocado', 'Item trocado')], default='ok', max_length=16)),
                ('quantidade_produto_recebido', models.DecimalField(decimal_places=3, default=0, max_digits=12)),
                ('observacao', models.CharField(blank=True, max_length=500)),
                ('conferencia', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='itens', to='estoque.conferenciatransferencia')),
                ('lote_enviado', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='estoque.loteproduto')),
                ('movimento_saida', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='item_conferencia_transferencia', to='estoque.movimentacaoestoque')),
                ('produto_enviado', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='itens_transferencia_enviados', to='produtos.produto')),
                ('produto_recebido', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='itens_transferencia_recebidos', to='produtos.produto')),
            ],
            options={'db_table': 'transferencias_conferencias_itens', 'ordering': ['pk']},
        ),
        migrations.AddIndex(
            model_name='conferenciatransferencia',
            index=models.Index(fields=['filial_destino', 'status', '-created_at'], name='transferenc_filial__df4064_idx'),
        ),
        migrations.AddIndex(
            model_name='conferenciatransferencia',
            index=models.Index(fields=['filial_origem', '-created_at'], name='transferenc_filial__49edbc_idx'),
        ),
    ]
