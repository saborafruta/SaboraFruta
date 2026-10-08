import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .gateway import EvolutionClient, GatewayWhatsAppError
from .models import ConfiguracaoWhatsApp, ConversaWhatsApp, MensagemWhatsApp


logger = logging.getLogger(__name__)


def _dentro_do_horario(configuracao, agora):
    hora = timezone.localtime(agora).time().replace(tzinfo=None)
    inicio = configuracao.recuperacao_horario_inicio
    fim = configuracao.recuperacao_horario_fim
    if inicio <= fim:
        return inicio <= hora <= fim
    return hora >= inicio or hora <= fim


def _texto_recuperacao(configuracao, conversa):
    contexto = conversa.contexto or {}
    texto = configuracao.mensagem_recuperacao_agendamento or ''
    valores = {
        'nome': conversa.nome_contato or (
            conversa.cliente.nome_display if conversa.cliente_id else 'tudo bem'
        ),
        'link': contexto.get('link_agendamento', ''),
    }
    for chave, valor in valores.items():
        texto = texto.replace(f'{{{chave}}}', str(valor))
    return texto.strip()


def recuperar_agendamentos_abandonados(*, configuracao=None, agora=None):
    """Envia uma única tentativa de ajuda para agendamentos iniciados e abandonados."""
    agora = agora or timezone.now()
    configuracoes = ConfiguracaoWhatsApp.objects.using('default').filter(
        ativo=True,
        agente_ativo=True,
        recuperacao_agendamento_ativa=True,
        status=ConfiguracaoWhatsApp.Status.CONECTADO,
    )
    if configuracao is not None:
        configuracoes = configuracoes.filter(pk=configuracao.pk)

    enviados = 0
    for config in configuracoes.iterator():
        if not _dentro_do_horario(config, agora):
            continue
        limite = agora - timedelta(minutes=config.recuperacao_atraso_minutos)
        ids = list(
            ConversaWhatsApp.objects.using('default')
            .filter(
                configuracao_id=config.pk,
                etapa_crm=ConversaWhatsApp.EtapaCRM.AGENDAMENTO_INICIADO,
                agendamento_iniciado_em__lte=limite,
                acompanhamento_enviado_em__isnull=True,
                ativa=True,
                atendimento_humano=False,
            )
            .values_list('pk', flat=True)[:100]
        )
        for conversa_id in ids:
            with transaction.atomic(using='default'):
                conversa = (
                    ConversaWhatsApp.objects.using('default')
                    .select_for_update()
                    .select_related('cliente')
                    .filter(
                        pk=conversa_id,
                        etapa_crm=ConversaWhatsApp.EtapaCRM.AGENDAMENTO_INICIADO,
                        agendamento_iniciado_em__lte=limite,
                        acompanhamento_enviado_em__isnull=True,
                        ativa=True,
                        atendimento_humano=False,
                    )
                    .first()
                )
                if not conversa:
                    continue
                texto = _texto_recuperacao(config, conversa)
                if not texto:
                    conversa.acompanhamento_enviado_em = agora
                    conversa.save(using='default', update_fields=[
                        'acompanhamento_enviado_em', 'updated_at',
                    ])
                    continue
                saida = MensagemWhatsApp.objects.using('default').create(
                    conversa_id=conversa.pk,
                    direcao=MensagemWhatsApp.Direcao.SAIDA,
                    texto=texto,
                    tipo='recuperacao_agendamento',
                    status='pendente',
                )
                try:
                    retorno = EvolutionClient(config).enviar_texto(conversa.telefone, texto)
                    saida.identificador_externo = str((retorno.get('key') or {}).get('id') or '')
                    saida.status = 'enviada'
                    enviados += 1
                except GatewayWhatsAppError as exc:
                    saida.status = 'erro'
                    saida.dados_evento = {'erro': str(exc)}
                    logger.warning('Falha ao recuperar agendamento no WhatsApp: %s', exc)
                saida.save(using='default', update_fields=[
                    'identificador_externo', 'status', 'dados_evento', 'updated_at',
                ])
                conversa.etapa_crm = ConversaWhatsApp.EtapaCRM.AGUARDANDO_CLIENTE
                conversa.acompanhamento_enviado_em = agora
                conversa.ultima_mensagem_em = agora
                conversa.save(using='default', update_fields=[
                    'etapa_crm', 'acompanhamento_enviado_em',
                    'ultima_mensagem_em', 'updated_at',
                ])
    return enviados
