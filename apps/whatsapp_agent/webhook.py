import logging

from django.db import transaction
from django.utils import timezone

from .agent import localizar_cliente, processar_mensagem, somente_digitos
from .conversation_service import encerrar_se_expirada
from .gateway import EvolutionClient, GatewayWhatsAppError
from .ai_service import IAWhatsAppError, OpenAIWhatsAppClient
from .intelligent_agent import processar_com_ia
from .models import ConversaWhatsApp, MensagemWhatsApp


logger = logging.getLogger(__name__)


def _evento(payload):
    return str(payload.get('event') or '').upper().replace('.', '_').replace('-', '_')


def _instancia(payload):
    instancia = payload.get('instance') or ''
    if isinstance(instancia, dict):
        return instancia.get('instanceName') or instancia.get('name') or ''
    return str(instancia)


def _conteudo_mensagem(mensagem):
    """Remove invólucros comuns usados pelo WhatsApp para texto e áudio."""
    atual = mensagem if isinstance(mensagem, dict) else {}
    for _ in range(4):
        proximo = None
        for chave in (
            'ephemeralMessage', 'viewOnceMessage', 'viewOnceMessageV2',
            'viewOnceMessageV2Extension', 'documentWithCaptionMessage',
        ):
            item = atual.get(chave)
            if isinstance(item, dict) and isinstance(item.get('message'), dict):
                proximo = item['message']
                break
        if proximo is None:
            break
        atual = proximo
    return atual


def _dados_mensagem(payload):
    dados = payload.get('data') or {}
    chave = dados.get('key') or {}
    mensagem = _conteudo_mensagem(dados.get('message') or {})
    texto = (
        mensagem.get('conversation')
        or (mensagem.get('extendedTextMessage') or {}).get('text')
        or (mensagem.get('imageMessage') or {}).get('caption')
        or (mensagem.get('videoMessage') or {}).get('caption')
        or ''
    )
    audio = mensagem.get('audioMessage') or {}
    return {
        'id': str(chave.get('id') or dados.get('id') or ''),
        'from_me': bool(chave.get('fromMe')),
        'remote_jid': str(chave.get('remoteJid') or dados.get('remoteJid') or ''),
        'nome': str(dados.get('pushName') or payload.get('sender') or '')[:150],
        'texto': str(texto).strip(),
        'tipo': 'audio' if audio else 'texto',
        'mimetype': str(audio.get('mimetype') or 'audio/ogg'),
        'dados_gateway': {
            'key': chave,
            'message': mensagem,
            'messageTimestamp': dados.get('messageTimestamp'),
        },
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
    if not dados['texto'] and dados['tipo'] != 'audio':
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
        conversa.save(update_fields=[
            'nome_contato', 'etapa_crm', 'ultima_mensagem_em', 'updated_at',
        ])
        padrao_mensagem = {
                'direcao': MensagemWhatsApp.Direcao.ENTRADA,
                'texto': dados['texto'], 'tipo': dados['tipo'],
                'status': 'processando' if dados['tipo'] == 'audio' else 'recebida',
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

    texto_processado = dados['texto']
    if dados['tipo'] == 'audio':
        if not configuracao.ia_ativa or not configuracao.ia_audio_ativo:
            mensagem.status = 'nao_suportado'
            mensagem.save(update_fields=['status', 'updated_at'])
            resposta = (
                'Ainda não estou configurado para entender áudios. '
                'Pode enviar a mensagem por texto, por favor?'
            ) if configuracao.agente_ativo else None
        else:
            try:
                midia = EvolutionClient(configuracao).obter_midia_base64(
                    dados['dados_gateway'],
                )
                transcricao = OpenAIWhatsAppClient(configuracao).transcrever(
                    conversa=conversa,
                    mensagem=mensagem,
                    base64_audio=midia['base64'],
                    mimetype=midia.get('mimetype') or dados['mimetype'],
                )
                texto_processado = str(transcricao.conteudo)
                mensagem.texto = texto_processado
                mensagem.status = 'transcrita'
                mensagem.dados_evento = {
                    'evento': evento,
                    'mimetype': midia.get('mimetype') or dados['mimetype'],
                }
                mensagem.save(update_fields=['texto', 'status', 'dados_evento', 'updated_at'])
                resposta = None
            except (GatewayWhatsAppError, IAWhatsAppError) as exc:
                mensagem.status = 'erro_transcricao'
                mensagem.dados_evento = {'evento': evento, 'erro': str(exc)}
                mensagem.save(update_fields=['status', 'dados_evento', 'updated_at'])
                logger.warning('Falha ao transcrever áudio do WhatsApp: %s', exc)
                resposta = (
                    'Não consegui entender esse áudio agora. '
                    'Pode enviar por texto, por favor?'
                ) if configuracao.agente_ativo else None
    else:
        resposta = None

    if configuracao.agente_ativo and texto_processado and resposta is None:
        resposta = processar_com_ia(conversa, texto_processado, mensagem)
        if resposta is None:
            resposta = processar_mensagem(conversa, texto_processado)

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
