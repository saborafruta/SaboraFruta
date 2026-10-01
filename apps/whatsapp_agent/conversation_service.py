from datetime import timedelta

from django.utils import timezone

from .models import ConfiguracaoWhatsApp, ConversaWhatsApp


def encerrar_conversa(conversa):
    conversa.etapa = 'encerrada'
    conversa.contexto = {}
    conversa.atendimento_humano = False
    conversa.ativa = False
    conversa.save(update_fields=[
        'etapa', 'contexto', 'atendimento_humano', 'ativa', 'updated_at',
    ])
    return conversa


def conversa_expirada(conversa, agora=None):
    configuracao = conversa.configuracao
    if (
        not conversa.ativa
        or not configuracao.encerramento_automatico_ativo
        or not conversa.ultima_mensagem_em
    ):
        return False
    agora = agora or timezone.now()
    limite = agora - timedelta(minutes=configuracao.tempo_inatividade_minutos)
    return conversa.ultima_mensagem_em < limite


def encerrar_se_expirada(conversa, agora=None):
    if not conversa_expirada(conversa, agora=agora):
        return False
    encerrar_conversa(conversa)
    return True


def encerrar_conversas_inativas(*, filial=None, agora=None):
    agora = agora or timezone.now()
    configuracoes = ConfiguracaoWhatsApp.objects.filter(
        ativo=True,
        encerramento_automatico_ativo=True,
    )
    if filial is not None:
        configuracoes = configuracoes.filter(filial=filial)

    total = 0
    for configuracao in configuracoes.iterator():
        limite = agora - timedelta(minutes=configuracao.tempo_inatividade_minutos)
        total += ConversaWhatsApp.objects.filter(
            configuracao=configuracao,
            ativa=True,
            ultima_mensagem_em__lt=limite,
        ).update(
            etapa='encerrada',
            contexto={},
            atendimento_humano=False,
            ativa=False,
            updated_at=agora,
        )
    return total
