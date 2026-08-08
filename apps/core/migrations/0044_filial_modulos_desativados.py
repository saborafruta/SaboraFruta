from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0043_alter_notificacao_tipo'),
    ]

    operations = [
        migrations.AddField(
            model_name='filial',
            name='modulos_desativados',
            field=models.JSONField(
                blank=True,
                default=list,
                help_text='Chaves das secoes do menu desativadas nesta filial '
                           '(cadastros/operacoes/financeiro/logistica/avancado/food_service).',
            ),
        ),
    ]
