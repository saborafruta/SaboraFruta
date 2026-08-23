"""
Estoque de aviamentos — o que há de linha, botão, zíper e etiqueta.

AVIAMENTO NÃO SE MEDE COMO TECIDO, e a diferença muda a pergunta da tela.
O tecido tem consumo real gravado na mesa de corte, então dá para dizer
quantos dias o rolo aguenta. Aviamento não tem apontamento nenhum: ninguém
registra quantos zíperes foram pregados. Projetar um ritmo de consumo aqui
seria inventar.

ENTÃO A COBERTURA VEM DA DEMANDA, e não do passado: o que as ORDENS ABERTAS
vão precisar, contra o que há em estoque. É a pergunta que aviamento
responde bem — "o que está na fábrica cabe no que eu tenho?" — e é a mesma
conta que o painel de necessidade já faz. Reusada daqui, não refeita: duas
implementações da mesma conta divergem, e aí as duas telas discordam sobre
o que falta comprar.

O AVIAMENTO NÃO TEM CADASTRO PRÓPRIO. Ele existe dentro da ficha, como uma
linha de `MaterialFicha`. A lista sai daí, agrupada pela mesma chave do
painel de necessidade (`chave_do_material`) — produto de estoque quando há,
texto quando não há.

SEM LIGAÇÃO COM ESTOQUE NÃO HÁ SALDO, e não haver saldo é diferente de o
saldo ser zero. A linha mostra traço, como na tela de tecidos, pelo mesmo
motivo: um zero inventado vira alarme falso diário.
"""
from decimal import Decimal

from django.db.models import Q

from ..models import MaterialFicha
from .necessidade import NecessidadeService, chave_do_material

ZERO = Decimal('0')
CENTAVO = Decimal('0.01')

# O que é aviamento, e o que é tecido — a divisão segue a ficha de papel:
# tecido e forro são o corpo da peça, o resto é o que se prega nela.
#
# Mora aqui e não na view porque as DUAS telas de aviamento a usam (esta e
# a de Engenharia, que importa daqui): duas listas do que conta como
# aviamento divergem, e aí uma tela mostra a etiqueta e a outra não.
TIPOS_AVIAMENTO = [
    MaterialFicha.Tipo.LINHA,
    MaterialFicha.Tipo.ELASTICO,
    MaterialFicha.Tipo.ZIPER,
    MaterialFicha.Tipo.BOTAO,
    MaterialFicha.Tipo.ETIQUETA,
    MaterialFicha.Tipo.TAG,
    MaterialFicha.Tipo.EMBALAGEM,
    MaterialFicha.Tipo.AVIAMENTO,
]


