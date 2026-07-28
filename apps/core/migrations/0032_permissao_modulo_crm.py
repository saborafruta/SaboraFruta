from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0031_alter_parametrossistema_certificado_base64_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='permissao',
            name='modulo',
            field=models.CharField(
                choices=[
                    ('vendas', 'Vendas'), ('estoque', 'Estoque'), ('financeiro', 'Financeiro'),
                    ('fiscal', 'Fiscal'), ('config', 'Configurações'), ('relatorios', 'Relatórios'),
                    ('pdv', 'PDV'), ('producao', 'Produção'), ('qualidade', 'Qualidade'),
                    ('compras', 'Compras'), ('cadastros', 'Cadastros'), ('produtos', 'Produtos'),
                    ('logistica', 'Logistica'), ('cashback', 'Cashback'), ('crm', 'CRM'),
                ],
                max_length=60,
            ),
        ),
    ]
