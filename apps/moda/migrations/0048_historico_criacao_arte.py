from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


def migrar_textos_existentes(apps, schema_editor):
    Pedido = apps.get_model('moda', 'PedidoProducao')
    Registro = apps.get_model('moda', 'RegistroCriacaoArte')
    registros = []
    for pedido in Pedido.objects.exclude(informacoes_criacao='').iterator():
        texto = (pedido.informacoes_criacao or '').strip()
        if texto:
            registros.append(Registro(
                pedido_id=pedido.pk,
                texto=texto,
                criado_em=pedido.updated_at or pedido.created_at,
            ))
    Registro.objects.bulk_create(registros)


def restaurar_campo_antigo(apps, schema_editor):
    Pedido = apps.get_model('moda', 'PedidoProducao')
    Registro = apps.get_model('moda', 'RegistroCriacaoArte')
    for pedido in Pedido.objects.all().iterator():
        textos = Registro.objects.filter(pedido_id=pedido.pk).order_by(
            'criado_em', 'pk',
        ).values_list('texto', flat=True)
        Pedido.objects.filter(pk=pedido.pk).update(
            informacoes_criacao='\n\n'.join(textos),
        )


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('moda', '0047_pedido_informacoes_criacao'),
    ]

    operations = [
        migrations.CreateModel(
            name='RegistroCriacaoArte',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('texto', models.TextField()),
                ('criado_em', models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ('criado_por', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='registros_criacao_arte', to=settings.AUTH_USER_MODEL)),
                ('pedido', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='historico_criacao', to='moda.pedidoproducao')),
            ],
            options={
                'db_table': 'moda_criacao_arte_historico',
                'ordering': ['-criado_em', '-pk'],
            },
        ),
        migrations.AddIndex(
            model_name='registrocriacaoarte',
            index=models.Index(fields=['pedido', '-criado_em'], name='moda_criacao_pedido_data'),
        ),
        migrations.RunPython(migrar_textos_existentes, restaurar_campo_antigo),
        migrations.RemoveField(
            model_name='pedidoproducao',
            name='informacoes_criacao',
        ),
    ]
