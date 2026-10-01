import logging

from django.db import transaction
from django.utils import timezone

from .agent import localizar_cliente, processar_mensagem, somente_digitos
from .conversation_service import encerrar_se_expirada
from .gateway import EvolutionClient, GatewayWhatsAppError
from .models import ConversaWhatsApp, MensagemWhatsApp


logger = logging.getLogger(__name__)


def _evento(payload):
    return str(payload.get('event') or '').upper().replace('.', '_').replace('-', '_')


def _instancia(payload):
    instancia = payload.get('instance') or ''
    if isinstance(instancia, dict):
        return instancia.get('instanceName') or instancia.get('name') or ''
    return str(instancia)


def _dados_mensagem(payload):
    dados = payload.get('data') or {}
    chave = dados.get('key') or {}
    mensagem = dados.get('message') or {}
    texto = (
        mensagem.get('conversation')
        or (mensagem.get('extendedTextMessage') or {}).get('text')
        or (mensagem.get('imageMessage') or {}).get('caption')
        or (mensagem.get('videoMessage') or {}).get('caption')
        or ''
    )
    return {
        'id': str(chave.get('id') or dados.get('id') or ''),
        'from_me': bool(chave.get('fromMe')),
        'remote_jid': str(chave.get('remoteJid') or dados.get('remoteJid') or ''),
        'nome': str(dados.get('pushName') or payload.get('sender') or '')[:150],
        'texto': str(texto).strip(),
    }


def receber_evento(configuracao, payload):
    evento = _evento(payload)
    if _instancia(payload) and _instancia(payload) != configuracao.instancia:
        return 'ignorado'
    configuracao.ultimo_evento_em = timezone.now()

    if evento == 'CONNECTION_UPDATE':
        estado = str((payload.get('data') or {}).get('state') or '').lower()
        configuracao.status = (
            configuracao.Status.CONECTADO if estado == 'open'
            else configuracao.Status.DESCONECTADO
        )
        configuracao.ultimo_erro = ''
        configuracao.save(update_fields=['status', 'ultimo_evento_em', 'ultimo_erro', 'updated_at'])
        return 'conexao_atualizada'

    configuracao.save(update_fields=['ultimo_evento_em', 'updated_at'])
    if evento != 'MESSAGES_UPSERT':
        return 'evento_registrado'

    dados = _dados_mensagem(payload)
    if not dados['remote_jid'] or dados['remote_jid'].endswith('@g.us'):
        return 'ignorado'
    agora = timezone.now()
    if dados['from_me']:
        ConversaWhatsApp.objects.filter(
            configuracao=configuracao, remote_jid=dados['remote_jid'],
        ).update(ultima_mensagem_em=agora, updated_at=agora)
        return 'ignorado'
    if not dados['texto']:
        return 'sem_texto'

    telefone = somente_digitos(dados['remote_jid'].split('@')[0])
    with transaction.atomic():
        conversa, criada = ConversaWhatsApp.objects.select_for_update().get_or_create(
            configuracao=configuracao, remote_jid=dados['remote_jid'],
            defaults={
                'filial': configuracao.filial, 'telefone': telefone,
                'nome_contato': dados['nome'], 'cliente': localizar_cliente(configuracao.filial, telefone),
            },
        )
        if not criada and dados['nome'] and not conversa.nome_contato:
            conversa.nome_contato = dados['nome']
        if not criada:
            encerrar_se_expirada(conversa, agora=agora)
        conversa.ultima_mensagem_em = agora
        conversa.save(update_fields=['nome_contato', 'ultima_mensagem_em', 'updated_at'])
        padrao_mensagem = {
                'direcao': MensagemWhatsApp.Direcao.ENTRADA,
                'texto': dados['texto'], 'tipo': 'texto',
                'dados_evento': {'evento': evento},
        }
        if dados['id']:
            mensagem, criada = MensagemWhatsApp.objects.get_or_create(
                conversa=conversa, identificador_externo=dados['id'], defaults=padrao_mensagem,
            )
        else:
            mensagem = MensagemWhatsApp.objects.create(conversa=conversa, **padrao_mensagem)
            criada = True
        if not criada:
            return 'duplicado'
        resposta = processar_mensagem(conversa, dados['texto']) if configuracao.agente_ativo else None

    if not resposta:
        return 'recebido'
    saida = MensagemWhatsApp.objects.create(
        conversa=conversa, direcao=MensagemWhatsApp.Direcao.SAIDA,
        texto=resposta, tipo='texto', status='pendente',
    )
    try:
        retorno = EvolutionClient(configuracao).enviar_texto(telefone, resposta)
        saida.identificador_externo = str((retorno.get('key') or {}).get('id') or '')
        saida.status = 'enviada'
    except GatewayWhatsAppError as exc:
        saida.status = 'erro'
        saida.dados_evento = {'erro': str(exc)}
        logger.warning('Falha ao responder WhatsApp: %s', exc)
    saida.save(update_fields=['identificador_externo', 'status', 'dados_evento', 'updated_at'])
    return saida.status
