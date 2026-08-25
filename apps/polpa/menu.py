"""
Mapa do vertical Polpa de Frutas — fonte única do menu, das URLs e das telas.

O QUE ESTA INDÚSTRIA TEM DE DIFERENTE. Numa confecção o material espera na
prateleira; aqui a matéria-prima APODRECE. A manga chega no caminhão a 30°C
e tem horas, não dias, até virar perda — e o que sai da despolpadeira só
continua sendo produto enquanto a temperatura não subir. Por isso o mapa
começa na balança (recebimento) e a cadeia de frio é um grupo próprio: num
ERP genérico ela vira "estoque", e aí o congelado some dentro do saldo como
se fosse parafuso.

A ordem dos grupos é a ordem do processo:

    RECEBIMENTO → FORMULAÇÃO → PRODUÇÃO → CONGELAMENTO/FRIO →
    QUALIDADE → EXPEDIÇÃO → INDICADORES

O QUE NÃO NASCE AQUI. Ficha técnica, ordem de produção, lote e inspeção já
existem no ERP (`apps.producao`, `apps.estoque`, `apps.lotes`,
`apps.qualidade`). O vertical acrescenta o que é da fruta -- pesagem com
tara, classificação por Brix, rendimento de despolpa, câmara fria,
rastreabilidade do recall -- e AMARRA nos modelos que já existem, em vez de
criar um segundo estoque paralelo que ninguém consegue conciliar depois.

Cada item nasce como tela em construção e vai sendo trocado por uma view
real. O que muda quando a tela fica pronta é a rota em `urls.py`; o item
continua aqui, apontando para o mesmo endereço.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Item:
    slug: str
    label: str
    descricao: str = ''


@dataclass(frozen=True)
class Grupo:
    slug: str
    label: str
    descricao: str
    # Nome do ícone desenhado em `_icones.html`.
    icone: str
    itens: tuple[Item, ...] = field(default_factory=tuple)


GRUPOS: tuple[Grupo, ...] = (
    Grupo(
        'recebimento', 'Recebimento',
        'A fruta entrando: balança, classificação e o que vira lote.',
        'balanca',
        (
            Item('romaneios', 'Recebimento de fruta',
                 'Pesagem com tara, nota do produtor e o lote que nasce dali.'),
            Item('classificacao', 'Classificação',
                 'Brix, pH, maturação e impureza — o que decide aceitar ou recusar.'),
            Item('produtores', 'Produtores',
                 'Quem fornece a fruta, com histórico de qualidade e rendimento.'),
            Item('recusas', 'Recusas e devoluções',
                 'Carga que voltou, com o motivo registrado.'),
        ),
    ),
    Grupo(
        'formulacao', 'Formulação',
        'A receita: o que entra em cada produto e quanto ele deve render.',
        'frasco',
        (
            Item('produtos', 'Produtos',
                 'Polpa, açaí, sorvete, picolé, mix e congelados do catálogo.'),
            Item('materias-primas', 'Matérias-primas',
                 'Fruta, açúcar, leite, gordura, estabilizante, aroma, corante.'),
            Item('receitas', 'Formulações',
                 'Percentual de fruta, água, açúcar e aditivos por produto.'),
            Item('rendimento', 'Rendimento padrão',
                 'Quanto de polpa sai de 100 kg de cada fruta — a régua da perda.'),
            Item('embalagens', 'Embalagens e rótulos',
                 'Saco, pote, balde, caixa: o que embala e o que a etiqueta diz.'),
        ),
    ),
    Grupo(
        'pcp', 'Planejamento',
        'O que produzir, quando e em qual linha — antes de a fábrica ligar.',
        'calendario',
        (
            Item('planejamento', 'Necessidade de produção',
                 'Estoque, pedidos, mínimo e previsão viram o que falta produzir.'),
            Item('calendario', 'Calendário',
                 'As ordens dia a dia, com a carga de cada recurso.'),
            Item('quadro', 'Quadro da produção',
                 'Onde está cada ordem, da planejada à produzida.'),
            Item('recursos', 'Linhas e máquinas',
                 'Capacidade por dia — sem ela, "cabe?" não tem resposta.'),
        ),
    ),
    Grupo(
        'producao', 'Produção',
        'Da seleção ao envase, batida por batida.',
        'engrenagem',
        (
            Item('ordens', 'Ordens de produção',
                 'O que produzir, de qual lote de fruta e para qual pedido.'),
            Item('batidas', 'Batidas',
                 'Cada batelada com sua formulação, rendimento e lote de saída.'),
            Item('etapas', 'Etapas do processo',
                 'Seleção, lavagem, sanitização, despolpa, pasteurização e envase.'),
            Item('apontamento', 'Apontamento',
                 'Quanto entrou, quanto saiu e quem estava na linha.'),
            Item('perdas', 'Perdas e refugo',
                 'Casca, caroço, sobra de linha e o que foi descartado.'),
        ),
    ),
    Grupo(
        'frio', 'Cadeia de frio',
        'Túnel, câmaras e temperatura — onde o produto continua sendo produto.',
        'floco',
        (
            Item('camaras', 'Câmaras frias',
                 'Cadastro das câmaras, com faixa de temperatura e capacidade.'),
            Item('temperatura', 'Registro de temperatura',
                 'Leituras por câmara, com o desvio marcado.'),
            Item('alertas', 'Alertas da cadeia de frio',
                 'Temperatura fora, vencimento, capacidade e lote bloqueado.'),
            Item('tunel', 'Túnel de congelamento',
                 'Entrada, tempo e saída de cada carga no túnel.'),
            Item('estoque-frio', 'Estoque congelado',
                 'Saldo por lote e validade, dentro de cada câmara.'),
        ),
    ),
    Grupo(
        'qualidade', 'Qualidade',
        'O que prova que o lote pode sair: análise, laudo e rastro.',
        'escudo',
        (
            Item('analises', 'Análises',
                 'Brix, pH, acidez e microbiológico, por lote.'),
            Item('laudos', 'Laudos',
                 'O boletim do lote, pronto para acompanhar a carga.'),
            Item('nao-conformidades', 'Não conformidades',
                 'Desvio registrado, com a ação tomada e quem tomou.'),
            Item('rastreabilidade', 'Rastreabilidade',
                 'Do produtor ao cliente e de volta — o caminho do recall.'),
        ),
    ),
    Grupo(
        'expedicao', 'Expedição',
        'A saída: separação pela validade, carregamento e entrega.',
        'caminhao',
        (
            Item('separacao', 'Separação',
                 'O que sai primeiro é o que vence primeiro (FEFO).'),
            Item('carregamento', 'Carregamento',
                 'Temperatura do baú na saída e conferência da carga.'),
            Item('entregas', 'Entregas',
                 'Quem recebeu, quando e em que temperatura.'),
        ),
    ),
    Grupo(
        'indicadores', 'Indicadores',
        'Rendimento, perda, custo e validade — os números que a fábrica cobra.',
        'grafico',
        (
            Item('painel', 'Painel',
                 'O dia da fábrica numa tela.'),
            Item('rendimento-real', 'Rendimento real',
                 'O que a fruta rendeu contra o que deveria render.'),
            Item('custos', 'Custo por lote',
                 'Matéria-prima, embalagem e processo dentro do custo do lote.'),
            Item('validade', 'Validade',
                 'O que está para vencer, por lote e por câmara.'),
        ),
    ),
)

GRUPOS_POR_SLUG = {g.slug: g for g in GRUPOS}


def buscar_item(grupo_slug: str, item_slug: str):
    grupo = GRUPOS_POR_SLUG.get(grupo_slug)
    if grupo is None:
        return None, None
    item = next((i for i in grupo.itens if i.slug == item_slug), None)
    return grupo, item


def total_itens() -> int:
    return sum(len(g.itens) for g in GRUPOS)


# ── O processo, em etapas ────────────────────────────────────────────────
# Usado no hub para desenhar a régua do fluxo. É o MESMO caminho que o
# produto percorre de verdade -- e escrevê-lo aqui obriga qualquer tela
# nova a se encaixar nele, em vez de inventar uma etapa paralela.
ETAPAS_FLUXO: tuple[str, ...] = (
    'Recebimento', 'Classificação', 'Seleção e lavagem', 'Despolpa',
    'Formulação', 'Pasteurização', 'Envase', 'Congelamento',
    'Estoque frio', 'Expedição',
)
