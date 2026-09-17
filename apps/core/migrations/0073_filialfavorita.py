from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('core', '0072_registroauditoria_modulo_produtos'),
    ]

    operations = [
        migrations.CreateModel(
            name='FilialFavorita',
            fields=[
                (
                    'id',
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                (
                    'filial',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='favoritada_por',
                        to='core.filial',
                    ),
                ),
                (
                    'usuario',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='filiais_favoritas',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'db_table': 'filiais_favoritas',
                'ordering': [
                    'filial__empresa__razao_social',
                    'filial__razao_social',
                ],
                'constraints': [
                    models.UniqueConstraint(
                        fields=('usuario', 'filial'),
                        name='filial_favorita_usuario_filial_unica',
                    ),
                ],
            },
        ),
    ]
