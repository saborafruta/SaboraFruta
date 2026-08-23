"""
Lotes — de que rolo saiu cada peça.

ESTA TELA EXISTE PARA UM DIA RUIM. O cliente liga dizendo que a camisa
encolheu na primeira lavagem, ou que a cor saiu diferente entre duas peças
do mesmo pedido. A pergunta que vem em seguida é sempre a mesma: **que
outros pedidos levaram peça daquele mesmo rolo?** Sem resposta, ou se troca
tudo o que se vendeu no mês, ou se espera o telefone tocar de novo.

O CAMINHO É lote → corte → ordem → pedido → cliente, e ele já existe inteiro
no banco: `RegistroCorte.lote` guarda o rolo, o corte aponta a ordem, a
ordem aponta o pedido e o pedido o cliente. O que faltava era ler a corrente
de trás para a frente.

O LOTE É TEXTO DIGITADO, e é essa a fragilidade da corrente. "L-123",
"l-123 " e "L 123" são o mesmo rolo para quem está na mesa de corte e três
lotes diferentes para o banco. A tela junta o que dá — espaço e caixa — e
NÃO tenta adivinhar o resto: juntar por semelhança esconderia justamente o
cadastro que precisa ser corrigido.

CORTE SEM LOTE É PEÇA SEM RASTRO. Ela não aparece em lote nenhum, e no dia
do defeito não há como saber se veio do rolo suspeito. Por isso a contagem
tem cartão próprio: é o furo da rastreabilidade, e ele não se conserta
depois do fato.

A ORDEM DA LISTA É POR PROBLEMA, não alfabética. Um rolo ruim se anuncia
antes da reclamação: ele corta pior (aproveitamento baixo) e refuga mais na
costura. Quem abre esta tela sem um lote em mãos quer ver o suspeito no
topo.
"""
from decimal import Decimal

from ..models import EtapaOrdem, RegistroCorte

ZERO = Decimal('0')
CEM = Decimal('100')
METRO = Decimal('0.01')

# As mesmas etapas administrativas que os outros indicadores ignoram: elas
# não são bancada, e uma perda apontada ali seria erro de digitação.
NAO_PRODUZEM = (
    EtapaOrdem.Etapa.PEDIDO,
    EtapaOrdem.Etapa.PLANEJAMENTO,
    EtapaOrdem.Etapa.MATERIAIS,
)

# Acima disto o rolo entra na lista de suspeitos. Não é um limite de
# qualidade da casa — é o ponto em que vale olhar o rolo antes de olhar a
# costureira.
REFUGO_SUSPEITO = Decimal('5')

# Quantos clientes citar na linha. A lista inteira num rolo grande viraria
# um parágrafo, e o que se quer ali é reconhecer o pedido de relance.
CLIENTES_NA_LINHA = 4


def chave_do_lote(texto: str) -> str:
    """
    O que faz dois cortes serem do MESMO rolo.

    Espaço e caixa são ruído de digitação e somem. O resto fica: "L-123" e
    "L123" continuam separados de propósito, porque juntá-los por semelhança
    esconderia o cadastro torto em vez de mostrá-lo.
    """
    return ' '.join((texto or '').split()).upper()


