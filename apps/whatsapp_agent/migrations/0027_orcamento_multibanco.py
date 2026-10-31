from django.db import migrations, models


def copiar_pedido_id(apps, schema_editor):
    Orcamento = apps.get_model('whatsapp_agent', 'OrcamentoWhatsApp')
    alias = schema_editor.connection.alias
    for orcamento in Orcamento.objects.using(alias).select_related('conversa').iterator():
        orcamento.tenant_alias = orcamento.conversa.tenant_alias or 'default'
        orcamento.pedido_operacional_id = orcamento.pedido_id
        orcamento.save(
            using=alias,
            update_fields=['tenant_alias', 'pedido_operacional_id'],
        )


class Migration(migrations.Migration):
    dependencies = [
        ('whatsapp_agent', '0026_orcamentowhatsapp'),
    ]

    operations = [
        migrations.AddField(
            model_name='orcamentowhatsapp',
            name='tenant_alias',
            field=models.SlugField(db_index=True, default='default', max_length=80),
        ),
        migrations.AddField(
            model_name='orcamentowhatsapp',
            name='pedido_operacional_id',
            field=models.PositiveBigIntegerField(null=True),
        ),
        migrations.RunPython(copiar_pedido_id, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='orcamentowhatsapp',
            name='pedido',
        ),
        migrations.AlterField(
            model_name='orcamentowhatsapp',
            name='pedido_operacional_id',
            field=models.PositiveBigIntegerField(
                help_text='ID do PedidoVenda dentro do banco operacional indicado por tenant_alias.',
            ),
        ),
        migrations.AddConstraint(
            model_name='orcamentowhatsapp',
            constraint=models.UniqueConstraint(
                fields=('tenant_alias', 'pedido_operacional_id'),
                name='whatsapp_orcamento_tenant_pedido_unico',
            ),
        ),
    ]
