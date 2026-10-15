import base64
import json
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

import requests

from .models import UsoIAWhatsApp


class IAWhatsAppError(Exception):
    pass


PRECOS = {
    # USD por 1 milhão de tokens. A estimativa é gravada junto ao uso para
    # preservar o histórico mesmo que os preços sejam atualizados depois.
    'gpt-4o-mini': {'entrada': Decimal('0.15'), 'cache': Decimal('0.075'), 'saida': Decimal('0.60')},
    'gpt-4o-mini-transcribe': {
        'entrada': Decimal('1.25'), 'cache': Decimal('0'), 'saida': Decimal('5.00'),
        'minuto': Decimal('0.003'),
    },
}


@dataclass
class ResultadoIA:
    conteudo: dict | str
    uso: UsoIAWhatsApp


class OpenAIWhatsAppClient:
    base_url = 'https://api.openai.com/v1'

    def __init__(self, configuracao):
        self.configuracao = configuracao
        self.api_key = configuracao.obter_ia_api_key()
        if not self.api_key:
            raise IAWhatsAppError('Cadastre uma chave da OpenAI para ativar o agente com IA.')

    @property
    def headers(self):
        return {'Authorization': f'Bearer {self.api_key}'}

    def _json(self, resposta):
        if resposta.status_code >= 400:
            try:
                detalhe = (resposta.json().get('error') or {}).get('message')
            except (ValueError, AttributeError):
                detalhe = None
            raise IAWhatsAppError(
                str(detalhe or f'A OpenAI respondeu HTTP {resposta.status_code}.')[:500],
            )
        try:
            return resposta.json()
        except ValueError as exc:
            raise IAWhatsAppError('A OpenAI retornou uma resposta inválida.') from exc

    def _registrar_uso(self, *, conversa, mensagem, tipo, modelo, usage, metadados=None):
        detalhes = usage.get('input_tokens_details') or usage.get('input_token_details') or {}
        entrada = int(usage.get('input_tokens') or 0)
        cache = int(detalhes.get('cached_tokens') or 0)
        saida = int(usage.get('output_tokens') or 0)
        total = int(usage.get('total_tokens') or entrada + saida)
        segundos = Decimal(str(usage.get('seconds') or 0))
        precos = PRECOS.get(modelo, PRECOS['gpt-4o-mini'])
        if segundos and precos.get('minuto'):
            custo_usd = segundos / Decimal('60') * precos['minuto']
        else:
            entrada_cobrada = max(entrada - cache, 0)
            custo_usd = (
                Decimal(entrada_cobrada) * precos['entrada']
                + Decimal(cache) * precos.get('cache', precos['entrada'])
                + Decimal(saida) * precos['saida']
            ) / Decimal('1000000')
        cotacao = Decimal(str(self.configuracao.ia_cotacao_dolar))
        custo_usd = custo_usd.quantize(Decimal('0.00000001'), rounding=ROUND_HALF_UP)
        custo_brl = (custo_usd * cotacao).quantize(
            Decimal('0.00000001'), rounding=ROUND_HALF_UP,
        )
        return UsoIAWhatsApp.objects.create(
            filial=self.configuracao.filial,
            configuracao=self.configuracao,
            conversa=conversa,
            mensagem=mensagem,
            tipo=tipo,
            modelo=modelo,
            tokens_entrada=entrada,
            tokens_entrada_cache=cache,
            tokens_saida=saida,
            tokens_total=total,
            segundos_audio=segundos,
            custo_usd=custo_usd,
            cotacao_dolar=cotacao,
            custo_brl=custo_brl,
            metadados=metadados or {},
        )

    def responder_estruturado(self, *, conversa, mensagem, instrucoes, entrada, schema):
        modelo = self.configuracao.ia_modelo
        payload = {
            'model': modelo,
            'instructions': instrucoes,
            'input': entrada,
            'max_output_tokens': 700,
            'text': {
                'format': {
                    'type': 'json_schema',
                    'name': 'decisao_atendimento_whatsapp',
                    'strict': True,
                    'schema': schema,
                },
            },
            'safety_identifier': f'wa_{conversa.pk}',
        }
        try:
            resposta = requests.post(
                f'{self.base_url}/responses',
                headers={**self.headers, 'Content-Type': 'application/json'},
                json=payload,
                timeout=35,
            )
        except requests.RequestException as exc:
            raise IAWhatsAppError('Não foi possível acessar a OpenAI agora.') from exc
        dados = self._json(resposta)
        textos = [
            parte.get('text', '')
            for item in dados.get('output') or []
            if item.get('type') == 'message'
            for parte in item.get('content') or []
            if parte.get('type') == 'output_text'
        ]
        try:
            conteudo = json.loads(''.join(textos))
        except (TypeError, json.JSONDecodeError) as exc:
            raise IAWhatsAppError('A IA não conseguiu estruturar a resposta.') from exc
        uso = self._registrar_uso(
            conversa=conversa,
            mensagem=mensagem,
            tipo=UsoIAWhatsApp.Tipo.RESPOSTA,
            modelo=modelo,
            usage=dados.get('usage') or {},
            metadados={'response_id': dados.get('id', '')},
        )
        return ResultadoIA(conteudo=conteudo, uso=uso)

    def transcrever(self, *, conversa, mensagem, base64_audio, mimetype='audio/ogg'):
        modelo = self.configuracao.ia_modelo_transcricao
        try:
            conteudo = base64.b64decode(base64_audio, validate=False)
        except (ValueError, TypeError) as exc:
            raise IAWhatsAppError('O áudio recebido está corrompido.') from exc
        if not conteudo or len(conteudo) > 25 * 1024 * 1024:
            raise IAWhatsAppError('O áudio está vazio ou excede o limite de 25 MB.')
        extensao = {
            'audio/ogg': 'ogg', 'audio/opus': 'ogg', 'audio/mpeg': 'mp3',
            'audio/mp4': 'm4a', 'audio/wav': 'wav', 'audio/webm': 'webm',
        }.get((mimetype or '').split(';')[0], 'ogg')
        try:
            resposta = requests.post(
                f'{self.base_url}/audio/transcriptions',
                headers=self.headers,
                files={'file': (f'audio.{extensao}', conteudo, mimetype or 'audio/ogg')},
                data={
                    'model': modelo,
                    'response_format': 'json',
                    'prompt': 'Conversa comercial em português do Brasil sobre agenda, serviços e pedidos.',
                },
                timeout=60,
            )
        except requests.RequestException as exc:
            raise IAWhatsAppError('Não foi possível transcrever o áudio agora.') from exc
        dados = self._json(resposta)
        texto = str(dados.get('text') or '').strip()
        if not texto:
            raise IAWhatsAppError('Não consegui identificar fala no áudio.')
        uso = self._registrar_uso(
            conversa=conversa,
            mensagem=mensagem,
            tipo=UsoIAWhatsApp.Tipo.TRANSCRICAO,
            modelo=modelo,
            usage=dados.get('usage') or {},
        )
        return ResultadoIA(conteudo=texto, uso=uso)
