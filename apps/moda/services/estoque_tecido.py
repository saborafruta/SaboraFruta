"""
Estoque de tecidos — o que há no rolo, e por quanto tempo ainda dá.

METRO PARADO NÃO É O NÚMERO QUE IMPORTA. "Tem 150 m de Dry" não diz nada
sozinho: são três meses de folga se a fábrica corta 50 m por mês, e são seis
dias se ela corta 25 m por dia. O que decide se a linha para é a COBERTURA —
o saldo dividido pelo consumo de verdade —, e é por ela que a tela ordena.

DE ONDE VEM O SALDO. O tecido é cadastro do vertical; o saldo mora no
estoque do ERP, por produto e filial. A ponte é `Tecido.produto_estoque`.
Onde ela ainda não foi ligada, a tela DEDUZ o produto pelas fichas técnicas
que usam aquele tecido — assim ela já serve antes de alguém preencher o
cadastro novo. A dedução é declarada em cada linha, porque um saldo lido por
caminho indireto pode estar olhando o produto errado, e quem decide compra
precisa saber disso.

SEM VÍNCULO NENHUM O SALDO É DESCONHECIDO, e desconhecido não é zero.
Mostrar 0 m num tecido que ninguém ligou ao estoque criaria um alarme falso
por dia, e alarme falso diário é como o alarme de verdade passa batido.

O CONSUMO SAI DA MESA DE CORTE (`RegistroCorte.consumo_real`), não da ficha:
a ficha é o que se planejava gastar, e aqui a pergunta é quanto o rolo está
perdendo de verdade por dia.
"""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from ..models import MaterialFicha, RegistroCorte, Tecido

ZERO = Decimal('0')
METRO = Decimal('0.01')

PERIODOS = (('30', '30 dias'), ('90', '90 dias'), ('180', '180 dias'))

# Abaixo disto o tecido entra na fila de comprar. Duas semanas é o que
# separa "dá para resolver na próxima compra" de "a linha para esperando
# rolo" -- prazo de entrega de malha raramente é menor que isso.
COBERTURA_CRITICA = 15


