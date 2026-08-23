"""
Estoque de produtos — o que há de peça pronta no catálogo.

O SALDO SOZINHO ENGANA NUMA CONFECÇÃO. A maior parte do que sai daqui é
feito sob encomenda: a camisa do time do bairro nasce contra um pedido e vai
embora, e nunca chega a ser "estoque". Um produto com saldo zero e duzentas
peças na linha está bem; um com trinta peças paradas há oito meses está mal,
e o número que os separa não é o saldo — é o que está VINDO e há quanto
tempo nada SAI.

Por isso a tela mostra três coisas ao lado do saldo:

  EM PRODUÇÃO -- peças nas ordens ainda abertas. É o que vai chegar, e sem
      isso um saldo baixo parece problema quando não é.
  PARADO HÁ -- dias desde a última saída daquele item. Peça pronta que
      sobrou de um pedido e nunca saiu é dinheiro preso, e o tempo é o
      único jeito de enxergá-la: ela não aparece em falta nenhuma.
  VALOR PARADO -- e é por ele que a tela ordena, porque a pergunta de
      estoque acabado é "onde meu dinheiro está preso".

O SALDO É DO PRODUTO DO ERP, e não da variante. `Variante` tem SKU mas não
tem estoque próprio: o vínculo com o saldo é `ProdutoModa.produto_erp`. Na
prática isso significa que a tela sabe que há trinta camisas amarelas, e não
sabe quantas são M e quantas são G. A contagem de SKUs fica na linha para
essa limitação não passar despercebida.

SEM VÍNCULO COM O ERP NÃO HÁ SALDO, e não haver saldo é diferente de o
saldo ser zero — a mesma regra das telas de tecido e aviamento.
"""
from decimal import Decimal

from django.db.models import Count, Q
from django.utils import timezone

from ..models import OrdemProducao, ProdutoModa

ZERO = Decimal('0')
CENTAVO = Decimal('0.01')

# A partir daqui a peça pronta deixa de ser estoque e vira dinheiro preso.
# Três meses é o que separa "sobrou do pedido do mês passado" de "ninguém
# vai levar isso": uma coleção de confecção raramente vive mais que isso.
DIAS_PARADO = 90

FILTROS = ('parado', 'com_saldo', 'sem_vinculo', 'em_producao')


