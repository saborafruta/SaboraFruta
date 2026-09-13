from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0067_fonte_mensagem_etiqueta_venda'),
    ]

    operations = [
        migrations.AddField(
            model_name='parametrossistema',
            name='checkout_venda_ativo',
            field=models.BooleanField(
                default=False,
                help_text='Exibe o Checkout no menu desta filial. Quando desativado, a tela fica indisponível.',
                verbose_name='Utilizar checkout de venda',
            ),
        ),
    ]
