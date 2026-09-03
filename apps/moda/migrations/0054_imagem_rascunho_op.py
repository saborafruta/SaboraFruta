from django.db import migrations, models
import django.db.models.deletion
import apps.moda.models.rascunho_op


class Migration(migrations.Migration):

    dependencies = [('moda', '0053_multiplos_rascunhos_op')]

    operations = [
        migrations.CreateModel(
            name='ImagemRascunhoOP',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('item_uid', models.CharField(db_index=True, max_length=100)),
                ('arquivo', models.ImageField(upload_to=apps.moda.models.rascunho_op.caminho_imagem_rascunho)),
                ('nome_original', models.CharField(max_length=255)),
                ('criado_em', models.DateTimeField(auto_now_add=True)),
                ('rascunho', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='imagens', to='moda.rascunhoop')),
            ],
            options={
                'verbose_name': 'imagem do rascunho de OP',
                'verbose_name_plural': 'imagens dos rascunhos de OP',
                'ordering': ['criado_em', 'id'],
            },
        ),
    ]
