"""
Fase 1 do estoque por depósito.

Cria o model `Deposito` (local de estoque dentro da filial), semeia UM
depósito padrão por filial e carimba esse depósito em todo `Estoque` e
`MovimentacaoEstoque` já existente. Como cada filial fica com um único
depósito, nada muda para o usuário — é só a fundação para as fases
seguintes (produção consumindo do depósito de fábrica, venda saindo do
depósito de loja, transferência interna sem NF-e).

O campo entra como `null=True`, o RunPython preenche e depois um
`AlterField` o torna obrigatório — o caminho seguro para adicionar FK
NOT NULL numa tabela com dados.
"""
from django.db import migrations, models
import django.db.models.deletion


def semear_depositos(apps, schema_editor):
    Filial = apps.get_model('core', 'Filial')
    Deposito = apps.get_model('estoque', 'Deposito')
    Estoque = apps.get_model('estoque', 'Estoque')
    MovimentacaoEstoque = apps.get_model('estoque', 'MovimentacaoEstoque')
    db = schema_editor.connection.alias

    for filial in Filial.objects.using(db).all():
        deposito, _ = Deposito.objects.using(db).get_or_create(
            filial_id=filial.pk,
            nome='Estoque Geral',
            defaults={'is_padrao': True, 'tipo': 'geral'},
        )
        Estoque.objects.using(db).filter(
            filial_id=filial.pk, deposito__isnull=True,
        ).update(deposito_id=deposito.pk)
        MovimentacaoEstoque.objects.using(db).filter(
            filial_id=filial.pk, deposito__isnull=True,
        ).update(deposito_id=deposito.pk)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
        ('estoque', '0013_movimentacao_cliente'),
    ]

    operations = [
        migrations.CreateModel(
            name='Deposito',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('nome', models.CharField(max_length=60)),
                ('tipo', models.CharField(choices=[('geral', 'Geral'), ('revenda', 'Revenda'), ('producao', 'Produção')], default='geral', max_length=12)),
                ('is_padrao', models.BooleanField(default=False, help_text='Depósito usado quando a operação não indica outro. Um por filial.')),
                ('permite_venda', models.BooleanField(default=True, help_text='Se desmarcado, o saldo deste depósito não fica disponível para venda.')),
                ('permite_producao', models.BooleanField(default=True, help_text='Se desmarcado, a produção não consome nem dá entrada neste depósito.')),
                ('ativo', models.BooleanField(default=True, db_index=True)),
                ('filial', models.ForeignKey(help_text='Filial proprietária do registro', on_delete=django.db.models.deletion.PROTECT, related_name='+', to='core.filial')),
            ],
            options={
                'verbose_name': 'Depósito',
                'verbose_name_plural': 'Depósitos',
                'db_table': 'estoque_depositos',
                'ordering': ['filial', 'nome'],
            },
        ),
        migrations.AddConstraint(
            model_name='deposito',
            constraint=models.UniqueConstraint(fields=('filial', 'nome'), name='deposito_nome_unico_por_filial'),
        ),
        migrations.AddConstraint(
            model_name='deposito',
            constraint=models.UniqueConstraint(condition=models.Q(('is_padrao', True)), fields=('filial',), name='deposito_um_padrao_por_filial'),
        ),
        # --- choices novas (transferência interna) ---
        migrations.AlterField(
            model_name='movimentacaoestoque',
            name='tipo_operacao',
            field=models.CharField(choices=[('entrada', 'Entrada'), ('saida', 'Saída'), ('transferencia_saida', 'Transferência (saída)'), ('transferencia_entrada', 'Transferência (entrada)'), ('transf_interna_saida', 'Transferência interna (saída)'), ('transf_interna_entrada', 'Transferência interna (entrada)'), ('ajuste_mais', 'Ajuste +'), ('ajuste_menos', 'Ajuste -'), ('inventario', 'Inventário'), ('devolucao_cliente', 'Devolução de Cliente'), ('devolucao_fornecedor', 'Devolução ao Fornecedor'), ('bonificacao', 'Bonificação'), ('roubo', 'Roubo/Furto'), ('perda', 'Perda'), ('deterioracao', 'Deterioração'), ('baixa_validade', 'Baixa por Validade'), ('uso_proprio', 'Uso Próprio'), ('brinde', 'Brinde'), ('quebra', 'Quebra'), ('producao_entrada', 'Produção (entrada)'), ('producao_saida', 'Produção (saída MP)')], db_index=True, max_length=40),
        ),
        migrations.AlterField(
            model_name='movimentacaoestoque',
            name='documento_tipo',
            field=models.CharField(blank=True, choices=[('pedido_venda', 'Pedido de Venda'), ('nfe', 'NF-e'), ('nfce', 'NFC-e'), ('outras_movimentacoes', 'Outras Movimentações'), ('inventario', 'Inventário'), ('transferencia', 'Transferência'), ('ajuste_manual', 'Ajuste Manual'), ('transferencia_interna', 'Transferência Interna'), ('ordem_producao', 'Ordem de Produção'), ('estorno_entrada', 'Estorno de Entrada'), ('comanda', 'Comanda (Food Service)')], max_length=30),
        ),
        # --- deposito nos saldos e movimentações (nullable -> backfill -> not null) ---
        migrations.AddField(
            model_name='estoque',
            name='deposito',
            field=models.ForeignKey(null=True, help_text='Local de estoque dentro da filial.', on_delete=django.db.models.deletion.PROTECT, related_name='estoques', to='estoque.deposito'),
        ),
        migrations.AddField(
            model_name='movimentacaoestoque',
            name='deposito',
            field=models.ForeignKey(null=True, help_text='Depósito movimentado.', on_delete=django.db.models.deletion.PROTECT, related_name='movimentacoes', to='estoque.deposito'),
        ),
        migrations.AddField(
            model_name='movimentacaoestoque',
            name='deposito_destino',
            field=models.ForeignKey(blank=True, null=True, help_text='Para transferência interna entre depósitos da mesma filial.', on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='estoque.deposito'),
        ),
        migrations.RunPython(semear_depositos, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='estoque',
            name='deposito',
            field=models.ForeignKey(help_text='Local de estoque dentro da filial.', on_delete=django.db.models.deletion.PROTECT, related_name='estoques', to='estoque.deposito'),
        ),
        migrations.AlterField(
            model_name='movimentacaoestoque',
            name='deposito',
            field=models.ForeignKey(help_text='Depósito movimentado.', on_delete=django.db.models.deletion.PROTECT, related_name='movimentacoes', to='estoque.deposito'),
        ),
        migrations.AlterUniqueTogether(
            name='estoque',
            unique_together={('produto', 'filial', 'deposito')},
        ),
        migrations.AddIndex(
            model_name='estoque',
            index=models.Index(fields=['deposito', 'quantidade_disponivel'], name='estoque_deposit_72b720_idx'),
        ),
    ]
