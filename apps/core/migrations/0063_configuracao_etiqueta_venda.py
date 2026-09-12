from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0062_separacao_filial_segura'),
    ]

    operations = [
        migrations.CreateModel(
            name='ConfiguracaoEtiquetaVenda',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('ativa', models.BooleanField(default=True)),
                ('largura_mm', models.DecimalField(decimal_places=2, default=Decimal('60.00'), help_text='Largura física da etiqueta, em milímetros.', max_digits=6, validators=[MinValueValidator(Decimal('20')), MaxValueValidator(Decimal('300'))])),
                ('altura_mm', models.DecimalField(decimal_places=2, default=Decimal('40.00'), help_text='Altura física da etiqueta, em milímetros.', max_digits=6, validators=[MinValueValidator(Decimal('20')), MaxValueValidator(Decimal('300'))])),
                ('impressora_nome', models.CharField(blank=True, help_text='Nome da impressora de etiquetas que o operador deve selecionar.', max_length=150)),
                ('texto_rodape', models.CharField(blank=True, default='Obrigado pela sua preferência!', max_length=300)),
                ('exibir_logo', models.BooleanField(default=True)),
                ('exibir_nome_empresa', models.BooleanField(default=True)),
                ('exibir_nome_cliente', models.BooleanField(default=True)),
                ('exibir_numero_venda', models.BooleanField(default=True)),
                ('exibir_data_venda', models.BooleanField(default=True)),
                ('filial', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='configuracao_etiqueta_venda', to='core.filial')),
            ],
            options={
                'verbose_name': 'Configuração de etiqueta de venda',
                'verbose_name_plural': 'Configurações de etiqueta de venda',
                'db_table': 'configuracoes_etiqueta_venda',
            },
        ),
    ]
