import logging

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.cadastros.models import Cliente
from apps.core.models import EmpresaBanco, Usuario
from apps.core.tenant_registry import register_tenant_database
from apps.crm.models import CarteiraCliente

from .agent import localizar_cliente, processar_mensagem, somente_digitos
from .conversation_service import encerrar_se_expirada
from .gateway import (
    EvolutionClient, GatewayWhatsAppError, MetaCloudClient, identificador_envio,
)
from .ai_service import IAWhatsAppError, OpenAIWhatsAppClient
from .intelligent_agent import processar_com_ia, resposta_fora_horario
from .models import (
    ConexaoWhatsAppVendedor, ConfiguracaoWhatsApp, ConversaWhatsApp, MensagemWhatsApp,
)


logger = logging.getLogger(__name__)


def _cliente_whatsapp(configuracao):
    if isinstance(configuracao, ConexaoWhatsAppVendedor):
        return MetaCloudClient(configuracao)
    if configuracao.provedor == ConfiguracaoWhatsApp.Provedor.META_CLOUD:
        return MetaCloudClient(configuracao)
    return EvolutionClient(configuracao)


def _conexao_comercial(configuracao):
    return isinstance(configuracao, ConexaoWhatsAppVendedor)


def _filtro_canal(configuracao, *, prefixo=''):
    campo = 'conexao_vendedor' if _conexao_comercial(configuracao) else 'configuracao'
    return {f'{prefixo}{campo}': configuracao}


def _dados_canal(configuracao):
    if _conexao_comercial(configuracao):
        return {
            'conexao_vendedor': configuracao,
            'filial': configuracao.filial_padrao,
        }
    return {'configuracao': configuracao, 'filial': configuracao.filial}


def _resolver_cliente_comercial(conexao, telefone):
    """Resolve o cliente no tenant sem criar relações entre bancos."""
    banco = EmpresaBanco.objects.using('default').filter(
        empresa_id=conexao.empresa_id,
        ativo=True,
        status=EmpresaBanco.Status.ATIVO,
    ).first()
    if not banco or not register_tenant_database(banco):
        return {
            'tenant_alias': '', 'cliente_operacional_id': None,
            'cliente_grupo_replicacao': None, 'fora_carteira': True,
        }
    digitos = somente_digitos(telefone)
    sufixo = digitos[-8:]
    if len(sufixo) < 8:
        return {
            'tenant_alias': banco.db_alias, 'cliente_operacional_id': None,
            'cliente_grupo_replicacao': None, 'fora_carteira': True,
        }
    candidatos = (
        Cliente.objects.using(banco.db_alias)
        .filter(filial__empresa__cnpj=conexao.empresa.cnpj, ativo=True)
        .filter(Q(celular__icontains=sufixo) | Q(telefone__icontains=sufixo))[:20]
    )
    cliente = next((
        item for item in candidatos
        if any(
            somente_digitos(numero).endswith(sufixo)
            for numero in (item.celular, item.telefone)
            if numero
        )
    ), None)
    if not cliente:
        return {
            'tenant_alias': banco.db_alias, 'cliente_operacional_id': None,
            'cliente_grupo_replicacao': None, 'fora_carteira': True,
        }
    vendedor = Usuario.objects.using(banco.db_alias).filter(
        email__iexact=conexao.vendedor.email,
        empresa__cnpj=conexao.empresa.cnpj,
        ativo=True,
    ).first()
    pertence = bool(
        vendedor
        and CarteiraCliente.objects.using(banco.db_alias).filter(
            empresa__cnpj=conexao.empresa.cnpj,
            grupo_cliente=cliente.grupo_replicacao,
            vendedor_id=vendedor.pk,
        ).exists()
    )
    return {
        'tenant_alias': banco.db_alias,
        'cliente_operacional_id': cliente.pk,
        'cliente_grupo_replicacao': cliente.grupo_replicacao,
        'fora_carteira': not pertence,
    }


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
        'dados_gateway': payload.get('_meta_media') or {
            'key': chave,
            'message': mensagem,
            'messageTimestamp': dados.get('messageTimestamp'),
        },
    }


