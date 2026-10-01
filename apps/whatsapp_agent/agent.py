import re
import unicodedata

from django.conf import settings
from django.db.models import Q
from django.urls import reverse

from apps.agenda.models import AgendaLinkPublico
from apps.cadastros.models import Cliente
from apps.core.services.tenant_public_link_service import TenantPublicLinkService
from apps.core.tenant_context import get_current_database_alias


HUMANO = {'atendente', 'humano', 'pessoa', 'falar com atendente', 'falar com uma pessoa'}
SAUDACOES = {
    'oi', 'ola', 'olá', 'bom dia', 'boa tarde', 'boa noite',
    'e ai', 'e aí', 'opa', 'menu', 'inicio', 'início',
}


def normalizar(texto):
    texto = unicodedata.normalize('NFKD', (texto or '').lower())
    return ''.join(item for item in texto if not unicodedata.combining(item)).strip()


def somente_digitos(valor):
    return re.sub(r'\D', '', valor or '')


def localizar_cliente(filial, telefone):
    digitos = somente_digitos(telefone)
    sufixo = digitos[-8:]
    if len(sufixo) < 8:
        return None
    candidatos = Cliente.objects.for_filial(filial).filter(ativo=True).filter(
        Q(celular__icontains=sufixo) | Q(telefone__icontains=sufixo),
    )[:20]
    return next(
        (
            item for item in candidatos
            if somente_digitos(item.celular or item.telefone).endswith(sufixo)
        ),
        None,
    )


def _menu_principal(configuracao):
    saudacao = configuracao.mensagem_saudacao.strip()
    return (
        f'{saudacao}\n\n'
        '*1.* Fazer um agendamento\n'
        '*2.* Falar com um atendente\n'
        '*3.* Encerrar a conversa\n\n'
        'Responda com *1*, *2* ou *3*.'
    )


def _link_agendamento(conversa):
    link, _ = AgendaLinkPublico.objects.get_or_create(filial=conversa.filial)
    if not link.ativo:
        link.ativo = True
        link.save(update_fields=['ativo', 'updated_at'])
    TenantPublicLinkService.register(
        kind='agenda', token=link.token, db_alias=get_current_database_alias(),
    )
    caminho = reverse('agenda_publica:agendar', args=[link.token])
    base_url = settings.PUBLIC_BASE_URL.rstrip('/')
    return f'{base_url}{caminho}'


def reiniciar(conversa):
    conversa.etapa = 'aguardando_opcao'
    conversa.contexto = {}
    conversa.atendimento_humano = False
    conversa.ativa = True
    conversa.save(update_fields=[
        'etapa', 'contexto', 'atendimento_humano', 'ativa', 'updated_at',
    ])
    return _menu_principal(conversa.configuracao)


def _encerrar(conversa):
    conversa.etapa = 'encerrada'
    conversa.contexto = {}
    conversa.atendimento_humano = False
    conversa.ativa = False
    conversa.save(update_fields=[
        'etapa', 'contexto', 'atendimento_humano', 'ativa', 'updated_at',
    ])
    return (
        'Conversa encerrada. Obrigado pelo contato! 👋\n'
        'Quando precisar, envie *oi* para começar novamente.'
    )


def processar_mensagem(conversa, texto):
    valor = normalizar(texto)

    # Uma nova mensagem reabre uma conversa encerrada. Assim o mesmo cliente
    # pode voltar depois sem depender de uma ação manual no painel.
    if not conversa.ativa:
        conversa.ativa = True
        conversa.etapa = 'aguardando_opcao'
        conversa.save(update_fields=['ativa', 'etapa', 'updated_at'])

    # Depois da transferência o robô permanece em silêncio para não disputar
    # a conversa com o atendente humano.
    if conversa.atendimento_humano:
        return None

    if valor in SAUDACOES or valor in {'recomecar', 'reiniciar', 'cancelar'}:
        return reiniciar(conversa)

    if valor == '1':
        conversa.etapa = 'aguardando_opcao'
        conversa.contexto = {}
        conversa.save(update_fields=['etapa', 'contexto', 'updated_at'])
        return (
            'Perfeito! Para escolher o profissional, o serviço, o dia e o horário, '
            'acesse o link abaixo:\n\n'
            f'{_link_agendamento(conversa)}\n\n'
            'Quando quiser ver as opções novamente, digite *menu*.'
        )

    if valor == '2' or any(comando == valor or comando in valor for comando in HUMANO):
        conversa.etapa = 'atendimento_humano'
        conversa.atendimento_humano = True
        conversa.save(update_fields=['etapa', 'atendimento_humano', 'updated_at'])
        return conversa.configuracao.mensagem_transferencia

    if valor == '3' or valor in {'encerrar', 'finalizar', 'sair'}:
        return _encerrar(conversa)

    return (
        'Não entendi essa opção.\n\n'
        f'{_menu_principal(conversa.configuracao)}'
    )
