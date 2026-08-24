"""
DRE gerencial — o resultado do mês, montado a partir dos títulos.

CALCULADO NA LEITURA, e não lido de uma tabela consolidada. Existe um
`DREConsolidado` gravado no banco, e ele continua onde está porque o
Analytics o consome — mas um resultado GRAVADO fica velho no instante em
que alguém corrige uma baixa, e ninguém confia num número que pode estar
velho. Aqui a conta é refeita a cada abertura, sobre os títulos que existem
naquele momento.

REGIME É ESCOLHA DE QUEM OLHA, e a diferença é material:

  CAIXA        o que entrou e saiu de dinheiro no mês. Só título PAGO, pela
               `data_pagamento` e pelo `valor_pago`. É o que bate com o
               extrato bancário.
  COMPETÊNCIA  o que foi do mês, tenha sido pago ou não. Todo título vivo,
               pela data de competência e pelo `valor_final`. É o que
               responde "este mês deu lucro?".

Um mês pode ter caixa excelente e competência no vermelho — basta receber
em janeiro o que se vendeu em dezembro. Mostrar um só dos dois e chamá-lo
de "o resultado" é como o dono se engana com o próprio negócio.

MOVIMENTO SEM CATEGORIA APARECE. É a regra mais importante desta tela: um
DRE que descarta em silêncio o que não foi classificado fecha bonito e está
errado, e o erro cresce com o descuido do cadastro. Aqui ele vira uma linha
própria, com o valor à vista — some do relatório só quando alguém
classificar.

A ÁRVORE VEM DA CATEGORIA FINANCEIRA (`PlanoContas`), agrupada pela conta
RAIZ. É o cadastro que o usuário já mantém e já usa para lançar; montar o
DRE sobre o `codigo_dre` do plano contábil seria mais formal e dependeria de
um de-para que hoje ninguém preenche.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Q, Sum
from django.db.models.functions import Coalesce

from apps.financeiro.constants.enums import StatusContaPagar, StatusContaReceber
from apps.financeiro.models import ContaPagar, ContaReceber, PlanoContas

ZERO = Decimal('0')
CEM = Decimal('100')

CAIXA = 'caixa'
COMPETENCIA = 'competencia'
REGIMES = (
    (CAIXA, 'Caixa'),
    (COMPETENCIA, 'Competência'),
)

# Título morto não é resultado: cancelado nunca foi receita nem despesa, e
# devolvido deixou de ser. Mantê-los inflaria os dois lados do DRE.
RECEBER_MORTOS = (StatusContaReceber.CANCELADO, StatusContaReceber.DEVOLVIDO)
PAGAR_MORTOS = (StatusContaPagar.CANCELADO,)

RECEITA = 'R'
DESPESA = 'D'


def primeiro_dia(referencia: date) -> date:
    return referencia.replace(day=1)


def mes_anterior(referencia: date) -> date:
    primeiro = primeiro_dia(referencia)
    return (primeiro - timedelta(days=1)).replace(day=1)


def proximo_mes(referencia: date) -> date:
    primeiro = primeiro_dia(referencia)
    if primeiro.month == 12:
        return primeiro.replace(year=primeiro.year + 1, month=1)
    return primeiro.replace(month=primeiro.month + 1)


def _pct(numerador, denominador):
    """Percentual, ou None quando não há base — que NÃO é o mesmo que 0%."""
    if not denominador:
        return None
    return (Decimal(numerador) / Decimal(denominador) * CEM).quantize(Decimal('0.1'))


class DREService:
    """Receitas e despesas do período, pela árvore de categorias."""

    @classmethod
    def painel(cls, filial, competencia: date, regime: str = CAIXA) -> dict:
        competencia = primeiro_dia(competencia)
        regime = regime if regime in dict(REGIMES) else CAIXA

        arvore = cls._arvore(filial)
        mes = cls._periodo(filial, competencia, proximo_mes(competencia), regime, arvore)
        anterior_ini = mes_anterior(competencia)
        anterior = cls._periodo(
            filial, anterior_ini, competencia, regime, arvore,
        )
        # Acumulado do ANO até o fim do mês escolhido: é a leitura que
        # separa um mês ruim de uma tendência ruim.
        ano = cls._periodo(
            filial, competencia.replace(month=1, day=1),
            proximo_mes(competencia), regime, arvore,
        )

        linhas = cls._comparar(mes, anterior, ano)
        return {
            'competencia': competencia,
            'regime': regime,
            'regimes': REGIMES,
            'mes': mes,
            'anterior': anterior,
            'ano': ano,
            'linhas': linhas,
            'resumo': cls._resumo(mes, anterior, ano),
            # `linhas` NUNCA e' vazia -- ela sempre traz os dois blocos, de
            # receita e de despesa, mesmo sem um titulo sequer. Sem esta
            # bandeira o template caia no caminho normal e desenhava dois
            # cabecalhos e dois totais zerados, sem dizer que nao ha' nada.
            'vazio': not any(
                b['linhas'] or b['sem_categoria']['tem'] for b in linhas
            ),
        }

    # ── A árvore de categorias ───────────────────────────────────────────

    @staticmethod
    def _arvore(filial) -> dict:
        """
        Cada categoria e a RAIZ dela, resolvida de uma vez.

        Subir `conta_pai` por consulta, categoria a categoria, seria uma ida
        ao banco por título lido. Aqui o cadastro inteiro vem numa consulta
        e a subida acontece em memória.
        """
        empresa = getattr(filial, 'empresa_id', None)
        contas = {
            c.pk: c for c in PlanoContas.objects.filter(
                Q(empresa_id=empresa) | Q(empresa__isnull=True),
            ).select_related('conta_pai')
        }

        raizes = {}
        for pk, conta in contas.items():
            atual = conta
            visitados = set()
            # O `visitados` não é zelo excessivo: `conta_pai` aponta para a
            # própria tabela, e um ciclo no cadastro (A filha de B, B filha
            # de A) travaria a tela inteira num laço infinito.
            while atual.conta_pai_id and atual.conta_pai_id in contas:
                if atual.pk in visitados:
                    break
                visitados.add(atual.pk)
                atual = contas[atual.conta_pai_id]
            raizes[pk] = atual
        return {'contas': contas, 'raizes': raizes}

    # ── Um período ───────────────────────────────────────────────────────

    @classmethod
    def _periodo(cls, filial, inicio, fim, regime, arvore) -> dict:
        receitas = cls._somar(
            ContaReceber.objects.for_filial(filial).exclude(status__in=RECEBER_MORTOS),
            regime, inicio, fim, campo_competencia='competencia',
        )
        despesas = cls._somar(
            # `excluido_em` é exclusão lógica: o título apagado continua na
            # tabela, e somá-lo cobraria do resultado uma despesa que alguém
            # já disse que não existe.
            ContaPagar.objects.for_filial(filial)
            .exclude(status__in=PAGAR_MORTOS).filter(excluido_em__isnull=True),
            regime, inicio, fim, campo_competencia='data_competencia',
        )

        return {
            'receita': cls._agrupar(receitas, arvore, RECEITA),
            'despesa': cls._agrupar(despesas, arvore, DESPESA),
        }

    @staticmethod
    def _somar(consulta, regime, inicio, fim, campo_competencia):
        """
        Soma por categoria, no regime pedido.

        No caixa vale a data e o valor do PAGAMENTO; na competência, a data
        de competência (ou o vencimento, quando ela está em branco) e o
        valor de face. Misturar os dois daria um número que não é nem um nem
        outro.
        """
        if regime == CAIXA:
            consulta = consulta.filter(
                status='pago', data_pagamento__gte=inicio, data_pagamento__lt=fim,
            )
            valor = 'valor_pago'
        else:
            # Competência em branco cai no vencimento: é o que a casa usa
            # como aproximação, e deixar o título de fora seria pior --
            # sumiria dinheiro do relatório por falta de cadastro.
            consulta = consulta.annotate(
                _quando=Coalesce(campo_competencia, 'data_vencimento'),
            ).filter(_quando__gte=inicio, _quando__lt=fim)
            valor = 'valor_final'

        return list(
            consulta.values('plano_contas_id').annotate(total=Sum(valor)),
        )

    @staticmethod
    def _agrupar(somas, arvore, tipo) -> dict:
        """
        Agrupa pela categoria RAIZ, guardando as filhas por dentro.

        Sem categoria vira uma linha própria -- nunca some. Um DRE que
        descarta em silêncio o que não foi classificado fecha bonito e está
        errado.
        """
        grupos: dict = {}
        sem_categoria = ZERO

        for linha in somas:
            total = linha['total'] or ZERO
            conta = arvore['contas'].get(linha['plano_contas_id'])
            if conta is None:
                sem_categoria += total
                continue
            raiz = arvore['raizes'][conta.pk]
            grupo = grupos.setdefault(raiz.pk, {
                'codigo': raiz.codigo,
                'descricao': raiz.descricao,
                'tipo': raiz.tipo or tipo,
                'total': ZERO,
                'filhas': {},
            })
            grupo['total'] += total
            if conta.pk != raiz.pk:
                filha = grupo['filhas'].setdefault(conta.pk, {
                    'codigo': conta.codigo, 'descricao': conta.descricao,
                    'total': ZERO,
                })
                filha['total'] += total

        return {
            'grupos': grupos,
            'sem_categoria': sem_categoria,
            'total': sum((g['total'] for g in grupos.values()), ZERO) + sem_categoria,
        }

    # ── Comparação entre os três períodos ────────────────────────────────

    @classmethod
    def _comparar(cls, mes, anterior, ano) -> list[dict]:
        """
        As linhas da tela: cada grupo com mês, mês anterior e acumulado.

        Uma categoria que existiu no mês passado e sumiu neste PRECISA
        aparecer zerada: some-la esconderia justamente a queda.
        """
        blocos = []
        for chave, rotulo in (('receita', 'Receitas'), ('despesa', 'Despesas')):
            pks = (
                set(mes[chave]['grupos'])
                | set(anterior[chave]['grupos'])
                | set(ano[chave]['grupos'])
            )
            linhas = []
            for pk in pks:
                atual = mes[chave]['grupos'].get(pk)
                velho = anterior[chave]['grupos'].get(pk)
                acumulado = ano[chave]['grupos'].get(pk)
                referencia = atual or acumulado or velho
                linhas.append(cls._linha(referencia, atual, velho, acumulado))
            linhas.sort(key=lambda l: (l['codigo'] or '', l['descricao']))

            sem = (
                mes[chave]['sem_categoria'], anterior[chave]['sem_categoria'],
                ano[chave]['sem_categoria'],
            )
            blocos.append({
                'chave': chave,
                'rotulo': rotulo,
                'linhas': linhas,
                'sem_categoria': {
                    'mes': sem[0], 'anterior': sem[1], 'ano': sem[2],
                    'tem': any(v for v in sem),
                },
                'total_mes': mes[chave]['total'],
                'total_anterior': anterior[chave]['total'],
                'total_ano': ano[chave]['total'],
            })
        return blocos

    @staticmethod
    def _linha(referencia, atual, velho, acumulado) -> dict:
        valor = (atual or {}).get('total', ZERO)
        antes = (velho or {}).get('total', ZERO)
        filhas = sorted(
            (atual or {}).get('filhas', {}).values(),
            key=lambda f: (f['codigo'] or '', f['descricao']),
        )
        return {
            'codigo': referencia['codigo'],
            'descricao': referencia['descricao'],
            'mes': valor,
            'anterior': antes,
            'ano': (acumulado or {}).get('total', ZERO),
            'variacao': valor - antes,
            'variacao_pct': _pct(valor - antes, antes),
            'filhas': filhas,
        }

    # ── Cabeçalho ────────────────────────────────────────────────────────

    @staticmethod
    def _resumo(mes, anterior, ano) -> dict:
        receita = mes['receita']['total']
        despesa = mes['despesa']['total']
        resultado = receita - despesa

        receita_ant = anterior['receita']['total']
        resultado_ant = receita_ant - anterior['despesa']['total']

        sem_categoria = (
            mes['receita']['sem_categoria'] + mes['despesa']['sem_categoria']
        )
        return {
            'receita': receita,
            'despesa': despesa,
            'resultado': resultado,
            'margem': _pct(resultado, receita),
            'receita_anterior': receita_ant,
            'resultado_anterior': resultado_ant,
            'variacao_resultado': resultado - resultado_ant,
            'receita_ano': ano['receita']['total'],
            'despesa_ano': ano['despesa']['total'],
            'resultado_ano': ano['receita']['total'] - ano['despesa']['total'],
            'margem_ano': _pct(
                ano['receita']['total'] - ano['despesa']['total'],
                ano['receita']['total'],
            ),
            # O tamanho do que ainda não foi classificado: é a medida de
            # quanto deste relatório dá para levar a sério.
            'sem_categoria': sem_categoria,
            'sem_categoria_pct': _pct(sem_categoria, receita + despesa),
        }
