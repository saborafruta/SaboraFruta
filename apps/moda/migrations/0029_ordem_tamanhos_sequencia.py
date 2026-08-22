"""
Põe `Tamanho.ordem` na sequência de grade -- e mostra no log o que achou.

A 0028 tentou isso e renumerou ZERO tamanhos, porque só entrava em grupo
que tivesse algum `ordem = 0`. O log de produção provou que a premissa
estava errada: nenhum tamanho está zerado. Se todos têm ordem e mesmo
assim a tela sai alfabética (G, GG, M, XG, XGG), então os valores EMPATAM
-- e com `Meta.ordering = ['tipo','ordem','sigla']` o empate cai no
desempate por `sigla`, que é exatamente a ordem alfabética que se quer
evitar.

"Todo mundo com ordem preenchida" não é sinônimo de "alguém configurou":
um grupo todo no mesmo número é degenerado, não deliberado. Por isso aqui
não há mais essa guarda -- a renumeração é sempre para a sequência
canônica, que é o que o docstring do model pede (PP, P, M, G, GG, XGG,
"nunca em ordem alfabética") e o que foi pedido na tela.

Sigla fora da sequência conhecida vai para o fim do próprio tipo, mas
mantendo a ordem relativa que já tinha (desempate pelo `ordem` atual):
não dá para adivinhar a posição de uma sigla que o sistema não reconhece,
então o mínimo é não embaralhar o que alguém já arrumou.

ATENÇÃO ao que esta migration NÃO faz: `tipo` ordena ANTES de `ordem`, e
tamanho em tipo diferente continua em bloco separado por mais que se mexa
na ordem. Se PP e P estiverem, por exemplo, como 'outro' e o resto como
'adulto', eles seguem aparecendo depois -- e isso é um erro de cadastro,
não de ordenação. O inventário impresso abaixo serve para enxergar isso.

Idempotente: só grava onde o valor muda. Reverso é no-op -- os valores
antigos não são guardados, e `ordem` só decide exibição (grade já salva
tem a própria `ItemGrade.ordem`, que não é tocada aqui).
"""
from django.db import migrations

PASSO = 10

SEQUENCIA = {
    'adulto': ['PP', 'P', 'M', 'G', 'GG', 'XG', 'XGG'],
    'plus_size': ['G1', 'G2', 'G3', 'G4', 'G5', 'G6', 'G7', 'G8'],
    'infantil': ['2', '4', '6', '8', '10', '12', '14', '16'],
    'unico': ['U', 'UNICO', 'ÚNICO'],
}


def _chave(tamanho):
    """Posição da sigla dentro do próprio tipo."""
    sigla = (tamanho.sigla or '').strip().upper()
    conhecidas = SEQUENCIA.get(tamanho.tipo, [])
    if sigla in conhecidas:
        return (0, conhecidas.index(sigla), 0, sigla)
    if sigla.isdigit():
        # Numérico fora da lista (infantil 18, 20...): pelo valor.
        return (1, int(sigla), 0, sigla)
    # Desconhecida: no fim, preservando a ordem relativa atual.
    return (2, 0, tamanho.ordem or 0, sigla)


def _mostrar(tamanhos):
    return ' | '.join(f'{t.sigla}({t.ordem})' for t in tamanhos)


def sequenciar(apps, schema_editor):
    Tamanho = apps.get_model('moda', 'Tamanho')

    # Model histórico não herda manager customizado: este `objects` é um
    # Manager comum, sem o filtro de filial -- que é o que se quer aqui.
    grupos = {}
    for t in Tamanho.objects.all():
        grupos.setdefault((t.filial_id, t.tipo), []).append(t)

    if not grupos:
        print('  [tamanhos] nenhum tamanho cadastrado')
        return

    tocados = 0
    for chave_grupo in sorted(grupos, key=lambda g: (g[0], g[1])):
        filial_id, tipo = chave_grupo
        tamanhos = grupos[chave_grupo]

        # Como estava: na ordem em que o Meta.ordering realmente exibe.
        atuais = sorted(tamanhos, key=lambda t: (t.ordem, t.sigla))
        print(f'  [tamanhos] filial {filial_id} / {tipo}')
        print(f'      antes : {_mostrar(atuais)}')

        tamanhos.sort(key=_chave)
        for posicao, t in enumerate(tamanhos, start=1):
            nova = posicao * PASSO
            if t.ordem != nova:
                t.ordem = nova
                t.save(update_fields=['ordem'])
                tocados += 1

        print(f'      depois: {_mostrar(tamanhos)}')

    print(f'  [tamanhos] {tocados} tamanho(s) renumerado(s)')


class Migration(migrations.Migration):

    dependencies = [
        ('moda', '0028_ordem_tamanhos'),
    ]

    operations = [
        migrations.RunPython(sequenciar, migrations.RunPython.noop),
    ]
