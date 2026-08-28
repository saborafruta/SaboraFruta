"""
As notas de bonificação saem do arquivo de vendas.

Enquanto a bonificação era emitida pela rotina de venda fora, o documento
nascia com `origem_tipo='viagem_venda_fora'` — ou seja, REGISTRADA COMO
VENDA nos próprios registros. Quem consultasse documentos por origem veria a
cortesia dentro das vendas, e um relatório de vendas do mês contaria
mercadoria que ninguém pagou.

Esta migração reetiqueta as notas cuja entrega é bonificação. Ela olha o
TIPO DA ENTREGA, e não o valor ou o CFOP: é o tipo que diz o que a operação
é, e os outros dois podem coincidir com os de uma venda.
"""
from django.db import migrations


def separar_bonificacoes(apps, schema_editor):
    DocumentoFiscal = apps.get_model('financeiro', 'DocumentoFiscal')
    VendaViagem = apps.get_model('logistica', 'VendaViagem')

    bonificacoes = set(
        VendaViagem.objects.filter(tipo='bonificacao').values_list('pk', flat=True)
    )
    if not bonificacoes:
        return

    DocumentoFiscal.objects.filter(
        origem_tipo='viagem_venda_fora', origem_id__in=bonificacoes,
    ).update(origem_tipo='viagem_bonificacao')


def voltar_para_venda(apps, schema_editor):
    """
    O reverso devolve as notas ao arquivo antigo — necessário para que uma
    volta atrás não deixe documentos apontando para uma origem que o código
    anterior não conhece.
    """
    DocumentoFiscal = apps.get_model('financeiro', 'DocumentoFiscal')
    DocumentoFiscal.objects.filter(origem_tipo='viagem_bonificacao').update(
        origem_tipo='viagem_venda_fora',
    )


class Migration(migrations.Migration):

    dependencies = [
        ('logistica', '0022_motivo_da_bonificacao'),
        ('financeiro', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(separar_bonificacoes, voltar_para_venda),
    ]
