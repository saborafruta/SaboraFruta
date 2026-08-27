"""
Dá as formas de pagamento padrão às filiais que já existem.

A semeadura passou a acontecer quando a filial é criada. Sem esta migration,
só filial nova sairia ganhando as formas -- as que já estão no banco
continuariam sem nenhuma, e o caixa delas não fecharia venda.

SÓ AGE NO VAZIO: filial que já tem forma própria foi cuidada por alguém, e
recriar as padrão aqui traria de volta a forma que essa pessoa desativou de
propósito.
"""
from django.db import migrations

# CONGELADO AQUI DE PROPOSITO, em vez de importar a lista do servico: migration
# e' retrato de um momento. Se a lista viva mudar amanha, esta migration nao
# pode passar a fazer outra coisa em quem rodar ela depois.
PADRAO = [
    ('Dinheiro', 'dinheiro', '01', False),
    ('PIX', 'pix', '17', False),
    ('Cartão de Crédito', 'cartao_credito', '03', True),
    ('Cartão de Débito', 'cartao_debito', '04', False),
    ('Transferência', 'ted', '99', False),
    ('Boleto', 'boleto', '15', False),
]


def semear(apps, schema_editor):
    Filial = apps.get_model('core', 'Filial')
    FormaPagamento = apps.get_model('financeiro', 'FormaPagamento')

    com_forma = set(
        FormaPagamento.objects.filter(filial__isnull=False)
        .values_list('filial_id', flat=True)
    )
    for filial in Filial.objects.exclude(pk__in=com_forma).iterator():
        for descricao, tipo, codigo_sefaz, gera_parcelas in PADRAO:
            FormaPagamento.objects.get_or_create(
                empresa_id=filial.empresa_id,
                filial_id=filial.pk,
                descricao=descricao,
                defaults={
                    'tipo': tipo,
                    'codigo_sefaz': codigo_sefaz,
                    'gera_parcelas': gera_parcelas,
                    'ativo': True,
                },
            )


def desfazer(apps, schema_editor):
    """
    Nao apaga nada.

    Depois de rodar, as formas sao dados de trabalho -- alguem pode ter
    ajustado taxa, prazo ou conta bancaria nelas. Voltar a migration nao pode
    levar esse ajuste junto.
    """


class Migration(migrations.Migration):

    dependencies = [
        ('financeiro', '0054_recorrencia_semanal_e_categorias_pessoal'),
        ('core', '0054_food_service_so_padaria'),
    ]

    operations = [
        migrations.RunPython(semear, desfazer),
    ]
