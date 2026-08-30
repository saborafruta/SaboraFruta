from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('moda', '0046_pedidoproducao_clientes_adicionais')]

    operations = [
        migrations.AddField(
            model_name='pedidoproducao',
            name='informacoes_criacao',
            field=models.TextField(
                'Informações para criação de artes', blank=True,
                help_text='Anotações internas da equipe de criação; não aparecem nos PDFs.',
            ),
        ),
    ]
