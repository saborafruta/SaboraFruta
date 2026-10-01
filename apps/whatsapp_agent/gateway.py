import io
from urllib.parse import quote

import qrcode
import requests
from django.conf import settings


class GatewayWhatsAppError(Exception):
    pass


class EvolutionClient:
    EVENTOS = ['QRCODE_UPDATED', 'CONNECTION_UPDATE', 'MESSAGES_UPSERT', 'MESSAGES_UPDATE']

    def __init__(self, configuracao):
        self.configuracao = configuracao
        self.base_url = (
            getattr(settings, 'WHATSAPP_EVOLUTION_URL', '') or configuracao.gateway_url
        ).rstrip('/')
        self.api_key = (
            getattr(settings, 'WHATSAPP_EVOLUTION_API_KEY', '') or configuracao.obter_api_key()
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
            'enabled': True,
            'url': webhook_url,
            'webhookByEvents': False,
            'webhookBase64': False,
            'events': self.EVENTOS,
        })

    def estado(self):
        dados = self._request('GET', f'/instance/connectionState/{quote(self.configuracao.instancia)}')
        return (dados.get('instance') or {}).get('state', 'close')

    def conectar(self):
        return self._request('GET', f'/instance/connect/{quote(self.configuracao.instancia)}')

    def enviar_texto(self, telefone, texto):
        return self._request('POST', f'/message/sendText/{quote(self.configuracao.instancia)}', json={
            'number': telefone,
            'text': texto,
            'delay': 700,
            'linkPreview': False,
        })


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
