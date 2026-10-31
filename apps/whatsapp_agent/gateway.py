import base64
import io
from urllib.parse import quote

import qrcode
import requests
from django.conf import settings


class GatewayWhatsAppError(Exception):
    pass


def identificador_envio(retorno):
    """Normaliza o identificador devolvido pelos gateways suportados."""
    if not isinstance(retorno, dict):
        return ''
    evolution_id = (retorno.get('key') or {}).get('id')
    mensagens = retorno.get('messages') or []
    meta_id = mensagens[0].get('id') if mensagens and isinstance(mensagens[0], dict) else ''
    return str(evolution_id or meta_id or '')


class EvolutionClient:
    EVENTOS = [
        'QRCODE_UPDATED', 'CONNECTION_UPDATE', 'MESSAGES_UPSERT',
        'MESSAGES_UPDATE', 'MESSAGES_EDITED', 'MESSAGES_DELETE',
    ]

    def __init__(self, configuracao):
        self.configuracao = configuracao
        self.base_url = (
            getattr(settings, 'WHATSAPP_EVOLUTION_URL', '')
            or getattr(configuracao, 'gateway_url', '')
        ).rstrip('/')
        obter_api_key = getattr(configuracao, 'obter_api_key', None)
        self.api_key = (
            getattr(settings, 'WHATSAPP_EVOLUTION_API_KEY', '')
            or (obter_api_key() if obter_api_key else '')
        )
        if not self.base_url or not self.api_key:
            raise GatewayWhatsAppError('O servidor de conexão do WhatsApp ainda não está configurado.')

    @property
    def headers(self):
        return {'apikey': self.api_key, 'Content-Type': 'application/json'}

    def _request(self, metodo, caminho, **kwargs):
        try:
            resposta = requests.request(
                metodo, f'{self.base_url}{caminho}', headers=self.headers, timeout=20, **kwargs,
            )
        except requests.RequestException as exc:
            raise GatewayWhatsAppError('Não foi possível acessar o servidor do WhatsApp.') from exc
        if resposta.status_code >= 400:
            try:
                detalhe = resposta.json().get('response', {}).get('message')
            except (ValueError, AttributeError):
                detalhe = None
            itens = detalhe if isinstance(detalhe, list) else [detalhe]
            if any(
                isinstance(item, dict) and item.get('exists') is False
                for item in itens
            ):
                detalhe = 'O número informado não foi encontrado no WhatsApp.'
            if isinstance(detalhe, list):
                detalhe = ' '.join(str(item) for item in detalhe)
            raise GatewayWhatsAppError(str(detalhe or f'Gateway respondeu HTTP {resposta.status_code}.'))
        try:
            return resposta.json()
        except ValueError as exc:
            raise GatewayWhatsAppError('O gateway retornou uma resposta inválida.') from exc

    def criar_instancia(self, webhook_url):
        return self._request('POST', '/instance/create', json={
            'instanceName': self.configuracao.instancia,
            'qrcode': True,
            'integration': 'WHATSAPP-BAILEYS',
            'rejectCall': True,
            'msgCall': 'Este número não recebe chamadas. Envie uma mensagem por favor.',
            'groupsIgnore': True,
            'alwaysOnline': False,
            'readMessages': True,
            'readStatus': False,
            'syncFullHistory': False,
            'webhook': {
                'url': webhook_url, 'byEvents': False, 'base64': False,
                'events': self.EVENTOS,
            },
        })

    def configurar_webhook(self, webhook_url):
        return self._request('POST', f'/webhook/set/{quote(self.configuracao.instancia)}', json={
            'webhook': {
                'enabled': True,
                'url': webhook_url,
                'byEvents': False,
                'base64': False,
                'events': self.EVENTOS,
            },
        })

    def estado(self):
        dados = self._request('GET', f'/instance/connectionState/{quote(self.configuracao.instancia)}')
        return (dados.get('instance') or {}).get('state', 'close')

    def conectar(self):
        return self._request('GET', f'/instance/connect/{quote(self.configuracao.instancia)}')

    def detalhes(self):
        return self._request(
            'GET',
            f'/instance/fetchInstances?instanceName={quote(self.configuracao.instancia)}',
        )

    def enviar_texto(self, telefone, texto):
        return self._request('POST', f'/message/sendText/{quote(self.configuracao.instancia)}', json={
            'number': telefone,
            'text': texto,
            'delay': 700,
            'linkPreview': False,
        })

    def enviar_documento(self, telefone, conteudo, nome_arquivo, legenda=''):
        return self.enviar_midia(
            telefone, conteudo, nome_arquivo, 'application/pdf',
            tipo='documento', legenda=legenda,
        )

    def enviar_midia(
        self, telefone, conteudo, nome_arquivo, mimetype, *, tipo='documento', legenda='',
    ):
        mediatype = {
            'imagem': 'image', 'audio': 'audio', 'video': 'video',
            'documento': 'document',
        }.get(tipo, 'document')
        return self._request(
            'POST',
            f'/message/sendMedia/{quote(self.configuracao.instancia)}',
            json={
                'number': telefone,
                'mediatype': mediatype,
                'mimetype': mimetype,
                'caption': legenda,
                'media': base64.b64encode(conteudo).decode('ascii'),
                'fileName': nome_arquivo,
                'delay': 700,
                'linkPreview': False,
            },
        )

    def obter_midia_base64(self, dados_mensagem):
        dados = self._request(
            'POST',
            f'/chat/getBase64FromMediaMessage/{quote(self.configuracao.instancia)}',
            json={'message': dados_mensagem, 'convertToMp4': False},
        )
        if not isinstance(dados, dict):
            raise GatewayWhatsAppError('O gateway não retornou o conteúdo do áudio.')
        base64_midia = (
            dados.get('base64')
            or dados.get('data')
            or (dados.get('media') or {}).get('base64')
        )
        if isinstance(base64_midia, str) and ',' in base64_midia[:100]:
            base64_midia = base64_midia.split(',', 1)[1]
        if not base64_midia:
            raise GatewayWhatsAppError('Não foi possível baixar o áudio recebido.')
        return {
            'base64': base64_midia,
            'mimetype': dados.get('mimetype') or dados.get('mimeType') or 'audio/ogg',
        }