class EstoqueLoteService:
    """Cada rolo, o que saiu dele e para quem foi."""

    @classmethod
    def painel(cls, filial, busca: str = '') -> dict:
        cortes = cls._cortes(filial)
        perdas = cls._perdas_por_ordem(filial)

        lotes: dict[tuple, dict] = {}
        sem_lote = {'cortes': 0, 'metros': ZERO, 'pecas': 0}
        for corte in cortes:
            chave = chave_do_lote(corte.lote)
            if not chave:
                # Peça sem rastro: no dia do defeito não há como saber se
                # ela veio do rolo suspeito.
                sem_lote['cortes'] += 1
                sem_lote['metros'] += corte.consumo_real or ZERO
                sem_lote['pecas'] += corte.quantidade
                continue
            tecido = corte.tecido_efetivo
            linha = lotes.setdefault((tecido.pk if tecido else 0, chave),
                                     cls._vazio(chave, tecido))
            cls._somar(linha, corte, perdas)

        linhas = [cls._fechar(l) for l in lotes.values()]
        resumo = cls._resumo(linhas, sem_lote)
        return {
            'linhas': cls._ordenar(cls._filtrar(linhas, busca)),
            'sem_lote': sem_lote,
            'resumo': resumo,
            'suspeito': REFUGO_SUSPEITO,
        }

    # ── Leitura ──────────────────────────────────────────────────────────

    @staticmethod
    def _cortes(filial):
        """
        Só corte CORTADO: o planejado ainda não tirou pano do rolo, e
        listá-lo diria que há peça daquele lote na rua.
        """
        return list(
            RegistroCorte.objects.for_filial(filial)
            .filter(status=RegistroCorte.Status.CORTADO)
            .select_related(
                'tecido', 'encaixe', 'ordem__pedido__cliente',
                'ordem__item__tecido', 'ordem__item__produto__tecido',
            )
        )

    @staticmethod
    def _perdas_por_ordem(filial) -> dict:
        """
        Refugo de cada ordem, somado das etapas.

        FONTE ÚNICA, a mesma do indicador de perdas: a inspeção de qualidade
        aplicada no fluxo já está dentro de `EtapaOrdem.perda`, e somá-la
        por cima contaria a mesma peça duas vezes.
        """
        perdas: dict[int, dict] = {}
        etapas = (
            EtapaOrdem.objects
            .filter(ordem__filial=filial, status=EtapaOrdem.Status.CONCLUIDA)
            .exclude(etapa__in=NAO_PRODUZEM)
            .values_list('ordem_id', 'perda', 'quantidade_produzida')
        )
        for ordem_id, perda, produzido in etapas:
            atual = perdas.setdefault(ordem_id, {'perda': 0, 'passou': 0})
            atual['perda'] += perda
            atual['passou'] += perda + produzido
        return perdas

    # ── Acumulação ───────────────────────────────────────────────────────

    @staticmethod
    def _vazio(chave, tecido) -> dict:
        return {
            'lote': chave,
            'tecido': tecido.nome if tecido else 'Sem tecido informado',
            'fornecedor': (
                tecido.fornecedor.razao_social
                if tecido and tecido.fornecedor_id else ''
            ),
            'cortes': 0,
            'metros': ZERO,
            'pecas': 0,
            'ordens': set(),
            'pedidos': set(),
            'clientes': set(),
            'numeros': [],
            'perda': 0,
            'passou': 0,
            'medidos': ZERO,
            'ponderado': ZERO,
            'primeiro': None,
            'ultimo': None,
        }

    @classmethod
    def _somar(cls, linha, corte, perdas) -> None:
        consumo = corte.consumo_real or ZERO
        linha['cortes'] += 1
        linha['metros'] += consumo
        linha['pecas'] += corte.quantidade

        ordem = corte.ordem
        # A perda entra UMA vez por ordem, mesmo que o lote tenha vários
        # cortes dela: o refugo é da ordem, não do enfesto.
        if ordem.pk not in linha['ordens']:
            perda = perdas.get(ordem.pk)
            if perda:
                linha['perda'] += perda['perda']
                linha['passou'] += perda['passou']
            linha['numeros'].append(ordem.numero)
        linha['ordens'].add(ordem.pk)

        pedido = ordem.pedido
        if pedido:
            linha['pedidos'].add(pedido.pk)
            if pedido.cliente_id:
                linha['clientes'].add(pedido.cliente.razao_social)

        aproveitamento = corte.aproveitamento_efetivo
        if aproveitamento > 0:
            # Ponderado pelo tecido gasto, como no indicador de perdas: um
            # enfesto de 2 m não pesa igual a um de 200 m.
            linha['medidos'] += consumo
            linha['ponderado'] += aproveitamento * consumo

        if corte.data:
            if linha['primeiro'] is None or corte.data < linha['primeiro']:
                linha['primeiro'] = corte.data
            if linha['ultimo'] is None or corte.data > linha['ultimo']:
                linha['ultimo'] = corte.data

    @staticmethod
    def _fechar(linha) -> dict:
        medidos = linha['medidos']
        passou = linha['passou']
        linha['aproveitamento'] = (
            (linha['ponderado'] / medidos).quantize(Decimal('0.1'))
            if medidos else None
        )
        # Percentual sobre o que PASSOU pelas bancadas daquelas ordens, e
        # não sobre o cortado: é assim que o indicador de perdas mede, e dois
        # números diferentes para a mesma coisa fariam as telas discordarem.
        linha['percentual_perda'] = (
            (Decimal(linha['perda']) / passou * CEM).quantize(Decimal('0.1'))
            if passou else None
        )
        linha['suspeito'] = (
            linha['percentual_perda'] is not None
            and linha['percentual_perda'] >= REFUGO_SUSPEITO
        )
        linha['metros'] = linha['metros'].quantize(METRO)
        linha['qtd_ordens'] = len(linha['ordens'])
        linha['qtd_pedidos'] = len(linha['pedidos'])
        clientes = sorted(linha['clientes'])
        linha['qtd_clientes'] = len(clientes)
        linha['clientes'] = clientes[:CLIENTES_NA_LINHA]
        linha['clientes_a_mais'] = max(len(clientes) - CLIENTES_NA_LINHA, 0)
        linha['numeros'] = sorted(linha['numeros'])
        return linha

    # ── Recorte e ordem ──────────────────────────────────────────────────

    @staticmethod
    def _filtrar(linhas, busca) -> list[dict]:
        """
        Busca por lote, tecido, ordem ou cliente.

        Cliente e ordem entram de propósito: quem chega aqui costuma ter na
        mão a reclamação, não o número do rolo — e é do cliente que se anda
        para trás até o lote.
        """
        alvo = chave_do_lote(busca)
        if not alvo:
            return linhas
        achados = []
        for l in linhas:
            campos = [l['lote'], l['tecido'], l['fornecedor']]
            campos += l['numeros'] + l['clientes']
            if any(alvo in chave_do_lote(c) for c in campos):
                achados.append(l)
        return achados

    @staticmethod
    def _ordenar(linhas) -> list[dict]:
        """
        Suspeitos primeiro, maior refugo no topo; depois os mais recentes.

        Um rolo ruim se anuncia antes da reclamação, e quem abre a tela sem
        um lote em mãos quer ver o suspeito de cara.
        """
        return sorted(linhas, key=lambda l: (
            not l['suspeito'],
            -(l['percentual_perda'] or ZERO),
            -(l['ultimo'].toordinal() if l['ultimo'] else 0),
            l['lote'],
        ))

    # ── Cabeçalho ────────────────────────────────────────────────────────

    @staticmethod
    def _resumo(linhas, sem_lote) -> dict:
        suspeitos = [l for l in linhas if l['suspeito']]
        rastreadas = sum(l['pecas'] for l in linhas)
        total = rastreadas + sem_lote['pecas']
        return {
            'lotes': len(linhas),
            'pecas': rastreadas,
            'metros': sum((l['metros'] for l in linhas), ZERO).quantize(METRO),
            'suspeitos': len(suspeitos),
            # O pior é o de maior PERCENTUAL de refugo: um rolo pequeno que
            # refuga muito é mais suspeito que um grande que refuga pouco.
            'pior': max(
                suspeitos, key=lambda l: l['percentual_perda'], default=None,
            ),
            'sem_rastro': sem_lote['pecas'],
            'cobertura': (
                (Decimal(rastreadas) / total * CEM).quantize(Decimal('0.1'))
                if total else None
            ),
        }
