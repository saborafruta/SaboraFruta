from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('financeiro', '0046_contapagar_antecipar_vencimento_dia_util_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='contapagar',
            name='dia_vencimento_mensal',
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='contapagar',
            name='regra_vencimento_mensal',
            field=models.CharField(
                choices=[
                    ('data_informada', 'Usar a data informada'),
                    ('primeiro_dia', 'Primeiro dia do mês'),
                    ('ultimo_dia', 'Último dia do mês'),
                    ('dia_fixo', 'Dia X do mês'),
                    ('quinto_dia_util', '5º dia útil'),
                ],
                default='data_informada',
                max_length=20,
            ),
        ),
    ]
