"""
Preenche `Tamanho.ordem` onde ela nunca foi definida.

`Tamanho.Meta.ordering` é `['tipo', 'ordem', 'sigla']`, e o docstring do
model diz o motivo de `ordem` existir: a grade é lida PP, P, M, G, GG, XGG,
"nunca em ordem alfabética". Só que `ordem` é `default=0` e o
`seed_grades_moda` só a grava quando CRIA o tamanho — tamanho cadastrado à
mão pela tela de Tamanhos fica com 0. Com o grupo inteiro em 0, o
`ordering` cai no desempate por `sigla` e a grade sai alfabética.

Era o que estava em produção: PP e P vinham do seed (ordem 10 e 20) e o
resto tinha ordem 0, então a tela mostrava `G, GG, M, XG, XGG, PP, P` --
os de ordem 0 primeiro, em ordem alfabética, e os do seed depois. Isso
aparecia na tela de Nova Grade e também nas colunas da grade do pedido,
que ordena por `(tamanho.ordem, tamanho.sigla)`.

ESCOPO DELIBERADAMENTE ESTREITO: só renumera um grupo (filial + tipo) que
tenha ALGUM tamanho com ordem 0 -- ou seja, um grupo que ninguém terminou
de configurar. Grupo em que toda ordem já é diferente de zero foi ajustado
por alguém e não é tocado. Dentro de um grupo que entra, a renumeração é
completa, de 10 em 10, para não sobrar empate com os valores antigos do
seed.

Sigla que não está na sequência conhecida vai para o fim do próprio tipo,
em ordem alfabética: inventar posição para uma sigla que o sistema não
reconhece seria pior do que deixá-la num lugar previsível.

Reverso é no-op: os valores originais não são guardados. Não há perda real
-- `ordem` só decide exibição, e as grades já salvas guardam a própria
`ItemGrade.ordem`, que esta migration não toca.
"""
from django.db import migrations

# Espaçado de 10 em 10, como o seed, para caber um tamanho no meio depois
# sem renumerar os vizinhos.
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
        return (0, conhecidas.index(sigla), sigla)
    if sigla.isdigit():
        # Numérico fora da lista (infantil 18, 20...): ordena pelo valor,
        # depois das siglas nomeadas do mesmo tipo.
        return (1, int(sigla), sigla)
    return (2, 0, sigla)


def preencher(apps, schema_editor):
    Tamanho = apps.get_model('moda', 'Tamanho')

    # Model histórico não herda manager customizado: este `objects` é um
    # Manager comum, sem o filtro de filial do FilialManager -- que é o que
    # se quer aqui, já que a renumeração é de todas as filiais.
    grupos = {}
    for t in Tamanho.objects.all():
        grupos.setdefault((t.filial_id, t.tipo), []).append(t)

    tocados = 0
    for (filial_id, tipo), tamanhos in sorted(grupos.items(), key=lambda x: str(x[0])):
        # Grupo já configurado por alguém: não mexe.
        if all(t.ordem for t in tamanhos):
            continue

        tamanhos.sort(key=_chave)
        for posicao, t in enumerate(tamanhos, start=1):
            nova = posicao * PASSO
            if t.ordem != nova:
                t.ordem = nova
                t.save(update_fields=['ordem'])
                tocados += 1

        print(
            f'  [tamanhos] filial {filial_id} / {tipo}: '
            f'{" | ".join(t.sigla for t in tamanhos)}'
        )

    print(f'  [tamanhos] {tocados} tamanho(s) renumerado(s)')


class Migration(migrations.Migration):

    dependencies = [
        ('moda', '0027_arquivo_por_token'),
    ]

    operations = [
        migrations.RunPython(preencher, migrations.RunPython.noop),
    ]
