from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0041_empresa_coordenadas'),
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
                    ('mapas', 'Mapas e Geolocalização'), ('food_service', 'Food Service'),
                ],
                max_length=60,
            ),
        ),
    ]
