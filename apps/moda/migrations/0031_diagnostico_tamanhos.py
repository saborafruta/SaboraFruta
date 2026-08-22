"""
Diagnóstico: descobrir ONDE estão os tamanhos -- e sequenciá-los se aparecerem.

A 0030 leu `moda_tamanhos` em SQL direto, no banco `railway` em
`postgres.railway.internal`, e contou ZERO. Só que a tela de pedido mostra
colunas G, GG, M, XG, XGG, e cada coluna daquelas é um `Tamanho` alcançado
por FK a partir de `moda_grade_pedido` -- com a tabela vazia, aquela página
levantaria erro de integridade em vez de renderizar. As duas coisas não
podem ser verdade sobre o mesmo banco.

Esta migration não decide nada por hipótese: mede.

  - Onde estou: current_database(), current_schema(), search_path, e a
    lista de schemas que têm uma tabela chamada moda_tamanhos.
  - Contagens de várias tabelas do moda, não só a de tamanhos. `moda_pedidos`
    é o discriminador: o usuário abriu os pedidos 7 e 10, então essa tabela
    TEM linhas no banco do app. Se ela vier 0 aqui, esta conexão não é a do
    app e o problema é de infraestrutura, não de ordenação. Se ela vier com
    linhas e `moda_tamanhos` vier 0, então os tamanhos sumiram de verdade e
    o assunto é outro.
  - Se em algum schema houver tamanhos, mostra e sequencia lá.

Só lê e, no máximo, faz UPDATE de `ordem`. Nada é criado nem apagado.
Reverso é no-op.
"""
from django.db import migrations

PASSO = 10

SEQUENCIA = {
    'adulto': ['PP', 'P', 'M', 'G', 'GG', 'XG', 'XGG'],
    'plus_size': ['G1', 'G2', 'G3', 'G4', 'G5', 'G6', 'G7', 'G8'],
    'infantil': ['2', '4', '6', '8', '10', '12', '14', '16'],
    'unico': ['U', 'UNICO', 'ÚNICO'],
}

TABELAS = [
    'moda_tamanhos', 'moda_grades', 'moda_itens_grade',
    'moda_pedidos', 'moda_itens_pedido', 'moda_grade_pedido',
]


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


def _um(cur, sql, params=None):
    try:
        cur.execute(sql, params or [])
        linha = cur.fetchone()
        return linha[0] if linha else None
    except Exception as erro:  # noqa: BLE001 - o erro em si é o diagnóstico
        return f'erro: {type(erro).__name__}: {erro}'


def diagnosticar(apps, schema_editor):
    conn = schema_editor.connection
    cfg = conn.settings_dict
    print('  [diag] ── onde estou ─────────────────────────────')
    print(f'  [diag] settings NAME={cfg.get("NAME")} HOST={cfg.get("HOST")} PORT={cfg.get("PORT")} USER={cfg.get("USER")}')

    with conn.cursor() as cur:
        print(f'  [diag] current_database={_um(cur, "SELECT current_database()")}')
        print(f'  [diag] current_schema  ={_um(cur, "SELECT current_schema()")}')
        print(f'  [diag] search_path     ={_um(cur, "SHOW search_path")}')

        cur.execute(
            "SELECT table_schema FROM information_schema.tables "
            "WHERE table_name = %s ORDER BY table_schema", ['moda_tamanhos'],
        )
        schemas = [r[0] for r in cur.fetchall()]
        print(f'  [diag] schemas com moda_tamanhos: {schemas or "(nenhum)"}')

        print('  [diag] ── contagens ──────────────────────────────')
        for tabela in TABELAS:
            print(f'  [diag]   {tabela:<22} = {_um(cur, f"SELECT count(*) FROM {tabela}")}')

        # Procura tamanhos em QUALQUER schema que tenha a tabela.
        alvo, linhas = None, []
        for schema in schemas:
            try:
                cur.execute(
                    f'SELECT id, filial_id, sigla, tipo, ordem FROM "{schema}".moda_tamanhos'
                )
                achadas = list(cur.fetchall())
            except Exception as erro:  # noqa: BLE001
                print(f'  [diag]   schema {schema}: erro ao ler ({type(erro).__name__})')
                continue
            print(f'  [diag]   schema {schema}: {len(achadas)} tamanho(s)')
            if achadas and not linhas:
                alvo, linhas = schema, achadas

        if not linhas:
            print('  [diag] nenhum tamanho em nenhum schema desta conexao -- nada a sequenciar')
            return

        print(f'  [diag] ── sequenciando no schema {alvo} ──────────')
        grupos = {}
        for linha in linhas:
            grupos.setdefault((linha[1], linha[3]), []).append(linha)

        tocados = 0
        for chave in sorted(grupos, key=lambda g: (g[0] or 0, g[1] or '')):
            grupo = grupos[chave]
            antes = ' | '.join(f'{l[2]}({l[4]})' for l in sorted(grupo, key=lambda l: (l[4], l[2])))
            grupo.sort(key=_chave)
            depois = []
            for posicao, linha in enumerate(grupo, start=1):
                nova = posicao * PASSO
                depois.append(f'{linha[2]}({nova})')
                if linha[4] != nova:
                    cur.execute(
                        f'UPDATE "{alvo}".moda_tamanhos SET ordem = %s WHERE id = %s',
                        [nova, linha[0]],
                    )
                    tocados += 1
            print(f'  [diag]   filial {chave[0]} / {chave[1]}')
            print(f'  [diag]     antes : {antes}')
            print(f'  [diag]     depois: {" | ".join(depois)}')

        print(f'  [diag] {tocados} tamanho(s) renumerado(s)')


class Migration(migrations.Migration):

    dependencies = [
        ('moda', '0030_ordem_tamanhos_sql'),
    ]

    operations = [
        migrations.RunPython(diagnosticar, migrations.RunPython.noop),
    ]
