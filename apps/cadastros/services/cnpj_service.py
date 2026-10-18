"""Consulta de pessoa jurídica na BrasilAPI, com retorno normalizado."""
from __future__ import annotations

import logging

import requests
from django.conf import settings
from django.core.cache import cache

from apps.core.services.exceptions import DadosInvalidosError

logger = logging.getLogger(__name__)


class CnpjService:
    TIMEOUT = 8
    CACHE_TTL = 60 * 60 * 24

    @classmethod
    def consultar(cls, cnpj: str) -> dict | None:
        documento = ''.join(filter(str.isdigit, str(cnpj or '')))
        if len(documento) != 14:
            raise DadosInvalidosError('CNPJ deve conter 14 dígitos.')

        cache_key = f'brasilapi:cnpj:{documento}'
        cached = cache.get(cache_key)
        if cached is not None:
            return cached if cached else None

        try:
            response = requests.get(
                settings.BRASILAPI_CNPJ_URL.format(cnpj=documento),
                timeout=cls.TIMEOUT,
            )
            if response.status_code == 404:
                cache.set(cache_key, {}, cls.CACHE_TTL)
                return None
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            logger.warning('Falha ao consultar BrasilAPI para CNPJ %s: %s', documento, exc)
            return None
        except ValueError:
            logger.warning('Resposta inválida da BrasilAPI para CNPJ %s', documento)
            return None

        tipo_logradouro = str(data.get('descricao_tipo_de_logradouro') or '').strip()
        logradouro = str(data.get('logradouro') or '').strip()
        endereco = ' '.join(parte for parte in (tipo_logradouro, logradouro) if parte)
        resultado = {
            'cnpj': documento,
            'razao_social': data.get('razao_social') or '',
            'nome_fantasia': data.get('nome_fantasia') or '',
            'inscricao_estadual': data.get('inscricao_estadual') or '',
            'endereco': endereco,
            'numero': data.get('numero') or '',
            'complemento': data.get('complemento') or '',
            'bairro': data.get('bairro') or '',
            'cidade': data.get('municipio') or '',
            'uf': data.get('uf') or '',
            'cep': ''.join(filter(str.isdigit, str(data.get('cep') or ''))),
            'codigo_municipio_ibge': str(
                data.get('codigo_municipio_ibge') or data.get('codigo_municipio') or ''
            ),
            'telefone': data.get('ddd_telefone_1') or '',
            'email': data.get('email') or '',
            'situacao_cadastral': data.get('descricao_situacao_cadastral') or '',
        }
        cache.set(cache_key, resultado, cls.CACHE_TTL)
        return resultado
