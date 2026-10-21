from decimal import Decimal

import django.db.models.deletion
from django.db import migrations, models


def migrar_localizacoes_legadas(apps, schema_editor):
    Produto = apps.get_model('produtos', 'Produto')
    Estoque = apps.get_model('estoque', 'Estoque')
    Endereco = apps.get_model('estoque', 'EnderecoEstoque')
    SaldoEndereco = apps.get_model('estoque', 'SaldoEndereco')
    db = schema_editor.connection.alias

    produtos = {
        produto.pk: produto.localizacao_estoque.strip().upper()
        for produto in Produto.objects.using(db).exclude(localizacao_estoque='')
        if produto.localizacao_estoque.strip()
    }
    if not produtos:
        return
    for estoque in (
        Estoque.objects.using(db)
        .filter(produto_id__in=produtos)
        .select_related('deposito')
        .iterator()
    ):
        endereco, _ = Endereco.objects.using(db).get_or_create(
            filial_id=estoque.filial_id,
            deposito_id=estoque.deposito_id,
            codigo=produtos[estoque.produto_id],
            defaults={'descricao': 'Migrado da localizacao do cadastro do produto.'},
        )
        SaldoEndereco.objects.using(db).get_or_create(
            estoque_id=estoque.pk,
            endereco_id=endereco.pk,
            defaults={
                'quantidade': max(Decimal('0'), estoque.quantidade_atual or Decimal('0')),
                'prioridade_separacao': 100,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ('estoque', '0023_remove_conferenciatransferencia_etapa'),
        ('produtos', '0046_produto_finalidade_vendavel'),
    ]

    operations = [
        migrations.CreateModel(
            name='EnderecoEstoque',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('codigo', models.CharField(help_text='Ex.: RUA-A / ESTANTE-03 / PRATELEIRA-02.', max_length=80)),
                ('descricao', models.CharField(blank=True, max_length=160)),
                ('ativo', models.BooleanField(db_index=True, default=True)),
                ('deposito', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='enderecos', to='estoque.deposito')),
                ('filial', models.ForeignKey(db_index=True, help_text='Filial proprietária do registro', on_delete=django.db.models.deletion.PROTECT, related_name='+', to='core.filial')),
            ],
            options={
                'verbose_name': 'Endereco de estoque',
                'verbose_name_plural': 'Enderecos de estoque',
                'db_table': 'estoque_enderecos',
                'ordering': ['deposito__nome', 'codigo', 'pk'],
            },
        ),
        migrations.CreateModel(
            name='SaldoEndereco',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('quantidade', models.DecimalField(decimal_places=3, default=0, max_digits=12)),
                ('prioridade_separacao', models.PositiveSmallIntegerField(default=100, help_text='Menor numero aparece primeiro para a separacao.')),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('endereco', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='saldos', to='estoque.enderecoestoque')),
                ('estoque', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='saldos_enderecos', to='estoque.estoque')),
            ],
            options={
                'verbose_name': 'Saldo por endereco',
                'verbose_name_plural': 'Saldos por endereco',
                'db_table': 'estoque_saldos_enderecos',
                'ordering': ['prioridade_separacao', 'endereco__codigo', 'pk'],
            },
        ),
        migrations.AddConstraint(
            model_name='enderecoestoque',
            constraint=models.UniqueConstraint(fields=('deposito', 'codigo'), name='endereco_codigo_unico_por_deposito'),
        ),
        migrations.AddConstraint(
            model_name='saldoendereco',
            constraint=models.UniqueConstraint(fields=('estoque', 'endereco'), name='saldo_unico_por_estoque_endereco'),
        ),
        migrations.AddConstraint(
            model_name='saldoendereco',
            constraint=models.CheckConstraint(condition=models.Q(('quantidade__gte', 0)), name='saldo_endereco_quantidade_nao_negativa'),
        ),
        migrations.RunPython(migrar_localizacoes_legadas, migrations.RunPython.noop),
    ]