class MetaCloudClient:
    """Cliente da API oficial WhatsApp Cloud da Meta."""

    def __init__(self, configuracao):
        self.configuracao = configuracao
        self.phone_number_id = (configuracao.meta_phone_number_id or '').strip()
        self.business_account_id = (configuracao.meta_business_account_id or '').strip()
        self.access_token = configuracao.obter_meta_access_token()
        self.graph_version = (configuracao.meta_graph_version or 'v23.0').strip()
        self.base_url = f'https://graph.facebook.com/{self.graph_version}'
        if not self.phone_number_id or not self.access_token:
            raise GatewayWhatsAppError(
                'Informe o ID do número e o token permanente da API oficial do WhatsApp.',
            )

    @property
    def headers(self):
        return {'Authorization': f'Bearer {self.access_token}'}

    def _request(self, metodo, caminho, **kwargs):
        headers = dict(self.headers)
        if 'files' not in kwargs:
            headers['Content-Type'] = 'application/json'
        try:
            resposta = requests.request(
                metodo,
                f'{self.base_url}{caminho}',
                headers=headers,
                timeout=30,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise GatewayWhatsAppError('Não foi possível acessar a API oficial do WhatsApp.') from exc
        if resposta.status_code >= 400:
            try:
                erro = resposta.json().get('error') or {}
                detalhe = erro.get('error_user_msg') or erro.get('message')
            except (ValueError, AttributeError):
                detalhe = None
            raise GatewayWhatsAppError(
                str(detalhe or f'API oficial respondeu HTTP {resposta.status_code}.'),
            )
        try:
            return resposta.json()
        except ValueError as exc:
            raise GatewayWhatsAppError('A API oficial retornou uma resposta inválida.') from exc

    def detalhes(self):
        return self._request(
            'GET',
            f'/{quote(self.phone_number_id)}?fields=display_phone_number,verified_name,quality_rating',
        )

    def enviar_texto(self, telefone, texto):
        return self._request('POST', f'/{quote(self.phone_number_id)}/messages', json={
            'messaging_product': 'whatsapp',
            'recipient_type': 'individual',
            'to': telefone,
            'type': 'text',
            'text': {'preview_url': False, 'body': texto},
        })

    def enviar_template(self, telefone, nome, idioma, parametros):
        return self._request('POST', f'/{quote(self.phone_number_id)}/messages', json={
            'messaging_product': 'whatsapp',
            'recipient_type': 'individual',
            'to': telefone,
            'type': 'template',
            'template': {
                'name': nome,
                'language': {'code': idioma or 'pt_BR'},
                'components': [{
                    'type': 'body',
                    'parameters': [
                        {'type': 'text', 'text': str(valor)} for valor in parametros
                    ],
                }],
            },
        })

    def enviar_documento(self, telefone, conteudo, nome_arquivo, legenda=''):
        return self.enviar_midia(
            telefone, conteudo, nome_arquivo, 'application/pdf',
            tipo='documento', legenda=legenda,
        )

    def enviar_midia(
        self, telefone, conteudo, nome_arquivo, mimetype, *, tipo='documento', legenda='',
    ):
        tipo_meta = {
            'imagem': 'image', 'audio': 'audio', 'video': 'video',
            'documento': 'document',
        }.get(tipo, 'document')
        midia = self._request(
            'POST',
            f'/{quote(self.phone_number_id)}/media',
            data={'messaging_product': 'whatsapp', 'type': mimetype},
            files={'file': (nome_arquivo, conteudo, mimetype)},
        )
        media_id = midia.get('id')
        if not media_id:
            raise GatewayWhatsAppError('A API oficial não devolveu o ID da mídia.')
        detalhe = {'id': media_id}
        if tipo_meta == 'document':
            detalhe['filename'] = nome_arquivo
        if legenda and tipo_meta != 'audio':
            detalhe['caption'] = legenda
        return self._request('POST', f'/{quote(self.phone_number_id)}/messages', json={
            'messaging_product': 'whatsapp',
            'recipient_type': 'individual',
            'to': telefone,
            'type': tipo_meta,
            tipo_meta: detalhe,
        })

    def obter_midia_base64(self, dados_mensagem):
        media_id = str(
            dados_mensagem.get('id')
            or (dados_mensagem.get('audio') or {}).get('id')
            or ''
        )
        if not media_id:
            raise GatewayWhatsAppError('A mensagem oficial não informou o ID da mídia.')
        metadados = self._request('GET', f'/{quote(media_id)}')
        url = metadados.get('url')
        if not url:
            raise GatewayWhatsAppError('A API oficial não devolveu a URL da mídia.')
        try:
            resposta = requests.get(url, headers=self.headers, timeout=30)
            resposta.raise_for_status()
        except requests.RequestException as exc:
            raise GatewayWhatsAppError('Não foi possível baixar a mídia recebida.') from exc
        return {
            'base64': base64.b64encode(resposta.content).decode('ascii'),
            'mimetype': metadados.get('mime_type') or 'audio/ogg',
        }


def cliente_whatsapp(configuracao):
    if configuracao.provedor == configuracao.Provedor.META_CLOUD:
        return MetaCloudClient(configuracao)
    return EvolutionClient(configuracao)


def qr_data_url(dados):
    base64_pronto = dados.get('base64') or (dados.get('qrcode') or {}).get('base64')
    if base64_pronto:
        return base64_pronto if base64_pronto.startswith('data:') else f'data:image/png;base64,{base64_pronto}'
    codigo = dados.get('code') or (dados.get('qrcode') or {}).get('code')
    if not codigo:
        return ''
    imagem = qrcode.make(codigo)
    arquivo = io.BytesIO()
    imagem.save(arquivo, format='PNG')
    import base64
    return f'data:image/png;base64,{base64.b64encode(arquivo.getvalue()).decode("ascii")}'


def identidade_instancia(dados):
    """Extrai número e nome sem depender de uma única versão da Evolution API."""
    if isinstance(dados, list):
        dados = dados[0] if dados else {}
    if not isinstance(dados, dict):
        return '', ''
    instancia = dados.get('instance') if isinstance(dados.get('instance'), dict) else dados
    numero = (
        instancia.get('ownerJid')
        or instancia.get('wuid')
        or instancia.get('number')
        or dados.get('ownerJid')
        or ''
    )
    numero = ''.join(filter(str.isdigit, str(numero).split('@')[0]))[:24]
    nome = (
        instancia.get('profileName')
        or instancia.get('name')
        or dados.get('profileName')
        or ''
    )
    return numero, str(nome)[:120]
