"""
Tira o Food Service de quem não é padaria.

A concessão pelo segmento já foi restringida a Padarias, mas isso sozinho
não limpou a tela de ninguém: a habilitação manual (`Empresa.modulos_extras`)
continua valendo por cima do segmento -- é ela a porta de quem não se
encaixa no vertical. E foi a migração `0047` que a marcou em massa, quando
o módulo deixou de ser universal, para não tirar mesa e comanda de quem
usava.

O resultado prático é que a fábrica de polpa seguia com o menu inteiro do
salão, agora por herança daquela marcação -- e não por escolha de ninguém.
Como a decisão é "Food Service é da padaria", esta migração retira a marca
de toda empresa cujo segmento não seja Padarias.

A porta continua aberta: a Central Administrativa segue podendo ligar o
módulo na mão para uma empresa específica. A diferença é que passa a ser
uma escolha explícita, com dono e data, em vez de herança de uma migração
antiga.

Empresa de segmento Padarias não é tocada -- ali a marca é redundante, mas
tirá-la não muda nada e mexer sem necessidade só cria risco.

O reverso é vazio: recolocar a marca em todo mundo devolveria o módulo até
a quem o admin tirou de propósito depois.
"""
from django.db import migrations

from apps.core.constants import segmentos as seg

CHAVE = 'food_service'


def retirar(apps, schema_editor):
    Empresa = apps.get_model('core', 'Empresa')

    alteradas = []
    for empresa in Empresa.objects.exclude(segmento=seg.PADARIAS):
        extras = list(empresa.modulos_extras or [])
        if CHAVE not in extras:
            continue
        empresa.modulos_extras = [c for c in extras if c != CHAVE]
        alteradas.append(empresa)

    if alteradas:
        Empresa.objects.bulk_update(alteradas, ['modulos_extras'])


def nao_desfazer(apps, schema_editor):
    """Reverso vazio — ver o docstring do módulo."""


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0053_usuario_menu_favoritos'),
    ]

    operations = [
        migrations.RunPython(retirar, nao_desfazer),
    ]