class EstoqueAviamentoService:
    """Saldo de cada aviamento, contra o que as ordens abertas pedem."""

    @classmethod
    def painel(cls, filial, busca: str = '', filtro: str = '') -> dict:
        materiais = cls._materiais(filial, busca)
        demandas = cls._demandas(filial)
        linhas = cls._agrupar(materiais, demandas)
        cls._preencher_estoque(filial, linhas)

        linhas = cls._ordenar(linhas)
        resumo = cls._resumo(linhas)
        return {
            'linhas': cls._filtrar(linhas, filtro),
            'resumo': resumo,
        }

    # ── Leitura ──────────────────────────────────────────────────────────

    @staticmethod
    def _materiais(filial, busca):
        consulta = (
            MaterialFicha.objects
            .filter(ficha__filial=filial, tipo__in=TIPOS_AVIAMENTO)
            .select_related('produto_estoque', 'ficha__produto')
        )
        if busca:
            consulta = consulta.filter(
                Q(descricao__icontains=busca) | Q(codigo__icontains=busca)
            )
        return list(consulta)

    @staticmethod
    def _demandas(filial) -> dict:
        """
        O que as ordens abertas vão consumir, pela chave do material.

        Vem do painel de necessidade inteiro (tecido incluído) e é filtrado
        pelo `join`: pedir ao serviço uma versão só de aviamento exigiria
        duplicar o cálculo, e é justamente o cálculo que não pode divergir.
        """
        return {l.chave: l for l in NecessidadeService.calcular(filial)}

    # ── Agrupamento ──────────────────────────────────────────────────────

    @staticmethod
    def _agrupar(materiais, demandas) -> list[dict]:
        grupos: dict[str, dict] = {}
        for material in materiais:
            chave = chave_do_material(material)
            grupo = grupos.setdefault(chave, {
                'chave': chave,
                'tipo': material.tipo,
                'tipo_rotulo': material.get_tipo_display(),
                'descricao': material.descricao,
                'codigo': material.codigo,
                'unidades': set(),
                'produto': material.produto_estoque,
                'fichas': 0,
                'precos': set(),
            })
            grupo['unidades'].add(material.get_unidade_display())
            grupo['fichas'] += 1
            if material.custo_unitario:
                grupo['precos'].add(material.custo_unitario)
            if material.produto_estoque_id and grupo['produto'] is None:
                grupo['produto'] = material.produto_estoque

        for grupo in grupos.values():
            demanda = demandas.get(grupo['chave'])
            grupo['unidades'] = sorted(grupo['unidades'])
            grupo['precos'] = sorted(grupo['precos'])
            # PREÇO DIVERGENTE não é detalhe: o mesmo zíper com dois valores
            # em fichas diferentes significa que um dos custos está velho, e
            # o custo da peça sai errado dos dois jeitos.
            grupo['preco_divergente'] = len(grupo['precos']) > 1
            grupo['preco'] = grupo['precos'][0] if grupo['precos'] else None
            grupo['ligado'] = grupo['produto'] is not None
            grupo['previsto'] = demanda.previsto if demanda else ZERO
            grupo['ordens'] = len(demanda.ordens) if demanda else 0
            # A linha da necessidade fica guardada porque `livre` e `falta`
            # só se resolvem junto com o saldo, no passo seguinte.
            grupo['demanda'] = demanda
            grupo['saldo'] = None
            grupo['reservado'] = None
            grupo['livre'] = None
            grupo['falta'] = None
            grupo['custo_medio'] = None
            grupo['valor'] = None
        return list(grupos.values())

    @staticmethod
    def _preencher_estoque(filial, linhas) -> None:
        """
        Saldo físico e valor parado, por produto e filial.

        O `livre` já veio da necessidade (que desconta reserva de outros);
        aqui entram o saldo bruto e o custo, que a necessidade não devolve
        porque não é a pergunta dela.
        """
        from apps.estoque.models.estoque import Estoque

        ids = {l['produto'].pk for l in linhas if l['ligado']}
        if not ids:
            return
        saldos = {
            e.produto_id: e
            for e in Estoque.objects.filter(produto_id__in=ids, filial=filial)
        }
        for linha in linhas:
            # Sem vínculo tudo fica None: é "não sei", e nunca zero, que
            # seria "acabou".
            if not linha['ligado']:
                continue

            saldo = saldos.get(linha['produto'].pk)
            if saldo is None:
                # Produto ligado mas sem registro de estoque NESTA filial:
                # o saldo é zero de verdade, e não desconhecido -- o
                # cadastro existe, a filial é que não tem o item.
                linha['saldo'] = linha['reservado'] = ZERO
                linha['custo_medio'] = linha['valor'] = ZERO
            else:
                linha['saldo'] = saldo.quantidade_atual
                linha['reservado'] = saldo.quantidade_reservada
                linha['custo_medio'] = saldo.custo_medio
                linha['valor'] = (
                    saldo.quantidade_atual * saldo.custo_medio
                ).quantize(CENTAVO)

            demanda = linha['demanda']
            if demanda is not None:
                linha['livre'] = demanda.livre
                linha['falta'] = demanda.deficit
            else:
                # Ligado e sem demanda nenhuma: nenhuma ordem aberta pede
                # este aviamento. A falta é ZERO de verdade -- deixá-la
                # None mandaria a linha para o mesmo traço de quem não tem
                # vínculo, e ali o sistema não sabe nada.
                disponivel = saldo.quantidade_disponivel if saldo else ZERO
                linha['livre'] = max(disponivel, ZERO)
                linha['falta'] = ZERO

    # ── Ordem e recorte ──────────────────────────────────────────────────

    @staticmethod
    def _ordenar(linhas) -> list[dict]:
        """
        O que falta primeiro, e dentro disso a maior falta no topo.

        A tela existe para responder "o que preciso comprar", e a resposta
        tem de estar na primeira linha — ordenar por nome faria quem olha
        procurar o problema no meio de uma lista alfabética.
        """
        return sorted(linhas, key=lambda l: (
            not (l['falta'] or ZERO) > ZERO,
            -(l['falta'] or ZERO),
            l['tipo_rotulo'],
            (l['descricao'] or '').upper(),
        ))

    @staticmethod
    def _filtrar(linhas, filtro) -> list[dict]:
        if filtro == 'faltando':
            return [l for l in linhas if (l['falta'] or ZERO) > ZERO]
        if filtro == 'sem_vinculo':
            return [l for l in linhas if not l['ligado']]
        if filtro == 'preco_divergente':
            return [l for l in linhas if l['preco_divergente']]
        return linhas

    # ── Cabeçalho ────────────────────────────────────────────────────────

    @staticmethod
    def _resumo(linhas) -> dict:
        faltando = [l for l in linhas if (l['falta'] or ZERO) > ZERO]
        ligadas = [l for l in linhas if l['ligado']]
        return {
            'aviamentos': len(linhas),
            'faltando': len(faltando),
            'sem_vinculo': sum(1 for l in linhas if not l['ligado']),
            'preco_divergente': sum(1 for l in linhas if l['preco_divergente']),
            'valor': sum((l['valor'] or ZERO for l in ligadas), ZERO).quantize(CENTAVO),
            # O de maior falta é o primeiro a parar a montagem: uma camisa
            # sem etiqueta não sai, por mais pronta que esteja.
            'pior': max(faltando, key=lambda l: l['falta'], default=None),
        }
