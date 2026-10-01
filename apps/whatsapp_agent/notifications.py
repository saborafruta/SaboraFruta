import logging
import re

from django.conf import settings
from django.utils import timezone

from apps.agenda.models import AgendamentoItem
from apps.core.models import EmpresaBanco, Filial

from .gateway import EvolutionClient, GatewayWhatsAppError
from .models import ConfiguracaoWhatsApp, ConversaWhatsApp, MensagemWhatsApp


logger = logging.getLogger(__name__)


def _numero_whatsapp(valor):
    numero = re.sub(r'\D', '', valor or '')
    if len(numero) in {10, 11}:
        numero = f'55{numero}'
    # Celulares brasileiros antigos podem estar cadastrados com oito dígitos.
    # Para números móveis (primeiro dígito local entre 6 e 9), inclui o nono
    # dígito depois do DDD sem alterar telefones fixos.
    if len(numero) == 12 and numero.startswith('55') and numero[4] in '6789':
        numero = f'{numero[:4]}9{numero[4:]}'
    return numero if 12 <= len(numero) <= 15 else ''


def _filial_central(filial, db_alias):
    if not settings.TENANT_DATABASE_ROUTING_ENABLED or db_alias == 'default':
        return Filial.objects.using('default').filter(pk=filial.pk, ativo=True).first()

    banco = EmpresaBanco.objects.using('default').filter(
        db_alias=db_alias,
        ativo=True,
        status=EmpresaBanco.Status.ATIVO,
    ).first()
    if not banco:
        return None
    return Filial.objects.using('default').filter(
        empresa_id=banco.empresa_id,
        cnpj=filial.cnpj,
        ativo=True,
    ).first()


def _configuracao_conectada(filial, db_alias):
    filial_central = _filial_central(filial, db_alias)
    if not filial_central:
        return None
    return (
        ConfiguracaoWhatsApp.objects.using('default')
        .filter(
            filial_id=filial_central.pk,
            ativo=True,
            status=ConfiguracaoWhatsApp.Status.CONECTADO,
        )
        .first()
    )


def _texto_agendamento(agendamento, db_alias, *, lembrete):
    servicos = ', '.join(
        AgendamentoItem.objects.using(db_alias)
        .filter(agendamento_id=agendamento.pk)
        .order_by('ordem', 'pk')
        .values_list('descricao', flat=True)
    )
    inicio = timezone.localtime(agendamento.inicio)
    titulo = (
        f'Olá, {agendamento.pessoa_atendida_nome}! 👋\n\n'
        'Este é um lembrete do seu agendamento:'
        if lembrete else
        f'Olá, {agendamento.pessoa_atendida_nome}! ✅\n\n'
        'Seu agendamento foi confirmado:'
    )
    final = '\n\nEsperamos você!' if lembrete else '\n\nSeu horário já está reservado. Até lá!'
    return (
        f'{titulo}\n\n'
        f'*Serviço:* {servicos or "Serviço agendado"}\n'
        f'*Profissional:* {agendamento.profissional}\n'
        f'*Data:* {inicio:%d/%m/%Y}\n'
        f'*Horário:* {inicio:%H:%M}'
        f'{final}'
    )


def _enviar_notificacao_agendamento(agendamento, *, db_alias, lembrete=False):
    telefone = _numero_whatsapp(
        agendamento.telefone_contato
        or getattr(agendamento.cliente, 'celular', '')
        or getattr(agendamento.cliente, 'telefone', '')
    )
    if not telefone:
        return False, 'O agendamento não possui um WhatsApp válido.'

    configuracao = _configuracao_conectada(agendamento.filial, db_alias)
    if not configuracao:
        return False, 'O WhatsApp desta filial não está conectado.'

    texto = _texto_agendamento(agendamento, db_alias, lembrete=lembrete)
    remote_jid = f'{telefone}@s.whatsapp.net'
    conversa, _ = ConversaWhatsApp.objects.using('default').get_or_create(
        configuracao_id=configuracao.pk,
        remote_jid=remote_jid,
        defaults={
            'filial_id': configuracao.filial_id,
            'telefone': telefone,
            'nome_contato': agendamento.pessoa_atendida_nome,
        },
    )
    conversa.ultima_mensagem_em = timezone.now()
    conversa.save(using='default', update_fields=['ultima_mensagem_em', 'updated_at'])
    saida = MensagemWhatsApp.objects.using('default').create(
        conversa_id=conversa.pk,
        direcao=MensagemWhatsApp.Direcao.SAIDA,
        texto=texto,
        tipo='lembrete_agendamento' if lembrete else 'confirmacao_agendamento',
        status='pendente',
    )
    try:
        retorno = EvolutionClient(configuracao).enviar_texto(telefone, texto)
        saida.identificador_externo = str((retorno.get('key') or {}).get('id') or '')
        saida.status = 'enviada'
        mensagem = 'Lembrete enviado pelo WhatsApp.' if lembrete else 'Confirmação enviada pelo WhatsApp.'
        enviado = True
    except GatewayWhatsAppError as exc:
        saida.status = 'erro'
        saida.dados_evento = {'erro': str(exc)}
        mensagem = f'Não foi possível enviar pelo WhatsApp: {exc}'
        enviado = False
        logger.warning('Falha ao enviar notificação de agendamento: %s', exc)
    saida.save(
        using='default',
        update_fields=['identificador_externo', 'status', 'dados_evento', 'updated_at'],
    )
    return enviado, mensagem


def enviar_notificacao_agendamento(agendamento, *, db_alias, lembrete=False):
    """Envia sem permitir que uma falha do canal desfaça o agendamento."""
    try:
        return _enviar_notificacao_agendamento(
            agendamento,
            db_alias=db_alias,
            lembrete=lembrete,
        )
    except Exception:
        logger.exception('Falha inesperada ao notificar agendamento pelo WhatsApp.')
        return False, 'Não foi possível enviar a mensagem pelo WhatsApp agora.'