class EstoqueProdutoService:
    """Saldo, produção em curso e tempo parado de cada produto."""

    @classmethod
    def painel(cls, filial, busca: str = '', filtro: str = '') -> dict:
        produtos = cls._produtos(filial, busca)
        linhas = [cls._linha(p) for p in produtos]
        cls._preencher_estoque(filial, linhas)
        cls._preencher_producao(filial, linhas)

        linhas = cls._ordenar(linhas)
        return {
            # O resumo descreve a FÁBRICA e é calculado antes do filtro:
            # filtrar por "parado" não pode zerar o contador de "sem
            # vínculo", senão o cabeçalho passa a descrever o recorte.
            'resumo': cls._resumo(linhas),
            'linhas': cls._filtrar(linhas, filtro),
            'dias_parado': DIAS_PARADO,
        }

    # ── Leitura ──────────────────────────────────────────────────────────

    @staticmethod
    def _produtos(filial, busca):
        consulta = (
            ProdutoModa.objects.for_filial(filial)
            .filter(ativo=True)
            .select_related('produto_erp', 'modelo', 'colecao')
            .annotate(skus=Count('variantes', filter=Q(variantes__ativo=True)))
        )
        if busca:
            consulta = consulta.filter(
                Q(nome__icontains=busca) | Q(codigo__icontains=busca)
                | Q(referencia__icontains=busca)
            )
        return list(consulta)

    @staticmethod
    def _linha(produto) -> dict:
        return {
            'produto': produto,
            'codigo': produto.codigo,
            'nome': produto.nome,
            'modelo': produto.modelo.nome if produto.modelo_id else '',
            'colecao': produto.colecao.nome if produto.colecao_id else '',
            'status': produto.get_status_display(),
            'descontinuado': produto.status == ProdutoModa.Status.DESCONTINUADO,
            'skus': produto.skus,
            'ligado': produto.produto_erp_id is not None,
            # Tudo o que depende do estoque nasce None: é "não sei", e
            # nunca zero, que seria "acabou".
            'saldo': None,
            'reservado': None,
            'disponivel': None,
            'valor': None,
            'ultima_saida': None,
            'dias_parado': None,
            'em_producao': 0,
            'ordens': 0,
        }

    @staticmethod
    def _preencher_estoque(filial, linhas) -> None:
        from apps.estoque.models.estoque import Estoque

        ids = {l['produto'].produto_erp_id for l in linhas if l['ligado']}
        if not ids:
            return
        saldos = {
            e.produto_id: e
            for e in Estoque.objects.filter(produto_id__in=ids, filial=filial)
        }
        hoje = timezone.now()
        for linha in linhas:
            if not linha['ligado']:
                continue
            saldo = saldos.get(linha['produto'].produto_erp_id)
            if saldo is None:
                # Produto ligado e sem registro NESTA filial: zero de
                # verdade. O cadastro existe, a filial é que não tem a peça.
                linha['saldo'] = linha['reservado'] = ZERO
                linha['disponivel'] = linha['valor'] = ZERO
                continue
            linha['saldo'] = saldo.quantidade_atual
            linha['reservado'] = saldo.quantidade_reservada
            linha['disponivel'] = saldo.quantidade_disponivel
            linha['valor'] = (saldo.quantidade_atual * saldo.custo_medio).quantize(CENTAVO)
            linha['ultima_saida'] = saldo.ultima_saida
            # Só faz sentido falar em "parado" havendo peça parada: um
            # produto zerado há um ano não é dinheiro preso, é produto que
            # não se faz mais.
            if saldo.quantidade_atual > 0:
                referencia = saldo.ultima_saida or saldo.ultima_entrada
                if referencia:
                    linha['dias_parado'] = (hoje - referencia).days

    @staticmethod
    def _preencher_producao(filial, linhas) -> None:
        """
        Peças nas ordens ainda abertas, por produto.

        É o que vai chegar. Sem esta coluna um saldo baixo parece problema
        quando na verdade tem duzentas peças na costura.
        """
        indice = {
            l['produto'].pk: l for l in linhas
        }
        ordens = (
            OrdemProducao.objects.for_filial(filial)
            .exclude(status__in=OrdemProducao.STATUS_ENCERRADOS)
            .values_list('item__produto_id', 'quantidade')
        )
        for produto_id, quantidade in ordens:
            linha = indice.get(produto_id)
            if linha is None:
                continue
            linha['em_producao'] += quantidade or 0
            linha['ordens'] += 1

    # ── Ordem e recorte ──────────────────────────────────────────────────

    @staticmethod
    def _ordenar(linhas) -> list[dict]:
        """
        Pelo VALOR PARADO, do maior para o menor.

        A pergunta de estoque acabado é "onde meu dinheiro está preso", e a
        resposta tem de estar na primeira linha. Ordenar por nome faria
        procurar o problema no meio de uma lista alfabética.
        """
        return sorted(linhas, key=lambda l: (
            -(l['valor'] or ZERO),
            -(l['em_producao']),
            (l['nome'] or '').upper(),
        ))

    @staticmethod
    def _filtrar(linhas, filtro) -> list[dict]:
        if filtro == 'parado':
            return [
                l for l in linhas
                if l['dias_parado'] is not None and l['dias_parado'] >= DIAS_PARADO
            ]
        if filtro == 'com_saldo':
            return [l for l in linhas if (l['saldo'] or ZERO) > ZERO]
        if filtro == 'sem_vinculo':
            return [l for l in linhas if not l['ligado']]
        if filtro == 'em_producao':
            return [l for l in linhas if l['em_producao']]
        return linhas

    # ── Cabeçalho ────────────────────────────────────────────────────────

    @staticmethod
    def _resumo(linhas) -> dict:
        parados = [
            l for l in linhas
            if l['dias_parado'] is not None and l['dias_parado'] >= DIAS_PARADO
        ]
        return {
            'produtos': len(linhas),
            'com_saldo': sum(1 for l in linhas if (l['saldo'] or ZERO) > ZERO),
            'sem_vinculo': sum(1 for l in linhas if not l['ligado']),
            'em_producao': sum(l['em_producao'] for l in linhas),
            'ordens': sum(l['ordens'] for l in linhas),
            'valor': sum((l['valor'] or ZERO for l in linhas), ZERO).quantize(CENTAVO),
            'parados': len(parados),
            'valor_parado': sum(
                (l['valor'] or ZERO for l in parados), ZERO,
            ).quantize(CENTAVO),
            # O pior é o de maior VALOR preso, e não o de mais tempo: seis
            # meses de uma peça de dez reais não é o problema que trinta
            # dias de mil reais é.
            'pior': max(parados, key=lambda l: l['valor'], default=None),
            'limite': DIAS_PARADO,
        }
