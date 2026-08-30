from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('financeiro', '0057_boleto_e_cartao_geram_parcelas'),
    ]

    operations = [
        migrations.AddField(
            model_name='formapagamento',
            name='exibir_no_pdv',
            field=models.BooleanField(
                default=True,
                verbose_name='Exibir no PDV',
                help_text='Desmarque para usar esta forma somente no Financeiro, sem exibi-la no PDV.',
            ),
        ),
    ]