def receber_evento(configuracao, payload):
    evento = _evento(payload)
    if (
        not _conexao_comercial(configuracao)
        and _instancia(payload)
        and _instancia(payload) != configuracao.instancia
    ):
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
    if _conexao_comercial(configuracao) and not configuracao.filial_padrao_id:
        configuracao.status = configuracao.Status.ERRO
        configuracao.ultimo_erro = 'Defina a filial padrão antes de receber mensagens.'
        configuracao.save(update_fields=['status', 'ultimo_erro', 'updated_at'])
        return 'sem_filial_padrao'
    agora = timezone.now()
    if dados['from_me']:
        conversa = (
            ConversaWhatsApp.objects.filter(
                **_filtro_canal(configuracao), remote_jid=dados['remote_jid'],
            )
            .order_by('-ativa', '-ultima_mensagem_em', '-created_at')
            .first()
        )
        if not _conexao_comercial(configuracao):
            if conversa:
                conversa.ultima_mensagem_em = agora
                conversa.save(update_fields=['ultima_mensagem_em', 'updated_at'])
            return 'ignorado'
        if not dados['texto'] and dados['tipo'] != 'audio':
            return 'sem_texto'
        telefone = somente_digitos(dados['remote_jid'].split('@')[0])
        with transaction.atomic():
            if dados['id'] and MensagemWhatsApp.objects.filter(
                **_filtro_canal(configuracao, prefixo='conversa__'),
                identificador_externo=dados['id'],
            ).exists():
                return 'duplicado'
            if conversa is None:
                resolucao = _resolver_cliente_comercial(configuracao, telefone)
                conversa = ConversaWhatsApp.objects.create(
                    **_dados_canal(configuracao),
                    remote_jid=dados['remote_jid'],
                    telefone=telefone,
                    nome_contato=dados['nome'],
                    ultima_mensagem_em=agora,
                    **resolucao,
                )
            else:
                conversa.ultima_mensagem_em = agora
                conversa.save(update_fields=['ultima_mensagem_em', 'updated_at'])
            MensagemWhatsApp.objects.create(
                conversa=conversa,
                identificador_externo=dados['id'],
                direcao=MensagemWhatsApp.Direcao.SAIDA,
                origem=MensagemWhatsApp.Origem.CELULAR,
                autor_nome=configuracao.vendedor.nome,
                autor_email=configuracao.vendedor.email,
                texto=dados['texto'],
                texto_original=dados['texto'],
                tipo=dados['tipo'],
                status='enviada',
                dados_evento={'evento': evento, 'origem': 'smb_message_echoes'},
            )
        return 'eco_registrado'
    if not dados['texto'] and dados['tipo'] != 'audio':
        return 'sem_texto'

    telefone = somente_digitos(dados['remote_jid'].split('@')[0])
    with transaction.atomic():
        if dados['id'] and MensagemWhatsApp.objects.filter(
            **_filtro_canal(configuracao, prefixo='conversa__'),
            identificador_externo=dados['id'],
        ).exists():
            return 'duplicado'
        conversa = (
            ConversaWhatsApp.objects.select_for_update()
            .filter(
                **_filtro_canal(configuracao),
                remote_jid=dados['remote_jid'],
                ativa=True,
            )
            .order_by('-ultima_mensagem_em', '-created_at')
            .first()
        )
        if (
            conversa
            and not _conexao_comercial(configuracao)
            and encerrar_se_expirada(conversa, agora=agora)
        ):
            conversa = None
        criada = conversa is None
        if criada:
            resolucao = (
                _resolver_cliente_comercial(configuracao, telefone)
                if _conexao_comercial(configuracao)
                else {}
            )
            conversa = ConversaWhatsApp.objects.create(
                **_dados_canal(configuracao),
                remote_jid=dados['remote_jid'],
                telefone=telefone,
                nome_contato=dados['nome'],
                cliente=(
                    None if _conexao_comercial(configuracao)
                    else localizar_cliente(configuracao.filial, telefone)
                ),
                **resolucao,
            )
        elif dados['nome'] and not conversa.nome_contato:
            conversa.nome_contato = dados['nome']
        conversa.ultima_mensagem_em = agora
        conversa.save(update_fields=[
            'nome_contato', 'etapa_crm', 'ultima_mensagem_em', 'updated_at',
        ])
        padrao_mensagem = {
                'direcao': MensagemWhatsApp.Direcao.ENTRADA,
                'origem': MensagemWhatsApp.Origem.CLIENTE,
                'texto': dados['texto'], 'tipo': dados['tipo'],
                'texto_original': dados['texto'],
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

    if _conexao_comercial(configuracao):
        return 'recebido'

    texto_processado = dados['texto']
    resposta = (
        resposta_fora_horario(conversa)
        if configuracao.agente_ativo
        else None
    )
    if dados['tipo'] == 'audio':
        if conversa.atendimento_humano and resposta is None:
            resposta = None
        elif resposta is not None:
            mensagem.status = 'recebida_fora_horario'
            mensagem.save(update_fields=['status', 'updated_at'])
        elif not configuracao.ia_ativa or not configuracao.ia_audio_ativo:
            mensagem.status = 'nao_suportado'
            mensagem.save(update_fields=['status', 'updated_at'])
            resposta = (
                'Ainda não estou configurado para entender áudios. '
                'Pode enviar a mensagem por texto, por favor?'
            ) if configuracao.agente_ativo else None
        else:
            try:
                midia = _cliente_whatsapp(configuracao).obter_midia_base64(
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
    if configuracao.agente_ativo and texto_processado and resposta is None:
        resposta = processar_com_ia(conversa, texto_processado, mensagem)
        if resposta is None:
            resposta = processar_mensagem(conversa, texto_processado)

    if not resposta:
        return 'recebido'
    saida = MensagemWhatsApp.objects.create(
        conversa=conversa, direcao=MensagemWhatsApp.Direcao.SAIDA,
        origem=MensagemWhatsApp.Origem.AUTOMACAO,
        texto=resposta, texto_original=resposta, tipo='texto', status='pendente',
    )
    try:
        retorno = _cliente_whatsapp(configuracao).enviar_texto(telefone, resposta)
        saida.identificador_externo = identificador_envio(retorno)
        saida.status = 'enviada'
    except GatewayWhatsAppError as exc:
        saida.status = 'erro'
        saida.dados_evento = {'erro': str(exc)}
        logger.warning('Falha ao responder WhatsApp: %s', exc)
    saida.save(update_fields=['identificador_externo', 'status', 'dados_evento', 'updated_at'])
    return saida.status


def receber_evento_meta(configuracao, payload):
    """Adapta webhooks oficiais ao fluxo único de conversa do iTED."""
    resultados = []
    for entrada in payload.get('entry') or []:
        for alteracao in entrada.get('changes') or []:
            valor = alteracao.get('value') or {}
            phone_number_id = str((valor.get('metadata') or {}).get('phone_number_id') or '')
            if phone_number_id and phone_number_id != configuracao.meta_phone_number_id:
                continue
            for status in valor.get('statuses') or []:
                identificador = str(status.get('id') or '')
                if identificador:
                    MensagemWhatsApp.objects.filter(
                        **_filtro_canal(configuracao, prefixo='conversa__'),
                        identificador_externo=identificador,
                    ).update(
                        status=str(status.get('status') or '')[:30],
                        dados_evento={'meta_status': status},
                    )
                resultados.append('status_atualizado')
            contatos = {
                str(item.get('wa_id') or ''): str((item.get('profile') or {}).get('name') or '')
                for item in (valor.get('contacts') or [])
            }
            for mensagem in valor.get('messages') or []:
                telefone = str(mensagem.get('from') or '')
                tipo = str(mensagem.get('type') or 'text')
                texto = str((mensagem.get('text') or {}).get('body') or '')
                audio = mensagem.get('audio') or {}
                convertido = {
                    'event': 'messages.upsert',
                    'data': {
                        'key': {
                            'id': mensagem.get('id'),
                            'fromMe': False,
                            'remoteJid': f'{telefone}@s.whatsapp.net',
                        },
                        'pushName': contatos.get(telefone, ''),
                        'messageTimestamp': mensagem.get('timestamp'),
                        'message': (
                            {'audioMessage': {
                                'mimetype': audio.get('mime_type') or 'audio/ogg',
                            }}
                            if tipo == 'audio'
                            else {'conversation': texto}
                        ),
                    },
                    '_meta_media': {
                        'id': audio.get('id'),
                        'audio': audio,
                    } if tipo == 'audio' else {},
                }
                resultados.append(receber_evento(configuracao, convertido))
            if _conexao_comercial(configuracao):
                # O campo assinado é ``smb_message_echoes``, mas a lista vem
                # em ``value.message_echoes`` no payload oficial da Meta.
                ecos = (
                    valor.get('message_echoes')
                    or valor.get('smb_message_echoes')
                    or []
                )
                for eco_bruto in ecos:
                    eco = eco_bruto.get('message') or eco_bruto
                    if not isinstance(eco, dict):
                        continue
                    telefone = str(
                        eco.get('to')
                        or eco_bruto.get('to')
                        or eco.get('recipient_id')
                        or eco_bruto.get('recipient_id')
                        or ''
                    )
                    tipo = str(eco.get('type') or 'text')
                    identificador_relacionado = str(
                        eco.get('message_id')
                        or (eco.get('context') or {}).get('id')
                        or ''
                    )
                    if tipo in {'revoke', 'deleted'} and identificador_relacionado:
                        MensagemWhatsApp.objects.filter(
                            **_filtro_canal(configuracao, prefixo='conversa__'),
                            identificador_externo=identificador_relacionado,
                        ).update(
                            status='apagada', apagada_em=timezone.now(),
                            dados_evento={'meta_revoke': eco_bruto},
                        )
                        resultados.append('mensagem_apagada')
                        continue
                    texto = str((eco.get('text') or {}).get('body') or '')
                    convertido = {
                        'event': 'messages.upsert',
                        'data': {
                            'key': {
                                'id': eco.get('id') or eco_bruto.get('id'),
                                'fromMe': True,
                                'remoteJid': f'{telefone}@s.whatsapp.net',
                            },
                            'pushName': configuracao.vendedor.nome,
                            'messageTimestamp': eco.get('timestamp'),
                            'message': {'conversation': texto},
                        },
                    }
                    resultados.append(receber_evento(configuracao, convertido))
    return resultados[-1] if resultados else 'evento_registrado'
