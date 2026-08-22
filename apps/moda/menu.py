"""
Mapa do vertical Moda e Confecção — fonte única do menu, das URLs e das telas.

Está aqui, e não espalhado em `urls.py` + sidebar + templates, porque são
62 telas: manter três listas em paralelo garantiria que uma hora elas
divergiriam (link no menu para rota que não existe, ou rota órfã).

Cada item nasce como tela em construção e vai sendo trocado por uma view
real conforme o módulo é implementado — ver `views.py`. O que muda quando
uma tela fica pronta é só o campo `view`, aqui.

A ordem dos grupos e dos itens é a ordem do fluxo da confecção:

    CLIENTE → PEDIDO → ARTE → ENGENHARIA → PLANEJAMENTO → MATERIAIS →
    CORTE → SUBLIMAÇÃO/BORDADO/SILK → COSTURA → ACABAMENTO →
    QUALIDADE → EMBALAGEM → EXPEDIÇÃO → ENTREGA
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
    # Nome do ícone Heroicon desenhado em `_icones.html`.
    icone: str
    itens: tuple[Item, ...] = field(default_factory=tuple)


GRUPOS: tuple[Grupo, ...] = (
    Grupo(
        'comercial', 'Comercial',
        'Do primeiro contato até o pedido aprovado e enviado à produção.',
        'carrinho',
        (
            Item('clientes', 'Clientes', 'Carteira de clientes da confecção.'),
            Item('orcamentos', 'Orçamentos', 'Propostas antes de virarem pedido.'),
            Item('pedidos', 'Pedidos', 'A ficha de produção digital.'),
            Item('aprovacao-pedido', 'Aprovação de pedido', 'Liberação comercial e financeira.'),
            Item('aprovacao-arte', 'Aprovação de arte', 'Aceite do layout pelo cliente.'),
            Item('envio-pedido', 'Envio de pedido', 'Passagem do pedido para a produção.'),
            Item('historico', 'Histórico', 'Tudo que já passou pelo comercial.'),
        ),
    ),
    Grupo(
        'produtos', 'Produtos',
        'O catálogo: do modelo à variante que vai para a etiqueta.',
        'camiseta',
        (
            Item('produtos', 'Produtos', 'Itens vendáveis do catálogo.'),
            Item('modelos', 'Modelos', 'Modelagem base reaproveitada entre produtos.'),
            Item('colecoes', 'Coleções', 'Agrupamento por temporada ou campanha.'),
            Item('categorias', 'Categorias', 'Classificação do catálogo.'),
            # Marcas e Linhas não estavam na lista original do menu, mas o
            # cadastro do produto tem os dois campos — sem tela, ficariam
            # como selects permanentemente vazios.
            Item('marcas', 'Marcas', 'Marcas próprias e de terceiros.'),
            Item('linhas', 'Linhas', 'Esportiva, Casual, Uniforme profissional.'),
            Item('cores', 'Cores', 'Cartela de cores da confecção.'),
            Item('tamanhos', 'Tamanhos', 'P, M, G, numéricos e especiais.'),
            Item('grades', 'Grades', 'Combinações de tamanho usadas nos pedidos.'),
            Item('variantes', 'Variantes', 'Cruzamento de produto, cor e tamanho — é o nível que tem SKU.'),
            Item('skus', 'SKUs', 'Código único de cada variante.'),
        ),
    ),
    Grupo(
        'engenharia', 'Engenharia',
        'Como a peça é feita: materiais, operações e o custo que sai disso.',
        'regua',
        (
            Item('ficha-tecnica', 'Ficha Técnica', 'A especificação completa da peça.'),
            Item('estrutura-produto', 'Estrutura do Produto', 'Composição em níveis.'),
            Item('materiais', 'Materiais', 'Tecidos e insumos que entram na peça.'),
            Item('aviamentos', 'Aviamentos', 'Linhas, botões, zíperes, etiquetas.'),
            Item('operacoes', 'Operações', 'Cada passo executável no chão de fábrica.'),
            Item('sequencia-producao', 'Sequência de Produção', 'A ordem em que as operações acontecem.'),
            Item('custos', 'Custos', 'Custo calculado a partir da ficha.'),
        ),
    ),
    Grupo(
        'pcp', 'PCP',
        'Planejamento e Controle da Produção: o que fazer, quando e em que ordem.',
        'calendario',
        (
            Item('planejamento', 'Planejamento', 'Carga de trabalho no horizonte.'),
            Item('capacidade', 'Capacidade', 'Quanto cada setor aguenta por período.'),
            Item('necessidade-materiais', 'Necessidade de Materiais',
                 'O que as ordens vão consumir contra o que há em estoque.'),
            Item('programacao', 'Programação', 'Distribuição das ordens no tempo.'),
            Item('ordens-producao', 'Ordens de Produção', 'A OP que desce para a fábrica.'),
            Item('priorizacao', 'Priorização', 'O que passa na frente e por quê.'),
            Item('acompanhamento', 'Acompanhamento', 'Onde cada ordem está agora.'),
        ),
    ),
    Grupo(
        'producao', 'Produção',
        'O chão de fábrica, na ordem em que a peça caminha.',
        'tesoura',
        (
            Item('kanban', 'Kanban', 'O quadro da fábrica, com arrastar e soltar.'),
            Item('corte', 'Corte', 'Corte do tecido conforme o encaixe.'),
            Item('encaixe', 'Encaixe', 'Aproveitamento do tecido no risco.'),
            Item('sublimacao', 'Sublimação', 'Estampa por transferência térmica.'),
            Item('bordado', 'Bordado', 'Aplicação bordada.'),
            Item('silk', 'Silk', 'Serigrafia.'),
            Item('preparacao', 'Preparação', 'Preparo das partes antes da costura.'),
            Item('costura', 'Costura', 'Montagem da peça.'),
            Item('acabamento', 'Acabamento', 'Arremate, limpeza e passadoria.'),
            Item('qualidade', 'Qualidade', 'Inspeção antes de liberar.'),
            Item('embalagem', 'Embalagem', 'Peça embalada e identificada.'),
        ),
    ),
    Grupo(
        'estoque', 'Estoque',
        'Onde está cada material e cada peça, em cada estágio.',
        'caixa',
        (
            Item('tecidos', 'Tecidos', 'Malhas e tecidos em rolo.'),
            Item('aviamentos', 'Aviamentos', 'Insumos de montagem.'),
            Item('produtos', 'Produtos', 'Itens de catálogo em estoque.'),
            Item('semiacabados', 'Semiacabados', 'Peças em processo (WIP).'),
            Item('acabados', 'Acabados', 'Prontas para expedir.'),
            Item('lotes', 'Lotes', 'Rastreabilidade por lote.'),
        ),
    ),
    Grupo(
        'expedicao', 'Expedição',
        'Da separação até a entrega confirmada.',
        'caminhao',
        (
            Item('separacao', 'Separação', 'Picking do pedido.'),
            Item('conferencia', 'Conferência', 'Checagem antes de fechar o volume.'),
            Item('embalagem', 'Embalagem', 'Volumes e etiquetas de envio.'),
            Item('entrega', 'Entrega', 'Saída e comprovação de entrega.'),
        ),
    ),
    Grupo(
        'indicadores', 'Indicadores',
        'Como a operação está indo — produção, custo e prazo.',
        'grafico',
        (
            Item('dashboard', 'Dashboard', 'Visão geral do vertical.'),
            Item('alertas', 'Alertas', 'O que precisa de alguém agora.'),
            Item('producao', 'Produção', 'Volume produzido por etapa.'),
            Item('wip', 'WIP', 'Trabalho em processo parado entre etapas.'),
            Item('eficiencia', 'Eficiência', 'Produzido contra capacidade.'),
            Item('perdas', 'Perdas', 'Refugo, retrabalho e sobra de tecido.'),
            Item('custos', 'Custos', 'Custo real contra o da ficha técnica.'),
            Item('margens', 'Margens', 'Margem por produto e por pedido.'),
            Item('prazos', 'Prazos', 'Entregas no prazo e atrasos.'),
        ),
    ),
)

GRUPOS_POR_SLUG = {g.slug: g for g in GRUPOS}

# As etapas do fluxo, para a tela inicial mostrar por que os grupos estão
# nessa ordem. É a régua do processo, não a lista de telas.
ETAPAS_FLUXO = (
    'Cliente', 'Pedido', 'Arte', 'Engenharia', 'Planejamento', 'Materiais',
    'Corte', 'Estampa', 'Costura', 'Acabamento', 'Qualidade', 'Embalagem',
    'Expedição', 'Entrega',
)


def buscar_item(grupo_slug: str, item_slug: str):
    """Devolve (Grupo, Item) ou (None, None) se a combinação não existir."""
    grupo = GRUPOS_POR_SLUG.get(grupo_slug)
    if grupo is None:
        return None, None
    for item in grupo.itens:
        if item.slug == item_slug:
            return grupo, item
    return grupo, None


def total_itens() -> int:
    return sum(len(g.itens) for g in GRUPOS)
