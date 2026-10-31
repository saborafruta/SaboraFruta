from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('whatsapp_agent', '0029_mensagemwhatsapp_arquivo'),
    ]

    operations = [
        migrations.CreateModel(
            name='LeituraConversaWhatsApp',
            fields=[
                (
                    'id',
                    models.BigAutoField(
                        auto_created=True, primary_key=True,
                        serialize=False, verbose_name='ID',
                    ),
                ),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('usuario_email', models.EmailField(db_index=True, max_length=254)),
                ('ultima_mensagem_id', models.PositiveBigIntegerField(default=0)),
                (
                    'conversa',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='leituras_crm',
                        to='whatsapp_agent.conversawhatsapp',
                    ),
                ),
            ],
            options={
                'db_table': 'whatsapp_leituras_conversa',
                'indexes': [
                    models.Index(
                        fields=['usuario_email', 'conversa'],
                        name='whatsapp_le_usuario_0f3a8d_idx',
                    ),
                ],
                'constraints': [
                    models.UniqueConstraint(
                        fields=('conversa', 'usuario_email'),
                        name='whatsapp_leitura_conversa_usuario_unica',
                    ),
                ],
            },
        ),
    ]