class EstoqueTecidoService:
    """Saldo, consumo e cobertura de cada tecido."""

    @classmethod
    def painel(cls, filial, dias: int, busca: str = '') -> dict:
        desde = timezone.localdate() - timedelta(days=dias)
        tecidos = cls._tecidos(filial, busca)
        vinculos = cls._vinculos(filial, tecidos)
        saldos = cls._saldos(filial, vinculos)
        consumos = cls._consumos(filial, desde)

        linhas = [
            cls._linha(t, vinculos.get(t.pk), saldos, consumos.get(t.pk), dias)
            for t in tecidos
        ]
        # Pela COBERTURA, do mais apertado para o mais folgado: é a fila de
        # quem comprar primeiro. Tecido sem vínculo vai para o fim -- não
        # é urgência, é cadastro faltando, e misturá-lo com o que está
        # acabando de verdade atrapalha a leitura.
        linhas.sort(key=lambda l: (
            l['cobertura'] is None,
            l['cobertura'] if l['cobertura'] is not None else 0,
        ))
        return {
            'linhas': linhas,
            'desde': desde,
            'resumo': cls._resumo(linhas),
        }

    # ── Leitura ──────────────────────────────────────────────────────────

    @staticmethod
    def _tecidos(filial, busca):
        consulta = (
            Tecido.objects.for_filial(filial)
            .filter(ativo=True)
            .select_related('fornecedor', 'produto_estoque')
        )
        if busca:
            consulta = consulta.filter(nome__icontains=busca)
        return list(consulta)

    @staticmethod
    def _vinculos(filial, tecidos) -> dict:
        """
        Produto de estoque de cada tecido: o do cadastro, ou o deduzido.

        A dedução caça as fichas cujo produto usa este tecido e pega o
        material de tecido principal ligado ao estoque. Vale como ponte
        provisória, e cada linha carrega de onde o número veio.
        """
        direto = {
            t.pk: (t.produto_estoque, 'direto')
            for t in tecidos if t.produto_estoque_id
        }
        faltando = [t.pk for t in tecidos if t.pk not in direto]
        if not faltando:
            return direto

        materiais = (
            MaterialFicha.objects
            .filter(
                ficha__filial=filial,
                tipo=MaterialFicha.Tipo.TECIDO_PRINCIPAL,
                produto_estoque__isnull=False,
                ficha__produto__tecido_id__in=faltando,
            )
            .select_related('produto_estoque', 'ficha__produto')
        )
        deduzido = {}
        for material in materiais:
            chave = material.ficha.produto.tecido_id
            # O primeiro encontrado manda: dois produtos podem apontar para
            # produtos de estoque diferentes, e não existe critério para
            # escolher entre eles. É justamente por isso que a linha diz
            # que o vínculo foi deduzido.
            deduzido.setdefault(chave, (material.produto_estoque, 'ficha'))
        return {**deduzido, **direto}

    @staticmethod
    def _saldos(filial, vinculos) -> dict:
        from apps.estoque.models.estoque import Estoque

        ids = {produto.pk for produto, _ in vinculos.values()}
        if not ids:
            return {}
        return {
            e.produto_id: e
            for e in Estoque.objects.filter(produto_id__in=ids, filial=filial)
        }

    @staticmethod
    def _consumos(filial, desde) -> dict:
        """
        Metros cortados por tecido no período.

        Agrupado por `tecido_efetivo` e não pelo campo do corte: o corte
        herda o tecido do item da ordem quando não tem um próprio, e ler o
        campo cru deixaria de fora justamente os cortes que não pediram
        exceção.
        """
        cortes = (
            RegistroCorte.objects.for_filial(filial)
            .filter(status=RegistroCorte.Status.CORTADO, data__gte=desde)
            .select_related('tecido', 'ordem__item__tecido',
                            'ordem__item__produto__tecido')
        )
        consumos: dict[int, dict] = {}
        for corte in cortes:
            tecido = corte.tecido_efetivo
            if tecido is None:
                continue
            linha = consumos.setdefault(tecido.pk, {'metros': ZERO, 'cortes': 0})
            linha['metros'] += corte.consumo_real or ZERO
            linha['cortes'] += 1
        return consumos

    # ── Uma linha ────────────────────────────────────────────────────────

    @classmethod
    def _linha(cls, tecido, vinculo, saldos, consumo, dias) -> dict:
        produto, origem = vinculo if vinculo else (None, None)
        estoque = saldos.get(produto.pk) if produto else None
        consumo = consumo or {'metros': ZERO, 'cortes': 0}

        disponivel = estoque.quantidade_disponivel if estoque else None
        metros = consumo['metros']
        por_dia = (metros / Decimal(dias)) if metros else ZERO

        return {
            'tecido': tecido,
            'nome': tecido.nome,
            'composicao': tecido.composicao,
            'gramatura': tecido.gramatura,
            'largura_cm': tecido.largura_cm,
            'fornecedor': tecido.fornecedor.razao_social if tecido.fornecedor else '',
            'produto': produto,
            'origem': origem,
            'deduzido': origem == 'ficha',
            'ligado': produto is not None,
            'saldo': estoque.quantidade_atual.quantize(METRO) if estoque else None,
            'reservado': estoque.quantidade_reservada.quantize(METRO) if estoque else None,
            'disponivel': disponivel.quantize(METRO) if disponivel is not None else None,
            'custo_medio': estoque.custo_medio if estoque else None,
            'valor': (
                (estoque.quantidade_atual * estoque.custo_medio).quantize(METRO)
                if estoque else None
            ),
            'consumo': metros.quantize(METRO),
            'cortes': consumo['cortes'],
            'consumo_dia': por_dia.quantize(METRO),
            'cobertura': cls._cobertura(disponivel, por_dia),
            # `sem_saldo` é diferente de `not ligado`: aqui o sistema SABE
            # que acabou, e ali ele não sabe nada.
            'sem_saldo': disponivel is not None and disponivel <= 0,
        }

    @staticmethod
    def _cobertura(disponivel, por_dia):
        """
        Dias que o saldo aguenta no ritmo do período.

        None quando falta um dos dois lados, e são coisas diferentes: sem
        vínculo não se sabe o saldo; sem consumo não há ritmo com que
        projetar — um tecido parado não "dura para sempre", ele só não está
        sendo usado, e fingir cobertura infinita o esconderia da tela.
        """
        if disponivel is None or por_dia <= 0:
            return None
        return int(disponivel / por_dia)

    # ── Cabeçalho ────────────────────────────────────────────────────────

    @staticmethod
    def _resumo(linhas) -> dict:
        ligadas = [l for l in linhas if l['ligado']]
        criticas = [
            l for l in linhas
            if l['cobertura'] is not None and l['cobertura'] < COBERTURA_CRITICA
        ]
        return {
            'tecidos': len(linhas),
            'sem_vinculo': sum(1 for l in linhas if not l['ligado']),
            'deduzidos': sum(1 for l in linhas if l['deduzido']),
            # O `ZERO` inicial nao e' enfeite: sem ele `sum` de uma lista
            # vazia devolve o int 0, que nao tem `quantize` -- e a tela
            # de uma fabrica sem tecido ligado quebrava.
            'valor': sum((l['valor'] or ZERO for l in ligadas), ZERO).quantize(METRO),
            'consumo': sum((l['consumo'] for l in linhas), ZERO).quantize(METRO),
            'criticos': len(criticas),
            'zerados': sum(1 for l in linhas if l['sem_saldo']),
            # O mais apertado é o primeiro a parar a linha, e é ele que a
            # compra tem de resolver antes dos outros. Calculado aqui em vez
            # de assumir a ordem da lista: quem chamar este resumo com as
            # linhas em outra ordem receberia o tecido errado.
            'pior': min(criticas, key=lambda l: l['cobertura'], default=None),
            'limite': COBERTURA_CRITICA,
        }
