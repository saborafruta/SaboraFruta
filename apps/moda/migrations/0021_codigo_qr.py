"""
Código de QR nos quatro documentos, em três passos por tabela.

`AddField` com `unique=True` de uma vez quebraria: todo registro existente
receberia string vazia, e a segunda linha de cada tabela violaria a
restrição no meio do `migrate` — que roda como release command, então o
container não sobe e o site fica em 502.

A sequência é a mesma do `token_publico` (0020) e do `qr_token` da Mesa:
cria sem unique, preenche linha a linha, e só então aplica a restrição.
"""
import secrets

from django.db import migrations, models

# Prefixo por modelo. Repetido aqui de propósito: migration não pode
# importar o modelo real — se amanhã alguém trocar o prefixo em
# `models/qr.py`, esta migration continua reproduzindo o passado, que é o
# que ela precisa fazer.
PREFIXOS = [
    ('PedidoProducao', 'PED'),
    ('OrdemProducao', 'OP'),
    ('FichaTecnica', 'FT'),
    ('RegistroCorte', 'LT'),
]


def gerar_codigos(apps, schema_editor):
    for nome, prefixo in PREFIXOS:
        Modelo = apps.get_model('moda', nome)
        pendentes = Modelo.objects.filter(codigo_qr='').values_list('pk', flat=True)
        # Um por vez, com `update` direto: `bulk_update` carregaria os
        # objetos inteiros à toa, e esta migration roda uma vez só.
        for pk in list(pendentes):
            Modelo.objects.filter(pk=pk).update(
                codigo_qr=f'{prefixo}-{secrets.token_urlsafe(9)}',
            )


def limpar(apps, schema_editor):
    """Reverso: as colunas somem junto no passo seguinte."""


def _passos(model_name):
    return [
        migrations.AddField(
            model_name=model_name,
            name='codigo_qr',
            field=models.CharField(
                blank=True, default='', editable=False, max_length=24,
                verbose_name='Código do QR',
            ),
        ),
    ]


def _unique(model_name):
    return migrations.AlterField(
        model_name=model_name,
        name='codigo_qr',
        field=models.CharField(
            blank=True, db_index=True, editable=False, max_length=24,
            unique=True, verbose_name='Código do QR',
        ),
    )


class Migration(migrations.Migration):

    dependencies = [
        ('moda', '0020_token_publico'),
    ]

    operations = [
        # Os quatro AddField primeiro, o backfill uma vez só, depois os
        # quatro AlterField. Intercalar faria quatro varreduras onde uma
        # basta.
        *_passos('pedidoproducao'),
        *_passos('ordemproducao'),
        *_passos('fichatecnica'),
        *_passos('registrocorte'),
        migrations.RunPython(gerar_codigos, limpar),
        _unique('pedidoproducao'),
        _unique('ordemproducao'),
        _unique('fichatecnica'),
        _unique('registrocorte'),
    ]
