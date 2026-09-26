import apps.pdv.models.rota_delivery
from django.db import migrations, models
import django.db.models.deletion


def copiar_rotas_publicas(apps, schema_editor):
    Legada = apps.get_model('pdv', 'RotaDeliveryPublica')
    Rota = apps.get_model('pdv', 'RotaDelivery')
    for antiga in Legada.objects.all().iterator():
        if not (antiga.pedido_ids or antiga.paradas_extras):
            continue
        Rota.objects.create(
            filial_id=antiga.filial_id,
            nome='Rota migrada', token=antiga.token,
            status='em_rota' if antiga.ativa else 'finalizada',
            pedido_ids=antiga.pedido_ids, pedido_etas=antiga.pedido_etas,
            pedido_status_anteriores=antiga.pedido_status_anteriores,
            paradas_extras=antiga.paradas_extras, ordem_paradas=antiga.ordem_paradas,
            paradas_extras_concluidas=antiga.paradas_extras_concluidas,
            entregador=antiga.entregador, ativa=antiga.ativa,
            publicada_em=antiga.updated_at,
            finalizada_em=None if antiga.ativa else antiga.updated_at,
        )


class Migration(migrations.Migration):
    dependencies = [('pdv', '0033_rotadeliverypublica_rfm_configuracao')]

    operations = [
        migrations.CreateModel(
            name='RotaDelivery',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('nome', models.CharField(default='Nova rota', max_length=100)),
                ('token', models.CharField(default=apps.pdv.models.rota_delivery.gerar_token_rota_delivery, editable=False, max_length=32, unique=True)),
                ('status', models.CharField(choices=[('rascunho', 'Rascunho'), ('em_rota', 'Em rota'), ('finalizada', 'Finalizada')], default='rascunho', max_length=20)),
                ('estado', models.JSONField(blank=True, default=dict)),
                ('pedido_ids', models.JSONField(blank=True, default=list)),
                ('pedido_etas', models.JSONField(blank=True, default=dict)),
                ('pedido_status_anteriores', models.JSONField(blank=True, default=dict)),
                ('paradas_extras', models.JSONField(blank=True, default=list)),
                ('ordem_paradas', models.JSONField(blank=True, default=list)),
                ('paradas_extras_concluidas', models.JSONField(blank=True, default=list)),
                ('conferencia_itens', models.JSONField(blank=True, default=dict)),
                ('entregador', models.CharField(blank=True, max_length=100)),
                ('distancia_km', models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ('tempo_total_s', models.PositiveIntegerField(default=0)),
                ('combustivel_litros', models.DecimalField(decimal_places=3, default=0, max_digits=10)),
                ('custo_combustivel', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('publicada_em', models.DateTimeField(blank=True, null=True)),
                ('finalizada_em', models.DateTimeField(blank=True, null=True)),
                ('ativa', models.BooleanField(default=True)),
                ('filial', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='rotas_delivery', to='core.filial')),
            ],
            options={'db_table': 'pdv_rotas_delivery', 'ordering': ['-created_at']},
        ),
        migrations.AddIndex(model_name='rotadelivery', index=models.Index(fields=['filial', 'status'], name='pdv_rota_filial_status_idx')),
        migrations.AddIndex(model_name='rotadelivery', index=models.Index(fields=['filial', 'finalizada_em'], name='pdv_rota_filial_fim_idx')),
        migrations.RunPython(copiar_rotas_publicas, migrations.RunPython.noop),
    ]
