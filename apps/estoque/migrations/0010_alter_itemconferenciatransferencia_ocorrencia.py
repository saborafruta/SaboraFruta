from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('estoque', '0009_reabrir_notificacoes_transferencias_aguardando'),
    ]

    operations = [
        migrations.AlterField(
            model_name='itemconferenciatransferencia',
            name='ocorrencia',
            field=models.CharField(
                choices=[
                    ('ok', 'Recebido corretamente'),
                    ('faltante', 'Quantidade faltante'),
                    ('trocado', 'Item trocado'),
                    ('devolvido', 'Devolver a origem'),
                ],
                default='ok',
                max_length=16,
            ),
        ),
    ]
