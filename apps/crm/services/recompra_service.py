"""
Detecção do padrão de recompra dos clientes.

Lê o histórico de compras concluídas (PedidoVenda B2B + VendaPDV),
calcula o intervalo médio entre compras de cada cliente, prevê a próxima
compra e pontua a urgência do contato comercial. O resultado é gravado
em `RecompraCliente`, que é o que as telas consultam.

Sobre performance: o histórico vem de duas tabelas diferentes, então em
vez de window functions o serviço faz duas queries planas (só cliente_id
+ data + valor) e agrupa em memória. Isso roda apenas no recálculo —
nunca por request — e a velocidade das telas vem da tabela de cache
indexada. Para uma base de milhares de clientes com um ano de histórico
são dezenas de milhares de tuplas pequenas, o que é barato.
"""
from __future__ import annotations

import logging
import statistics
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.crm import constants as c
from apps.crm.models import RecompraCliente, RecompraControle

logger = logging.getLogger(__name__)


class RecompraService:
    """Recalcula o padrão de recompra. Todos os métodos são idempotentes."""

    # ------------------------------------------------------------ coleta
    @staticmethod
    def _filiais_do_escopo(filial):
        """
        Matriz enxerga a empresa inteira; filial comum enxerga só a si.
        Mesmo critério usado nos blocos de RFM/Curva ABC do dashboard.
        """
        from apps.core.models import Filial

        if filial.is_matriz:
            return Filial.objects.filter(empresa=filial.empresa)
        return Filial.objects.filter(pk=filial.pk)

    @classmethod
    def _coletar_historico(cls, filial, cliente_ids=None):
        """
        Devolve {(cliente_id, filial_id): {'compras': [...], 'representante_id': ...}}.

        O agrupamento inclui a filial de origem da compra — e não apenas o
        cliente — porque o padrão de recompra é por onde ele compra: um
        cliente atendido por duas filiais tem um ritmo em cada uma, e é
        isso que o vendedor daquela filial precisa ver. Também é o que faz
        o filtro por filial na tela ter sentido e evita que a matriz veja
        o mesmo cliente duplicado.

        Só entram vendas efetivamente concluídas: pedidos confirmados em
        diante e vendas de PDV finalizadas. Orçamentos, rascunhos,
        cancelados e devolvidos ficam de fora por construção.
        """
        from apps.pdv.models import VendaPDV
        from apps.vendas.models import PedidoVenda

        filiais = cls._filiais_do_escopo(filial)
        inicio = timezone.localdate() - timedelta(days=c.JANELA_HISTORICO_DIAS)

        status_validos = [
            PedidoVenda.Status.CONFIRMADO,
            PedidoVenda.Status.EM_SEPARACAO,
            PedidoVenda.Status.FATURADO,
            PedidoVenda.Status.PARCIALMENTE_FATURADO,
            PedidoVenda.Status.ENTREGUE,
        ]

        pedidos = (
            PedidoVenda.objects
            .filter(
                filial__in=filiais,
                status__in=status_validos,
                cliente_id__isnull=False,
                data_emissao__date__gte=inicio,
            )
            .order_by('cliente_id', 'data_emissao')
        )
        vendas_pdv = (
            VendaPDV.objects
            .filter(
                filial__in=filiais,
                status='finalizada',
                cliente_id__isnull=False,
                data_venda__date__gte=inicio,
            )
            .order_by('cliente_id', 'data_venda')
        )
        if cliente_ids:
            pedidos = pedidos.filter(cliente_id__in=cliente_ids)
            vendas_pdv = vendas_pdv.filter(cliente_id__in=cliente_ids)

        historico: dict[tuple[int, int], dict] = {}

        for cliente_id, filial_id, data, valor, representante_id in pedidos.values_list(
            'cliente_id', 'filial_id', 'data_emissao', 'valor_total', 'representante_id',
        ):
            reg = historico.setdefault(
                (cliente_id, filial_id), {'compras': [], 'representante_id': None},
            )
            reg['compras'].append((timezone.localtime(data).date(), valor or Decimal('0')))
            # As queries vêm ordenadas por data crescente, então o último
            # representante visto é o do pedido mais recente.
            if representante_id:
                reg['representante_id'] = representante_id

        for cliente_id, filial_id, data, valor in vendas_pdv.values_list(
            'cliente_id', 'filial_id', 'data_venda', 'valor_total',
        ):
            reg = historico.setdefault(
                (cliente_id, filial_id), {'compras': [], 'representante_id': None},
            )
            reg['compras'].append((timezone.localtime(data).date(), valor or Decimal('0')))

        return historico

    # --------------------------------------------------------- cálculo
    @staticmethod
    def _consolidar_por_dia(compras):
        """
        Duas compras no mesmo dia são um evento só de compra (senão o
        intervalo entre elas seria 0 e derrubaria a média artificialmente).
        Devolve lista de (data, valor_somado) ordenada por data.
        """
        por_dia: dict = {}
        for data, valor in compras:
            por_dia[data] = por_dia.get(data, Decimal('0')) + Decimal(valor or 0)
        return sorted(por_dia.items())

    @staticmethod
    def _classificar(media: float) -> str:
        F = RecompraCliente.Frequencia
        if c.FAIXA_SEMANAL[0] <= media <= c.FAIXA_SEMANAL[1]:
            return F.SEMANAL
        if c.FAIXA_QUINZENAL[0] <= media <= c.FAIXA_QUINZENAL[1]:
            return F.QUINZENAL
        if c.FAIXA_MENSAL[0] <= media <= c.FAIXA_MENSAL[1]:
            return F.MENSAL
        return F.PERSONALIZADA

    @staticmethod
    def _status_por_dias(dias_restantes: int) -> str:
        S = RecompraCliente.Status
        if dias_restantes < 0:
            return S.VERMELHO
        if dias_restantes <= c.DIAS_ALERTA_AMARELO:
            return S.AMARELO
        return S.VERDE

    @classmethod
    def _teto_valor(cls, filial, historico, parcial: bool) -> float:
        """
        Valor de referência para normalizar o peso de ticket médio no score:
        o 90º percentil da base, para que um cliente gigante não achate a
        nota de todos os outros.

        Num recálculo parcial (uma venda acabou de acontecer) o histórico
        tem só aquele cliente — usar o percentil dele daria sempre nota
        cheia e inflaria o score. Nesse caso o teto vem dos valores já
        gravados na tabela, mantendo a nota comparável com o resto da base.
        """
        if parcial:
            valores = list(
                RecompraCliente.objects
                .filter(filial__in=cls._filiais_do_escopo(filial), valor_medio__gt=0)
                .order_by('valor_medio')
                .values_list('valor_medio', flat=True)
            )
            medias = [float(v) for v in valores]
        else:
            medias = []
            for reg in historico.values():
                compras = cls._consolidar_por_dia(reg['compras'])
                if compras:
                    medias.append(float(sum(v for _, v in compras)) / len(compras))
            medias.sort()

        if not medias:
            return 1.0
        idx = min(int(len(medias) * 0.9), len(medias) - 1)
        return medias[idx] or 1.0

    @staticmethod
    def _calcular_score(*, dias_restantes, media, desvio, qtd_compras,
                        valor_medio, dias_relacionamento, teto_valor) -> int:
        """
        Prioridade de contato, 0-100. Premia quem está atrasado em relação
        ao próprio ritmo, compra alto, é previsível, compra sempre e é
        cliente antigo. Os pesos vivem em constants.py.
        """
        # Urgência: atraso medido em "quantas médias" já passaram. Um cliente
        # semanal 7 dias atrasado é mais urgente que um mensal 7 dias atrasado.
        if dias_restantes is not None and dias_restantes < 0 and media > 0:
            atraso_relativo = min(abs(dias_restantes) / media, c.TETO_ATRASO_RELATIVO)
            urgencia = atraso_relativo / c.TETO_ATRASO_RELATIVO
        elif dias_restantes is not None and dias_restantes <= c.DIAS_ALERTA_AMARELO:
            # Ainda não venceu, mas está na janela de alerta: meia urgência,
            # decrescente conforme sobram dias.
            urgencia = 0.5 * (1 - dias_restantes / max(c.DIAS_ALERTA_AMARELO, 1))
        else:
            urgencia = 0.0

        valor = min(float(valor_medio) / teto_valor, 1.0) if teto_valor > 0 else 0.0

        # Regularidade: coeficiente de variação baixo = cliente previsível.
        regularidade = max(0.0, 1 - (desvio / media)) if media > 0 else 0.0

        recorrencia = min(qtd_compras / c.TETO_COMPRAS_SCORE, 1.0)
        relacionamento = min(dias_relacionamento / c.TETO_RELACIONAMENTO_DIAS, 1.0)

        score = (
            urgencia * c.PESO_URGENCIA
            + valor * c.PESO_VALOR
            + regularidade * c.PESO_REGULARIDADE
            + recorrencia * c.PESO_RECORRENCIA
            + relacionamento * c.PESO_RELACIONAMENTO
        )
        return max(0, min(100, round(score)))

    # -------------------------------------------------------- recálculo
    @classmethod
    @transaction.atomic
    def recalcular(cls, filial, cliente_ids=None) -> int:
        """
        Recalcula o padrão de recompra no escopo de `filial` — a empresa
        inteira se ela for matriz, senão só ela mesma. Grava uma linha por
        (cliente, filial onde ele compra). Com `cliente_ids`, limita-se a
        esses clientes. Devolve quantas linhas foram gravadas.
        """
        if filial is None:
            return 0

        historico = cls._coletar_historico(filial, cliente_ids=cliente_ids)
        if not historico:
            return 0

        hoje = timezone.localdate()
        teto_valor = cls._teto_valor(filial, historico, parcial=bool(cliente_ids))

        registros = []
        for (cliente_id, filial_id), reg in historico.items():
            compras = cls._consolidar_por_dia(reg['compras'])[-c.MAX_COMPRAS_CONSIDERADAS:]
            if not compras:
                continue

            datas = [d for d, _ in compras]
            valores = [Decimal(v) for _, v in compras]
            qtd = len(compras)
            valor_total = sum(valores, Decimal('0'))
            valor_medio = (valor_total / qtd).quantize(Decimal('0.01'))

            base = dict(
                cliente_id=cliente_id,
                filial_id=filial_id,
                representante_id=reg['representante_id'],
                qtd_compras=qtd,
                primeira_compra=datas[0],
                ultima_compra=datas[-1],
                valor_medio=valor_medio,
                valor_total_periodo=valor_total,
            )

            if qtd < c.MIN_COMPRAS_PARA_PADRAO:
                # Sem intervalos suficientes: registra o cliente mesmo assim
                # (a tela mostra "Padrão insuficiente"), mas sem previsão.
                registros.append(RecompraCliente(
                    **base,
                    media_intervalo_dias=Decimal('0'),
                    desvio_padrao_dias=Decimal('0'),
                    frequencia=RecompraCliente.Frequencia.SEM_PADRAO,
                    proxima_compra_prevista=None,
                    dias_restantes=None,
                    status=RecompraCliente.Status.CINZA,
                    score=0,
                    nivel_confianca=Decimal('0'),
                ))
                continue

            intervalos = [(datas[i] - datas[i - 1]).days for i in range(1, qtd)]
            media = statistics.fmean(intervalos)
            desvio = statistics.pstdev(intervalos) if len(intervalos) > 1 else 0.0

            prevista = datas[-1] + timedelta(days=round(media))
            dias_restantes = (prevista - hoje).days
            # Confiança = regularidade: quanto menor a variação dos
            # intervalos, mais confiável é a previsão.
            confianca = max(0.0, min(1.0, 1 - (desvio / media))) if media > 0 else 0.0

            registros.append(RecompraCliente(
                **base,
                media_intervalo_dias=Decimal(str(round(media, 2))),
                desvio_padrao_dias=Decimal(str(round(desvio, 2))),
                frequencia=cls._classificar(media),
                proxima_compra_prevista=prevista,
                dias_restantes=dias_restantes,
                status=cls._status_por_dias(dias_restantes),
                score=cls._calcular_score(
                    dias_restantes=dias_restantes,
                    media=media,
                    desvio=desvio,
                    qtd_compras=qtd,
                    valor_medio=valor_medio,
                    dias_relacionamento=(hoje - datas[0]).days,
                    teto_valor=teto_valor,
                ),
                nivel_confianca=Decimal(str(round(confianca, 3))),
            ))

        if not registros:
            return 0

        RecompraCliente.objects.bulk_create(
            registros,
            update_conflicts=True,
            unique_fields=['cliente', 'filial'],
            update_fields=[
                'representante', 'media_intervalo_dias', 'desvio_padrao_dias',
                'qtd_compras', 'frequencia', 'primeira_compra', 'ultima_compra',
                'proxima_compra_prevista', 'dias_restantes', 'status',
                'valor_medio', 'valor_total_periodo', 'score', 'nivel_confianca',
                'ultima_atualizacao', 'updated_at',
            ],
            batch_size=500,
        )
        return len(registros)

    @classmethod
    def recalcular_se_obsoleto(cls, filial, horas: int = c.HORAS_ATE_OBSOLETO) -> bool:
        """
        Recalcula em lote se o último recálculo da empresa passou de `horas`.

        Usa a linha de controle como lock (`select_for_update(skip_locked=True)`)
        para que os dois workers do gunicorn não recalculem a mesma empresa
        simultaneamente — quem não pegar o lock simplesmente segue com os
        dados atuais, que no pior caso estão algumas horas defasados.
        Devolve True se recalculou.
        """
        if filial is None:
            return False

        limite = timezone.now() - timedelta(hours=horas)
        try:
            with transaction.atomic():
                controle = (
                    RecompraControle.objects
                    .select_for_update(skip_locked=True)
                    .filter(empresa_id=filial.empresa_id)
                    .first()
                )
                if controle is None:
                    controle, criado = RecompraControle.objects.get_or_create(
                        empresa_id=filial.empresa_id,
                    )
                    if not criado:
                        # Outro worker acabou de criar e está com o lock.
                        return False
                elif controle.ultima_execucao and controle.ultima_execucao > limite:
                    return False

                cls.recalcular(filial)
                controle.ultima_execucao = timezone.now()
                controle.save(update_fields=['ultima_execucao', 'updated_at'])
                return True
        except Exception:
            # Dados levemente defasados são muito melhores que uma tela que
            # não abre — a falha fica no log para investigação.
            logger.exception('Falha no recálculo de recompra da filial #%s', filial.pk)
            return False

    @classmethod
    def recalcular_cliente_da_venda(cls, filial, cliente_id) -> None:
        """
        Atualização incremental após uma venda. Nunca propaga exceção: uma
        falha aqui não pode derrubar o faturamento nem o fechamento do PDV.
        """
        if not filial or not cliente_id:
            return
        try:
            cls.recalcular(filial, cliente_ids=[cliente_id])
        except Exception:
            logger.exception('Falha ao atualizar recompra do cliente #%s', cliente_id)
