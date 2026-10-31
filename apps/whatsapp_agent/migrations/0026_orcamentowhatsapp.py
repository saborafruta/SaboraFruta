import apps.whatsapp_agent.models
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('vendas', '0006_pedidovenda_idempotency_key_and_more'),
        ('whatsapp_agent', '0025_conexaowhatsappvendedor_somente_atendimento_humano_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='OrcamentoWhatsApp',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('token_publico', models.CharField(default=apps.whatsapp_agent.models.gerar_token_orcamento_whatsapp, editable=False, max_length=64, unique=True)),
                ('status', models.CharField(choices=[('rascunho', 'Rascunho'), ('enviado', 'Aguardando cliente'), ('aprovado', 'Aprovado'), ('recusado', 'Não aprovado')], db_index=True, default='rascunho', max_length=16)),
                ('desconto_geral_tipo', models.CharField(choices=[('valor', 'Valor (R$)'), ('percentual', 'Percentual (%)')], default='valor', max_length=12)),
                ('desconto_geral_valor', models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ('enviado_em', models.DateTimeField(blank=True, null=True)),
                ('respondido_em', models.DateTimeField(blank=True, null=True)),
                ('motivo_recusa', models.TextField(blank=True)),
                ('conversa', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='orcamentos', to='whatsapp_agent.conversawhatsapp')),
                ('pedido', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='orcamento_whatsapp', to='vendas.pedidovenda')),
            ],
            options={
                'db_table': 'whatsapp_orcamentos',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='orcamentowhatsapp',
            index=models.Index(fields=['conversa', '-created_at'], name='whatsapp_or_convers_8f6e71_idx'),
        ),
        migrations.AddIndex(
            model_name='orcamentowhatsapp',
            index=models.Index(fields=['token_publico'], name='whatsapp_or_token_p_7fe9fa_idx'),
        ),
    ]
