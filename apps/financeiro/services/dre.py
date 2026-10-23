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
  VENCIMENTO   o que vence no mês, pago ou não. Todo título vivo, pela
               `data_vencimento` e pelo `valor_final`. É a visão de agenda
               financeira e pressão de caixa futura.

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

from django.db.models import Count, F, Q, Sum
from django.db.models.functions import Coalesce

from apps.financeiro.constants.enums import StatusContaPagar, StatusContaReceber
from apps.financeiro.models import (
    ContaPagar,
    ContaReceber,
    ExtratoBancario,
    PagamentoContaPagar,
    PagamentoContaReceber,
    PlanoContas,
)

ZERO = Decimal('0')
CEM = Decimal('100')

CAIXA = 'caixa'
COMPETENCIA = 'competencia'
VENCIMENTO = 'vencimento'
REGIMES = (
    (CAIXA, 'Caixa'),
    (COMPETENCIA, 'Competência'),
    (VENCIMENTO, 'Vencimento'),
)

# Título morto não é resultado: cancelado nunca foi receita nem despesa, e
# devolvido deixou de ser. Mantê-los inflaria os dois lados do DRE.
RECEBER_MORTOS = (StatusContaReceber.CANCELADO, StatusContaReceber.DEVOLVIDO)
PAGAR_MORTOS = (StatusContaPagar.CANCELADO,)

RECEITA = 'R'
DESPESA = 'D'

