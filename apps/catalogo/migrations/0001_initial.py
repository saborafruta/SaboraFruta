import django.core.validators
import django.db.models.deletion
from django.db import migrations, models

import apps.catalogo.models


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        ('core', '0077_alter_parametrossistema_resumo_whatsapp_ativo'),
        ('cadastros', '0017_funcionario_foto'),
        ('produtos', '0042_produto_catalogo'),
        ('vendas', '0003_item_devolucao_apresentacao'),
    ]
    operations = [
        migrations.CreateModel(
            name='CatalogoConfiguracao',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('ativo', models.BooleanField(default=True)),
                ('titulo', models.CharField(blank=True, max_length=120)),
                ('descricao', models.CharField(blank=True, max_length=240)),
                ('pedido_minimo', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('retirada_ativa', models.BooleanField(default=True)),
                ('entrega_ativa', models.BooleanField(default=True)),
                ('agendamento_entrega_ativo', models.BooleanField(default=True)),
                ('frete_gratis_ativo', models.BooleanField(default=False)),
                ('valor_minimo_frete_gratis', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('frete_abaixo_limite', models.CharField(choices=[('fixo', 'Valor fixo'), ('a_combinar', 'A combinar com o cliente'), ('gratis', 'Sempre grátis')], default='a_combinar', max_length=20)),
                ('valor_frete', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('prazo_minimo_entrega_horas', models.PositiveSmallIntegerField(default=1)),
                ('filial', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='configuracao_catalogo', to='core.filial')),
            ],
            options={'db_table': 'catalogo_configuracoes'},
        ),
        migrations.CreateModel(
            name='CatalogoLinkPublico',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('token', models.CharField(default=apps.catalogo.models.gerar_token, max_length=80, unique=True)),
                ('ativo', models.BooleanField(default=True)),
                ('filial', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='link_catalogo_publico', to='core.filial')),
            ],
            options={'db_table': 'catalogo_links_publicos'},
        ),
        migrations.CreateModel(
            name='PedidoCatalogo',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('numero', models.CharField(db_index=True, max_length=24)),
                ('token', models.CharField(default=apps.catalogo.models.gerar_token, max_length=80, unique=True)),
                ('conversa_id', models.PositiveBigIntegerField(blank=True, null=True)),
                ('status', models.CharField(choices=[('aguardando_cliente', 'Aguardando confirmação do cliente'), ('aguardando_loja', 'Aguardando aprovação da loja'), ('aprovado', 'Aprovado'), ('em_separacao', 'Em separação'), ('pronto', 'Pronto para entrega'), ('saiu_entrega', 'Saiu para entrega'), ('entregue', 'Entregue'), ('cancelado', 'Cancelado')], default='aguardando_cliente', max_length=28)),
                ('nome_cliente', models.CharField(max_length=150)),
                ('telefone', models.CharField(db_index=True, max_length=15)),
                ('modalidade', models.CharField(choices=[('entrega', 'Entrega'), ('retirada', 'Retirada na loja')], max_length=12)),
                ('forma_pagamento', models.CharField(choices=[('pix', 'Pix'), ('dinheiro', 'Dinheiro na entrega'), ('cartao', 'Cartão na entrega'), ('a_combinar', 'A combinar')], max_length=20)),
                ('troco_para', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ('endereco_entrega', models.JSONField(blank=True, default=dict)),
                ('entrega_em', models.DateTimeField(blank=True, null=True)),
                ('observacao', models.TextField(blank=True)),
                ('subtotal', models.DecimalField(decimal_places=2, max_digits=12)),
                ('valor_frete', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('frete_a_combinar', models.BooleanField(default=False)),
                ('total', models.DecimalField(decimal_places=2, max_digits=12)),
                ('confirmado_cliente_em', models.DateTimeField(blank=True, null=True)),
                ('aprovado_loja_em', models.DateTimeField(blank=True, null=True)),
                ('cliente', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='pedidos_catalogo', to='cadastros.cliente')),
                ('filial', models.ForeignKey(help_text='Filial proprietária do registro', on_delete=django.db.models.deletion.PROTECT, related_name='+', to='core.filial')),
                ('pedido_venda', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='origem_catalogo', to='vendas.pedidovenda')),
            ],
            options={'db_table': 'catalogo_pedidos', 'ordering': ['-created_at']},
        ),
        migrations.CreateModel(
            name='ItemPedidoCatalogo',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('descricao', models.CharField(max_length=150)),
                ('quantidade', models.PositiveIntegerField(validators=[django.core.validators.MinValueValidator(1)])),
                ('valor_unitario', models.DecimalField(decimal_places=2, max_digits=12)),
                ('valor_total', models.DecimalField(decimal_places=2, max_digits=12)),
                ('pedido', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='itens', to='catalogo.pedidocatalogo')),
                ('produto', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='produtos.produto')),
            ],
            options={'db_table': 'catalogo_pedido_itens', 'ordering': ['id']},
        ),
        migrations.AddIndex(model_name='pedidocatalogo', index=models.Index(fields=['filial', 'status'], name='catalogo_pe_filial__cdcacf_idx')),
        migrations.AddIndex(model_name='pedidocatalogo', index=models.Index(fields=['telefone', '-created_at'], name='catalogo_pe_telefon_73ff50_idx')),
    ]
