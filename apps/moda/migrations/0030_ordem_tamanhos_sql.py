"""
Sequencia `Tamanho.ordem` por SQL direto -- e diz no log o que encontrou.

Duas tentativas falharam por diagnóstico errado, e o log de produção
derrubou as duas:

  0028  guarda "só mexe onde ordem = 0"      -> 0 renumerados (nada está zerado)
  0029  sem guarda, via ORM histórico        -> "nenhum tamanho cadastrado"

A 0029 é a contradição que motiva esta: a tela de Nova Grade mostra sete
tamanhos, e o template só desenha checkbox dentro do `{% for %}` -- com a
tabela vazia ele cairia no `{% empty %}` ("Nenhum tamanho cadastrado
ainda"). Então existem linhas, e quem não as viu foi o model histórico da
migration, não o banco.

Por isso aqui não há ORM: contagem, leitura e gravação saem em SQL na
tabela `moda_tamanhos`. SQL não depende de manager, de estado de migration
nem de qual manager o histórico rendeu -- é o caminho que não pode ser
derrotado pelo mistério.

O log recebe banco/host, as duas contagens (ORM e SQL) e o inventário
antes/depois de cada grupo. Se as contagens divergirem, a diferença entre
elas é a resposta; se as duas derem zero, o app fala com outro banco e o
problema muda de lugar.

Regra de ordenação: sequência de grade por tipo (PP, P, M, G, GG, XG,
XGG), numerada de 10 em 10. Sigla fora da sequência conhecida vai para o
fim do próprio tipo, preservando a ordem relativa atual.

`tipo` ordena ANTES de `ordem` no Meta.ordering, então tamanho cadastrado
noutro tipo continua em bloco separado -- isso é erro de cadastro, e o
inventário impresso mostra.

Idempotente: só faz UPDATE onde o valor muda. Reverso é no-op.
"""
from django.db import migrations

TABELA = 'moda_tamanhos'
PASSO = 10

SEQUENCIA = {
    'adulto': ['PP', 'P', 'M', 'G', 'GG', 'XG', 'XGG'],
    'plus_size': ['G1', 'G2', 'G3', 'G4', 'G5', 'G6', 'G7', 'G8'],
    'infantil': ['2', '4', '6', '8', '10', '12', '14', '16'],
    'unico': ['U', 'UNICO', 'ÚNICO'],
}


def _chave(linha):
    """linha = (id, filial_id, sigla, tipo, ordem)."""
    _, _, sigla, tipo, ordem = linha
    sigla = (sigla or '').strip().upper()
    conhecidas = SEQUENCIA.get(tipo, [])
    if sigla in conhecidas:
        return (0, conhecidas.index(sigla), 0, sigla)
    if sigla.isdigit():
        return (1, int(sigla), 0, sigla)
    return (2, 0, ordem or 0, sigla)


def sequenciar(apps, schema_editor):
    conn = schema_editor.connection
    cfg = conn.settings_dict
    print(f'  [tamanhos] banco={cfg.get("NAME")} host={cfg.get("HOST")} engine={cfg.get("ENGINE")}')

    # Contagem pelo ORM histórico, só para comparar com o SQL.
    try:
        via_orm = apps.get_model('moda', 'Tamanho').objects.count()
    except Exception as erro:  # noqa: BLE001 - o valor do erro é o diagnóstico
        via_orm = f'erro: {type(erro).__name__}: {erro}'
    print(f'  [tamanhos] contagem via ORM historico: {via_orm}')

    with conn.cursor() as cur:
        cur.execute(f'SELECT count(*) FROM {TABELA}')
        total = cur.fetchone()[0]
        print(f'  [tamanhos] contagem via SQL direto : {total}')

        if not total:
            print('  [tamanhos] tabela vazia no banco desta conexao -- nada a fazer')
            return

        cur.execute(
            f'SELECT id, filial_id, sigla, tipo, ordem FROM {TABELA} '
            f'ORDER BY filial_id, tipo, ordem, sigla'
        )
        linhas = list(cur.fetchall())

        grupos = {}
        for linha in linhas:
            grupos.setdefault((linha[1], linha[3]), []).append(linha)

        tocados = 0
        for chave_grupo in sorted(grupos, key=lambda g: (g[0] or 0, g[1] or '')):
            filial_id, tipo = chave_grupo
            grupo = grupos[chave_grupo]

            antes = ' | '.join(f'{l[2]}({l[4]})' for l in grupo)
            grupo.sort(key=_chave)

            depois = []
            for posicao, linha in enumerate(grupo, start=1):
                nova = posicao * PASSO
                depois.append(f'{linha[2]}({nova})')
                if linha[4] != nova:
                    cur.execute(
                        f'UPDATE {TABELA} SET ordem = %s WHERE id = %s',
                        [nova, linha[0]],
                    )
                    tocados += 1

            print(f'  [tamanhos] filial {filial_id} / {tipo}')
            print(f'      antes : {antes}')
            print(f'      depois: {" | ".join(depois)}')

    print(f'  [tamanhos] {tocados} tamanho(s) renumerado(s)')


class Migration(migrations.Migration):

    dependencies = [
        ('moda', '0029_ordem_tamanhos_sequencia'),
    ]

    operations = [
        migrations.RunPython(sequenciar, migrations.RunPython.noop),
    ]
