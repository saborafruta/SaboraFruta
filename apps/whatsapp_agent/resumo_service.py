import datetime
import hashlib
import logging
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.utils import timezone

from apps.core.models import ParametrosSistema
from apps.core.services.tenant_task_service import TenantTaskService
from apps.core.tenant_context import get_current_database_alias

from .gateway import EvolutionClient, GatewayWhatsAppError
from .models import ConfiguracaoWhatsAppCentral, EnvioResumoWhatsApp


logger = logging.getLogger(__name__)
ZERO = Decimal('0')


def _moeda(valor):
    valor = Decimal(str(valor or 0)).quantize(Decimal('0.01'))
    texto = f'{valor:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
    return f'R$ {texto}'


def _metricas_filial(filial, data_referencia, incluir_agenda=False):
    from apps.estoque.models import AlertaVencimento, Estoque
    from apps.financeiro.constants.enums import StatusContaReceber
    from apps.financeiro.models import ContaReceber, PagamentoContaReceber
    from apps.pdv.models import PagamentoVendaPDV, VendaPDV
    from apps.vendas.models import PedidoVenda

    status_pedidos = [
        PedidoVenda.Status.CONFIRMADO,
        PedidoVenda.Status.EM_SEPARACAO,
        PedidoVenda.Status.FATURADO,
        PedidoVenda.Status.PARCIALMENTE_FATURADO,
        PedidoVenda.Status.ENTREGUE,
    ]
    pedidos = PedidoVenda.objects.filter(
        filial=filial,
        status__in=status_pedidos,
        data_emissao__date=data_referencia,
    )
    vendas_pdv = VendaPDV.objects.filter(
        filial=filial,
        status='finalizada',
        data_venda__date=data_referencia,
    )

    pedidos_agg = pedidos.aggregate(
        total=Sum('valor_total'), quantidade=Count('id'), descontos=Sum('valor_desconto'),
    )
    pdv_agg = vendas_pdv.aggregate(
        total=Sum('valor_total'), quantidade=Count('id'), descontos=Sum('valor_desconto'),
    )
    total_vendas = (pedidos_agg['total'] or ZERO) + (pdv_agg['total'] or ZERO)
    quantidade_vendas = (pedidos_agg['quantidade'] or 0) + (pdv_agg['quantidade'] or 0)
    descontos = (pedidos_agg['descontos'] or ZERO) + (pdv_agg['descontos'] or ZERO)

    recebimento_pdv = PagamentoVendaPDV.objects.filter(
        venda_pdv__filial=filial,
        venda_pdv__data_venda__date=data_referencia,
        venda_pdv__status='finalizada',
        forma_pagamento__movimenta_caixa=True,
        status='aprovado',
    ).aggregate(valor=Sum('valor'), troco=Sum('troco'))
    recebimento_contas = PagamentoContaReceber.objects.filter(
        filial=filial,
        data_pagamento=data_referencia,
    ).aggregate(total=Sum('valor_pago'))['total'] or ZERO
    recebido = (
        (recebimento_pdv['valor'] or ZERO)
        - (recebimento_pdv['troco'] or ZERO)
        + recebimento_contas
    )

    cancelamentos = (
        VendaPDV.objects.filter(filial=filial, cancelado_em__date=data_referencia).count()
        + PedidoVenda.objects.filter(
            filial=filial,
            status=PedidoVenda.Status.CANCELADO,
            updated_at__date=data_referencia,
        ).count()
    )
    estoque_critico = (
        Estoque.objects.filter(
            filial=filial,
            produto__estoque_minimo__gt=0,
            quantidade_disponivel__lt=F('produto__estoque_minimo'),
        )
        .values('produto_id')
        .distinct()
        .count()
    )
    vencimentos = (
        AlertaVencimento.objects.filter(filial=filial, resolvido=False)
        .values('produto_id')
        .distinct()
        .count()
    )
    hoje = timezone.localdate()
    contas_vencidas_qs = ContaReceber.objects.filter(
        filial=filial,
        status__in=[StatusContaReceber.ABERTO, StatusContaReceber.VENCIDO],
        data_vencimento__lt=hoje,
    )
    contas_vencidas = contas_vencidas_qs.aggregate(
        quantidade=Count('id'), total=Sum('valor_saldo'),
    )

    data_anterior = data_referencia - datetime.timedelta(days=1)
    anterior_pedidos = pedidos.model.objects.filter(
        filial=filial,
        status__in=status_pedidos,
        data_emissao__date=data_anterior,
    ).aggregate(total=Sum('valor_total'))['total'] or ZERO
    anterior_pdv = VendaPDV.objects.filter(
        filial=filial,
        status='finalizada',
        data_venda__date=data_anterior,
    ).aggregate(total=Sum('valor_total'))['total'] or ZERO
    total_anterior = anterior_pedidos + anterior_pdv
    if total_anterior:
        variacao = ((total_vendas - total_anterior) / total_anterior * Decimal('100')).quantize(
            Decimal('0.1'),
        )
        comparacao = f'{variacao:+}% em relação a {data_anterior:%d/%m}'.replace('.', ',')
    elif total_vendas:
        comparacao = 'houve vendas; o dia anterior ficou sem movimento'
    else:
        comparacao = 'sem vendas nos dois dias comparados'

    metricas = {
        'empresa': filial.empresa.nome_fantasia or filial.empresa.razao_social,
        'filial': filial.nome_fantasia or filial.razao_social,
        'data': data_referencia.strftime('%d/%m/%Y'),
        'vendas': _moeda(total_vendas),
        'recebido': _moeda(recebido),
        'quantidade_vendas': quantidade_vendas,
        'ticket_medio': _moeda(total_vendas / quantidade_vendas if quantidade_vendas else ZERO),
        'descontos': _moeda(descontos),
        'cancelamentos': cancelamentos,
        'estoque_critico': f'{estoque_critico} produto(s)',
        'vencimentos': f'{vencimentos} produto(s)',
        'contas_vencidas': (
            f"{contas_vencidas['quantidade'] or 0} conta(s) — "
            f"{_moeda(contas_vencidas['total'] or ZERO)}"
        ),
        'comparacao': comparacao,
        'agenda': '',
    }
    if incluir_agenda:
        from apps.agenda.models import Agendamento

        ontem = Agendamento.objects.filter(
            filial=filial,
            inicio__date=data_referencia,
        ).exclude(status=Agendamento.Status.CANCELADO).count()
        previstos = Agendamento.objects.filter(
            filial=filial,
            inicio__date=hoje,
        ).exclude(
            status__in=[Agendamento.Status.CANCELADO, Agendamento.Status.NAO_COMPARECEU],
        ).count()
        metricas['agenda'] = (
            f'\n\n📅 *Agenda:* {ontem} atendimento(s) em {data_referencia:%d/%m}; '
            f'{previstos} previsto(s) para hoje'
        )
    return metricas


