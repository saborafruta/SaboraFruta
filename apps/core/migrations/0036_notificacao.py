from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0035_desfruta_nfe_serie_5'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Notificacao',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('tipo', models.CharField(choices=[('transferencia_recebida', 'Transferencia recebida'), ('transferencia_conferida', 'Transferencia conferida'), ('alerta_sistema', 'Alerta do sistema')], db_index=True, max_length=40)),
                ('titulo', models.CharField(max_length=140)),
                ('mensagem', models.CharField(blank=True, max_length=500)),
                ('url', models.CharField(blank=True, max_length=500)),
                ('referencia_tipo', models.CharField(blank=True, max_length=50)),
                ('referencia_id', models.CharField(blank=True, max_length=80)),
                ('ativa', models.BooleanField(db_index=True, default=True)),
                ('filial', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='notificacoes', to='core.filial')),
            ],
            options={
                'db_table': 'notificacoes',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='NotificacaoLeitura',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('lida_em', models.DateTimeField(auto_now_add=True)),
                ('notificacao', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='leituras', to='core.notificacao')),
                ('usuario', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='leituras_notificacoes', to=settings.AUTH_USER_MODEL)),
            ],
            options={'db_table': 'notificacoes_leituras'},
        ),
        migrations.AddField(
            model_name='notificacao',
            name='lida_por',
            field=models.ManyToManyField(blank=True, related_name='notificacoes_lidas', through='core.NotificacaoLeitura', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddIndex(
            model_name='notificacao',
            index=models.Index(fields=['filial', 'ativa', '-created_at'], name='notificacoe_filial__be68d6_idx'),
        ),
        migrations.AddIndex(
            model_name='notificacao',
            index=models.Index(fields=['referencia_tipo', 'referencia_id'], name='notificacoe_referen_608f67_idx'),
        ),
        migrations.AddConstraint(
            model_name='notificacao',
            constraint=models.UniqueConstraint(condition=models.Q(('referencia_id', ''), _negated=True), fields=('filial', 'tipo', 'referencia_tipo', 'referencia_id'), name='notificacao_referencia_unica'),
        ),
        migrations.AddConstraint(
            model_name='notificacaoleitura',
            constraint=models.UniqueConstraint(fields=('notificacao', 'usuario'), name='notificacao_leitura_usuario_unica'),
        ),
    ]
