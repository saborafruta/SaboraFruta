from django.core import signing
from django.utils import timezone

from .models import ConversaWhatsApp


TOKEN_SALT = 'ited.whatsapp.agendamento'
TOKEN_MAX_AGE = 60 * 60 * 24 * 30


def gerar_token_agendamento(conversa):
    return signing.dumps(
        {'conversa': conversa.pk, 'configuracao': conversa.configuracao_id},
        salt=TOKEN_SALT,
        compress=True,
    )


def conversa_do_token(token):
    if not token:
        return None
    try:
        dados = signing.loads(token, salt=TOKEN_SALT, max_age=TOKEN_MAX_AGE)
    except signing.BadSignature:
        return None
    return (
        ConversaWhatsApp.objects.using('default')
        .filter(
            pk=dados.get('conversa'),
            configuracao_id=dados.get('configuracao'),
        )
        .first()
    )


def marcar_agendamento_iniciado(token):
    conversa = conversa_do_token(token)
    if not conversa or conversa.etapa_crm in {
        ConversaWhatsApp.EtapaCRM.AGENDADO,
        ConversaWhatsApp.EtapaCRM.CONCLUIDO,
    }:
        return conversa
    agora = timezone.now()
    conversa.etapa_crm = ConversaWhatsApp.EtapaCRM.AGENDAMENTO_INICIADO
    conversa.agendamento_iniciado_em = agora
    conversa.acompanhamento_enviado_em = None
    conversa.save(using='default', update_fields=[
        'etapa_crm', 'agendamento_iniciado_em',
        'acompanhamento_enviado_em', 'updated_at',
    ])
    return conversa


def marcar_agendamento_confirmado(token):
    conversa = conversa_do_token(token)
    if not conversa:
        return None
    agora = timezone.now()
    conversa.etapa_crm = ConversaWhatsApp.EtapaCRM.AGENDADO
    conversa.agendamento_confirmado_em = agora
    conversa.ativa = True
    conversa.save(using='default', update_fields=[
        'etapa_crm', 'agendamento_confirmado_em', 'ativa', 'updated_at',
    ])
    return conversa