class _Valores(dict):
    def __missing__(self, chave):
        return '{' + chave + '}'


def _montar_mensagem(configuracao, metricas):
    try:
        return configuracao.mensagem_resumo.format_map(_Valores(metricas)).strip()
    except (KeyError, ValueError):
        padrao = ConfiguracaoWhatsAppCentral._meta.get_field('mensagem_resumo').default
        return padrao.format_map(_Valores(metricas)).strip()


def _inicio_janela(configuracao, data):
    return timezone.make_aware(
        datetime.datetime.combine(data, configuracao.horario_inicio),
        timezone.get_current_timezone(),
    )


def diagnosticar_resumos_diarios(data_referencia=None):
    """Resume por que uma preparação manual não criou novos envios."""
    hoje = timezone.localdate()
    data_referencia = data_referencia or (hoje - datetime.timedelta(days=1))
    estado = {
        'filiais_ativas': 0,
        'destinatarios_ativos': 0,
        'filiais_com_destinatario': 0,
    }

    def _contar_banco():
        parametros = ParametrosSistema.objects.filter(
            resumo_whatsapp_ativo=True,
            filial__ativo=True,
            filial__empresa__ativo=True,
        ).annotate(
            destinatarios_ativos=Count(
                'destinatarios_resumo_whatsapp',
                filter=Q(
                    destinatarios_resumo_whatsapp__ativo=True,
                    destinatarios_resumo_whatsapp__telefone__gt='',
                ),
            ),
        )
        linhas = list(parametros.values_list('destinatarios_ativos', flat=True))
        estado['filiais_ativas'] += len(linhas)
        estado['destinatarios_ativos'] += sum(linhas)
        estado['filiais_com_destinatario'] += sum(1 for total in linhas if total)
        return 0

    TenantTaskService.executar_em_todos(_contar_banco)
    estado['fila'] = {
        item['status']: item['total']
        for item in (
            EnvioResumoWhatsApp.objects.using('default')
            .filter(data_referencia=data_referencia)
            .values('status')
            .annotate(total=Count('id'))
        )
    }
    estado['data_referencia'] = data_referencia
    return estado