CORES_GRAFICO = (
    '#2563eb', '#ef4444', '#f59e0b', '#10b981', '#8b5cf6', '#ec4899',
    '#06b6d4', '#84cc16', '#f97316', '#6366f1', '#14b8a6', '#a855f7',
)


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
            'grafico_despesas': cls._grafico_despesas(mes['despesa']),
            'comparativo_mensal': cls._comparativo_mensal(
                filial, competencia, regime, arvore, quantidade=6,
            ),
            'categorias_disponiveis': {
                tipo: sum(
                    1 for conta in arvore['contas'].values()
                    if conta.tipo == tipo and conta.ativo and conta.aceita_lancamento
                )
                for tipo in (RECEITA, DESPESA)
            },
            # `linhas` NUNCA e' vazia -- ela sempre traz os dois blocos, de
            # receita e de despesa, mesmo sem um titulo sequer. Sem esta
            # bandeira o template caia no caminho normal e desenhava dois
            # cabecalhos e dois totais zerados, sem dizer que nao ha' nada.
            'vazio': not any(
                b['linhas'] or b['sem_categoria']['tem'] for b in linhas
            ),
        }

    @classmethod
    def _comparativo_mensal(cls, filial, competencia, regime, arvore, quantidade=6):
        """DRE horizontal por grupo para os últimos meses."""
        referencias = [competencia]
        for _ in range(max(1, quantidade) - 1):
            referencias.append(mes_anterior(referencias[-1]))
        referencias.reverse()
        periodos = [
            cls._periodo(
                filial, referencia, proximo_mes(referencia), regime, arvore,
            )
            for referencia in referencias
        ]
        blocos = []
        for chave, rotulo in (('receita', 'Receitas'), ('despesa', 'Despesas')):
            grupos_ids = set().union(*(periodo[chave]['grupos'] for periodo in periodos))
            linhas = []
            for grupo_id in grupos_ids:
                referencia = next(
                    periodo[chave]['grupos'][grupo_id]
                    for periodo in periodos if grupo_id in periodo[chave]['grupos']
                )
                linhas.append({
                    'codigo': referencia['codigo'], 'descricao': referencia['descricao'],
                    'valores': [
                        periodo[chave]['grupos'].get(grupo_id, {}).get('total', ZERO)
                        for periodo in periodos
                    ],
                })
            linhas.sort(key=lambda linha: (linha['codigo'] or '', linha['descricao']))
            sem_categoria = [periodo[chave]['sem_categoria'] for periodo in periodos]
            blocos.append({
                'chave': chave, 'rotulo': rotulo, 'linhas': linhas,
                'sem_categoria': sem_categoria,
                'tem_sem_categoria': any(sem_categoria),
                'totais': [periodo[chave]['total'] for periodo in periodos],
            })
        receitas = [periodo['receita']['total'] for periodo in periodos]
        despesas = [periodo['despesa']['total'] for periodo in periodos]
        resultados = [receita - despesa for receita, despesa in zip(receitas, despesas)]
        return {
            'colunas': referencias, 'blocos': blocos, 'resultados': resultados,
            'margens': [_pct(resultado, receita) for resultado, receita in zip(resultados, receitas)],
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
        caminhos = {}
        for pk, conta in contas.items():
            atual = conta
            visitados = set()
            caminho = [conta]
            # O `visitados` não é zelo excessivo: `conta_pai` aponta para a
            # própria tabela, e um ciclo no cadastro (A filha de B, B filha
            # de A) travaria a tela inteira num laço infinito.
            while atual.conta_pai_id and atual.conta_pai_id in contas:
                if atual.pk in visitados:
                    break
                visitados.add(atual.pk)
                atual = contas[atual.conta_pai_id]
                caminho.append(atual)
            raizes[pk] = atual
            caminhos[pk] = list(reversed(caminho))
        return {'contas': contas, 'raizes': raizes, 'caminhos': caminhos}

    # ── Um período ───────────────────────────────────────────────────────

    @classmethod
    def _periodo(cls, filial, inicio, fim, regime, arvore) -> dict:
        if regime == CAIXA:
            # Caixa nasce das BAIXAS, não do resumo acumulado do título. Assim
            # uma conta recebida em maio e junho aparece parcialmente em cada
            # mês, em vez de ter todo o valor jogado na data da última baixa.
            receitas = cls._somar_pagamentos(
                PagamentoContaReceber.objects.filter(
                    filial=filial,
                    conta_receber__excluido_em__isnull=True,
                ).exclude(conta_receber__status__in=RECEBER_MORTOS),
                inicio, fim, 'conta_receber__plano_contas_id',
            )
            despesas = cls._somar_pagamentos(
                PagamentoContaPagar.objects.filter(
                    filial=filial,
                    conta_pagar__excluido_em__isnull=True,
                ).exclude(conta_pagar__status__in=PAGAR_MORTOS),
                inicio, fim, 'conta_pagar__plano_contas_id',
            )
        elif regime == COMPETENCIA:
            receitas = cls._somar_titulos(
                ContaReceber.objects.for_filial(filial).exclude(status__in=RECEBER_MORTOS),
                inicio, fim, campo_data='competencia', fallback_vencimento=True,
            )
            despesas = cls._somar_titulos(
                # `excluido_em` é exclusão lógica: o título apagado continua
                # na tabela, e somá-lo cobraria do resultado uma despesa que
                # alguém já disse que não existe.
                ContaPagar.objects.for_filial(filial)
                .exclude(status__in=PAGAR_MORTOS).filter(excluido_em__isnull=True),
                inicio, fim, campo_data='data_competencia', fallback_vencimento=True,
            )
        else:
            receitas = cls._somar_titulos(
                ContaReceber.objects.for_filial(filial).exclude(status__in=RECEBER_MORTOS),
                inicio, fim, campo_data='data_vencimento',
            )
            despesas = cls._somar_titulos(
                ContaPagar.objects.for_filial(filial)
                .exclude(status__in=PAGAR_MORTOS).filter(excluido_em__isnull=True),
                inicio, fim, campo_data='data_vencimento',
            )

        # A Posição Diária também permite entradas e saídas manuais. Elas
        # vivem no extrato bancário (não geram um título) e precisam entrar
        # na DRE; transferências são ignoradas porque apenas movem dinheiro
        # entre contas da própria empresa. Sem competência própria, o
        # movimento manual permanece na sua data de lançamento em todos os
        # critérios.
        extratos = ExtratoBancario.objects.filter(
            filial=filial,
            origem='manual',
        ).exclude(status='excluido').exclude(tipo_lancamento='transferencia')
        receitas = cls._combinar_somas(
            receitas,
            cls._somar_extratos(extratos, inicio, fim, RECEITA),
        )
        despesas = cls._combinar_somas(
            despesas,
            cls._somar_extratos(extratos, inicio, fim, DESPESA),
        )

        return {
            'receita': cls._agrupar(receitas, arvore, RECEITA),
            'despesa': cls._agrupar(despesas, arvore, DESPESA),
        }

    @staticmethod
    def _somar_pagamentos(consulta, inicio, fim, campo_categoria):
        """
        Soma cada baixa no dia em que ela ocorreu.

        O vínculo da categoria vive no título pai, mas o valor e a data que
        pertencem ao caixa vivem no pagamento individual.
        """
        return list(
            consulta.filter(
                data_pagamento__gte=inicio, data_pagamento__lt=fim,
            ).values(
                plano_contas_id=F(campo_categoria),
            ).annotate(
                total=Sum('valor_pago'), quantidade=Count('id'),
            )
        )

    @staticmethod
    def _somar_titulos(
        consulta, inicio, fim, campo_data, fallback_vencimento=False,
    ):
        """Soma os títulos pela data escolhida para a leitura da DRE."""
        # Competência em branco cai no vencimento: é o que a casa usa como
        # aproximação, e deixar o título de fora seria pior -- sumiria
        # dinheiro do relatório por falta de cadastro.
        if fallback_vencimento:
            consulta = consulta.annotate(
                _quando=Coalesce(campo_data, 'data_vencimento'),
            )
            campo_filtro = '_quando'
        else:
            campo_filtro = campo_data
        consulta = consulta.filter(**{
            f'{campo_filtro}__gte': inicio,
            f'{campo_filtro}__lt': fim,
        })
        return list(
            consulta.values('plano_contas_id').annotate(
                total=Sum('valor_final'), quantidade=Count('id'),
            ),
        )

    @staticmethod
    def _somar_extratos(consulta, inicio, fim, tipo):
        """Soma lançamentos manuais classificados da Posição Diária."""
        consulta = consulta.annotate(
            _quando=Coalesce('data_credito', 'data_lancamento'),
        ).filter(_quando__gte=inicio, _quando__lt=fim)
        if tipo == RECEITA:
            consulta = consulta.filter(valor__gt=0)
        else:
            consulta = consulta.filter(valor__lt=0)

        somas = list(
            consulta.values('plano_contas_id').annotate(
                total=Sum('valor'), quantidade=Count('id'),
            ),
        )
        if tipo == DESPESA:
            for soma in somas:
                soma['total'] = abs(soma['total'] or ZERO)
        return somas

    @staticmethod
    def _combinar_somas(*listas):
        """Combina origens financeiras sem perder categoria ou quantidade."""
        combinadas = {}
        for lista in listas:
            for linha in lista:
                categoria_id = linha['plano_contas_id']
                acumulada = combinadas.setdefault(categoria_id, {
                    'plano_contas_id': categoria_id,
                    'total': ZERO,
                    'quantidade': 0,
                })
                acumulada['total'] += linha.get('total') or ZERO
                acumulada['quantidade'] += linha.get('quantidade') or 0
        return list(combinadas.values())

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
        sem_categoria_qtd = 0

        for linha in somas:
            total = linha['total'] or ZERO
            quantidade = linha.get('quantidade') or 0
            conta = arvore['contas'].get(linha['plano_contas_id'])
            if conta is None:
                sem_categoria += total
                sem_categoria_qtd += quantidade
                continue
            raiz = arvore['raizes'][conta.pk]
            grupo = grupos.setdefault(raiz.pk, {
                'id': raiz.pk,
                'codigo': raiz.codigo,
                'descricao': raiz.descricao,
                'tipo': raiz.tipo or tipo,
                'total': ZERO,
                'quantidade': 0,
                'filhas': {},
            })
            grupo['total'] += total
            grupo['quantidade'] += quantidade
            if conta.pk != raiz.pk:
                caminho = arvore['caminhos'].get(conta.pk, [conta])
                # Cada nível recebe o valor dos seus descendentes. Assim a
                # tela consegue abrir Grupo > Subcategoria > Categoria sem
                # perder a reconciliação com o total da raiz.
                for nivel, item in enumerate(caminho[1:], start=1):
                    filha = grupo['filhas'].setdefault(item.pk, {
                        'id': item.pk,
                        'parent_id': caminho[nivel - 1].pk,
                        'nivel': nivel,
                        'codigo': item.codigo,
                        'descricao': item.descricao,
                        'caminho': ' > '.join(
                            parte.descricao for parte in caminho[1:nivel + 1]
                        ),
                        'total': ZERO,
                        'quantidade': 0,
                    })
                    filha['total'] += total
                    filha['quantidade'] += quantidade

        return {
            'grupos': grupos,
            'sem_categoria': sem_categoria,
            'sem_categoria_qtd': sem_categoria_qtd,
            'total': sum((g['total'] for g in grupos.values()), ZERO) + sem_categoria,
            'quantidade': sum((g['quantidade'] for g in grupos.values()), 0) + sem_categoria_qtd,
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
                linhas.append(cls._linha(
                    referencia, atual, velho, acumulado, mes[chave]['total'],
                ))
            linhas.sort(key=lambda l: (l['codigo'] or '', l['descricao']))

            sem = (
                mes[chave]['sem_categoria'], anterior[chave]['sem_categoria'],
                ano[chave]['sem_categoria'],
            )
            blocos.append({
                'chave': chave,
                'rotulo': f'{rotulo} por categoria',
                'linhas': linhas,
                'sem_categoria': {
                    'mes': sem[0], 'anterior': sem[1], 'ano': sem[2],
                    'tem': any(v for v in sem),
                    'quantidade': mes[chave]['sem_categoria_qtd'],
                    'participacao_pct': _pct(sem[0], mes[chave]['total']),
                },
                'total_mes': mes[chave]['total'],
                'total_anterior': anterior[chave]['total'],
                'total_ano': ano[chave]['total'],
                'quantidade_mes': mes[chave]['quantidade'],
            })
        return blocos

    @staticmethod
    def _linha(referencia, atual, velho, acumulado, total_bloco) -> dict:
        valor = (atual or {}).get('total', ZERO)
        antes = (velho or {}).get('total', ZERO)
        filhas = sorted(
            (atual or {}).get('filhas', {}).values(),
            key=lambda f: (f['codigo'] or '', f['descricao']),
        )
        pais = {filha['parent_id'] for filha in filhas}
        for filha in filhas:
            filha['participacao_pct'] = _pct(filha['total'], total_bloco)
            filha['tem_filhos'] = filha['id'] in pais
        return {
            'id': referencia['id'],
            'codigo': referencia['codigo'],
            'descricao': referencia['descricao'],
            'mes': valor,
            'anterior': antes,
            'ano': (acumulado or {}).get('total', ZERO),
            'quantidade': (atual or {}).get('quantidade', 0),
            'participacao_pct': _pct(valor, total_bloco),
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
            'movimentos_receita': mes['receita']['quantidade'],
            'movimentos_despesa': mes['despesa']['quantidade'],
            'movimentos': mes['receita']['quantidade'] + mes['despesa']['quantidade'],
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

    @staticmethod
    def _grafico_despesas(despesas) -> dict:
        """Fatias do gráfico por grupo raiz, reconciliadas com o total."""
        itens = [
            {
                'codigo': grupo['codigo'],
                'descricao': grupo['descricao'],
                'total': grupo['total'],
            }
            for grupo in despesas['grupos'].values()
            if grupo['total'] > 0
        ]
        if despesas['sem_categoria'] > 0:
            itens.append({
                'codigo': '',
                'descricao': 'Sem categoria',
                'total': despesas['sem_categoria'],
            })
        itens.sort(key=lambda item: item['total'], reverse=True)

        total = sum((item['total'] for item in itens), ZERO)
        inicio = ZERO
        partes = []
        for indice, item in enumerate(itens):
            cor = CORES_GRAFICO[indice % len(CORES_GRAFICO)]
            fim = (inicio + (item['total'] / total * CEM)) if total else inicio
            item['cor'] = cor
            item['participacao_pct'] = _pct(item['total'], total)
            partes.append(f'{cor} {inicio:.2f}% {fim:.2f}%')
            inicio = fim

        return {
            'itens': itens,
            'total': total,
            'tem_dados': bool(itens),
            'gradiente': ', '.join(partes) if partes else '#e2e8f0 0% 100%',
        }