def preparar_resumos_diarios(data_referencia=None, disparo_manual=False):
    """Cria a fila idempotente, sem fazer qualquer disparo em massa."""
    configuracao = ConfiguracaoWhatsAppCentral.carregar()
    if not configuracao.resumos_ativos and not disparo_manual:
        return 0
    hoje = timezone.localdate()
    data_referencia = data_referencia or (hoje - datetime.timedelta(days=1))
    existentes = EnvioResumoWhatsApp.objects.using('default').filter(
        configuracao=configuracao,
        data_referencia=data_referencia,
    ).count()
    estado = {'total': existentes, 'criados': 0, 'sequencia': 0}
    inicio = timezone.now() if disparo_manual else _inicio_janela(configuracao, hoje)
    intervalo = datetime.timedelta(minutes=configuracao.intervalo_entre_envios_minutos)

    def _preparar_banco():
        if estado['total'] >= configuracao.limite_diario:
            return 0
        alias = get_current_database_alias()
        criados = 0
        parametros_qs = (
            ParametrosSistema.objects.filter(
                resumo_whatsapp_ativo=True,
                filial__ativo=True,
                filial__empresa__ativo=True,
            )
            .select_related('filial__empresa')
            .prefetch_related('destinatarios_resumo_whatsapp')
            .order_by('filial__empresa_id', 'filial_id')
        )
        for parametros in parametros_qs:
            destinatarios = [
                item for item in parametros.destinatarios_resumo_whatsapp.all()
                if item.ativo and item.telefone
            ]
            if not destinatarios:
                continue
            metricas = _metricas_filial(
                parametros.filial,
                data_referencia,
                parametros.resumo_whatsapp_incluir_agenda,
            )
            mensagem = _montar_mensagem(configuracao, metricas)
            for destinatario in destinatarios:
                if estado['total'] >= configuracao.limite_diario:
                    return criados
                agendado = inicio + intervalo * estado['sequencia']
                semente = (
                    f'{data_referencia}:{alias}:{parametros.filial.cnpj}:{destinatario.telefone}'
                )
                jitter = (
                    0
                    if disparo_manual
                    else int(hashlib.sha256(semente.encode()).hexdigest()[:4], 16) % 46
                )
                agendado += datetime.timedelta(seconds=jitter)
                envio, criado = EnvioResumoWhatsApp.objects.using('default').get_or_create(
                    configuracao=configuracao,
                    tenant_alias=alias,
                    filial_cnpj=parametros.filial.cnpj,
                    telefone=destinatario.telefone,
                    data_referencia=data_referencia,
                    defaults={
                        'empresa_nome': metricas['empresa'],
                        'filial_nome': metricas['filial'],
                        'destinatario_nome': destinatario.nome,
                        'metricas': metricas,
                        'mensagem': mensagem,
                        'agendado_para': agendado,
                        'disparo_manual': disparo_manual,
                    },
                )
                if criado:
                    estado['total'] += 1
                    estado['criados'] += 1
                    estado['sequencia'] += 1
                    criados += 1
                elif disparo_manual and envio.status in {
                    EnvioResumoWhatsApp.Status.PENDENTE,
                    EnvioResumoWhatsApp.Status.ERRO,
                }:
                    envio.status = EnvioResumoWhatsApp.Status.PENDENTE
                    envio.agendado_para = agendado
                    envio.disparo_manual = True
                    envio.tentativas = 0
                    envio.ultimo_erro = ''
                    envio.save(using='default', update_fields=[
                        'status', 'agendado_para', 'disparo_manual', 'tentativas',
                        'ultimo_erro', 'updated_at',
                    ])
                    estado['criados'] += 1
                    estado['sequencia'] += 1
                    criados += 1
        return criados

    TenantTaskService.executar_em_todos(_preparar_banco)
    return estado['criados']


def processar_proximo_resumo():
    """Envia no máximo uma mensagem, respeitando janela, intervalo e retentativas."""
    agora = timezone.now()
    hora_local = timezone.localtime(agora).time().replace(tzinfo=None)
    with transaction.atomic(using='default'):
        configuracao = (
            ConfiguracaoWhatsAppCentral.objects.using('default')
            .select_for_update()
            .filter(pk=1)
            .first()
        )
        if not configuracao or configuracao.status != configuracao.Status.CONECTADO:
            return 0
        dentro_da_janela = configuracao.horario_inicio <= hora_local <= configuracao.horario_fim
        intervalo = datetime.timedelta(minutes=configuracao.intervalo_entre_envios_minutos)
        if configuracao.ultimo_envio_em and agora - configuracao.ultimo_envio_em < intervalo:
            return 0
        EnvioResumoWhatsApp.objects.using('default').filter(
            configuracao=configuracao,
            status=EnvioResumoWhatsApp.Status.ENVIANDO,
            updated_at__lt=agora - datetime.timedelta(minutes=15),
        ).update(status=EnvioResumoWhatsApp.Status.PENDENTE, ultimo_erro='Envio interrompido; reagendado.')
        candidatos = EnvioResumoWhatsApp.objects.using('default').filter(
            configuracao=configuracao,
            status=EnvioResumoWhatsApp.Status.PENDENTE,
            agendado_para__lte=agora,
            tentativas__lt=configuracao.max_tentativas,
        )
        if not (configuracao.resumos_ativos and dentro_da_janela):
            candidatos = candidatos.filter(Q(disparo_manual=True))
        envio = (
            candidatos
            .select_for_update()
            .order_by('agendado_para', 'id')
            .first()
        )
        if not envio:
            return 0
        envio.status = EnvioResumoWhatsApp.Status.ENVIANDO
        envio.tentativas += 1
        envio.ultimo_erro = ''
        envio.save(update_fields=['status', 'tentativas', 'ultimo_erro', 'updated_at'])
        configuracao.ultimo_envio_em = agora
        configuracao.save(update_fields=['ultimo_envio_em', 'updated_at'])

    try:
        retorno = EvolutionClient(configuracao).enviar_texto(envio.telefone, envio.mensagem)
    except GatewayWhatsAppError as exc:
        logger.warning('Falha no resumo diário do WhatsApp: %s', exc)
        envio.ultimo_erro = str(exc)
        envio.status = (
            EnvioResumoWhatsApp.Status.ERRO
            if envio.tentativas >= configuracao.max_tentativas
            else EnvioResumoWhatsApp.Status.PENDENTE
        )
        envio.agendado_para = agora + intervalo
        envio.save(update_fields=['status', 'agendado_para', 'ultimo_erro', 'updated_at'])
        return 0

    envio.status = EnvioResumoWhatsApp.Status.ENVIADO
    envio.enviado_em = timezone.now()
    envio.identificador_externo = str((retorno.get('key') or {}).get('id') or '')
    envio.save(update_fields=[
        'status', 'enviado_em', 'identificador_externo', 'ultimo_erro', 'updated_at',
    ])
    return 1
