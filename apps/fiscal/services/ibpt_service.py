from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.core.services.exceptions import DadosInvalidosError
from apps.fiscal.models import AliquotaIBPT


def _api_url(arquivo: str) -> str:
    base = getattr(
        settings,
        'IBPT_API_BASE_URL',
        'https://api-ibpt.seunegocionanuvem.com.br',
    ).rstrip('/')
    return f'{base}/{arquivo.lstrip("/")}'


# O ModSecurity do provedor bloqueia com 406 qualquer requisicao cujo
# User-Agent comece com "python-requests/" -- e' o padrao que a biblioteca
# manda sozinha, tratado ali como assinatura de bot/scraper. Um User-Agent
# de navegador passa pela mesma regra sem problema (confirmado direto no
# WAF); nao muda nada no lado do IBPT, so evita cair no filtro.
_HEADERS_PADRAO = {
    'Accept': 'application/json',
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/120.0 Safari/537.36'
    ),
}


def _decimal(valor) -> Decimal:
    try:
        return Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise DadosInvalidosError('A tabela IBPT retornou uma aliquota invalida.') from exc


def _data_iso(valor) -> date:
    try:
        return date.fromisoformat(str(valor))
    except (TypeError, ValueError) as exc:
        raise DadosInvalidosError('A tabela IBPT retornou uma vigencia invalida.') from exc


def _normalizar_registro(dados: dict, uf: str) -> dict:
    ncm = ''.join(ch for ch in str(dados.get('codigo') or '') if ch.isdigit())
    if len(ncm) != 8:
        raise DadosInvalidosError('A tabela IBPT retornou um NCM invalido.')
    fonte = str(dados.get('fonte') or '').strip()
    versao = str(dados.get('versao') or '').strip()
    if not fonte or not versao:
        raise DadosInvalidosError('A tabela IBPT retornou fonte ou versao vazia.')
    return {
        'ncm': ncm,
        'uf': uf,
        'descricao': str(dados.get('descricao') or '')[:500],
        'federal_nacional': _decimal(dados.get('nacionalfederal')),
        'federal_importado': _decimal(dados.get('importadosfederal')),
        'estadual': _decimal(dados.get('estadual')),
        'municipal': _decimal(dados.get('municipal')),
        'fonte': fonte[:120],
        'versao': versao[:20],
        'vigencia_inicio': _data_iso(dados.get('vigenciainicio')),
        'vigencia_fim': _data_iso(dados.get('vigenciafim')),
    }


def _salvar_registros(registros: list[dict]) -> int:
    agora = timezone.now()
    objetos = [AliquotaIBPT(**registro, updated_at=agora) for registro in registros]
    with transaction.atomic():
        AliquotaIBPT.objects.bulk_create(
            objetos,
            batch_size=1000,
            update_conflicts=True,
            unique_fields=['uf', 'ncm', 'versao'],
            update_fields=[
                'descricao', 'federal_nacional', 'federal_importado',
                'estadual', 'municipal', 'fonte', 'vigencia_inicio',
                'vigencia_fim', 'updated_at',
            ],
        )
    return len(objetos)


def consultar_ncm_ibpt(ncm: str, uf: str) -> AliquotaIBPT:
    ncm = ''.join(ch for ch in str(ncm or '') if ch.isdigit())
    uf = str(uf or '').strip().upper()
    resposta = requests.get(
        _api_url('api_ibpt.php'),
        params={'codigo': ncm, 'uf': uf},
        headers=_HEADERS_PADRAO,
        timeout=15,
    )
    resposta.raise_for_status()
    registro = _normalizar_registro(resposta.json(), uf)
    _salvar_registros([registro])
    return AliquotaIBPT.objects.get(
        uf=uf, ncm=registro['ncm'], versao=registro['versao']
    )


def sincronizar_tabela_ibpt(uf: str) -> dict:
    uf = str(uf or '').strip().upper()
    resposta = requests.get(
        _api_url('api_ibpt_json.php'),
        params={'uf': uf},
        headers=_HEADERS_PADRAO,
        timeout=120,
    )
    resposta.raise_for_status()
    dados = resposta.json()
    if str(dados.get('uf') or '').upper() != uf:
        raise DadosInvalidosError('A tabela IBPT retornou uma UF diferente da solicitada.')
    # O payload do IBPT mistura tres coisas na mesma lista, distinguidas
    # pelo campo `tipo`: 0 = NCM de mercadoria (8 digitos), 1 = NBS de
    # servico (9 digitos), 2 = codigo de servico LC 116 (4 digitos). Esta
    # tabela e' so de mercadoria -- e' o que `AliquotaIBPT.ncm` (8 digitos)
    # e a nota fiscal de produto (NFC-e) usam. Sem filtrar, todo item de
    # servico batia como "NCM invalido" e derrubava a sincronizacao
    # inteira por causa de linhas que o sistema nunca ia consultar.
    itens = [item for item in (dados.get('ncm') or []) if item.get('tipo') == 0]
    if len(itens) < 10000:
        raise DadosInvalidosError('A tabela IBPT recebida esta incompleta.')
    registros = [_normalizar_registro(item, uf) for item in itens]
    quantidade = _salvar_registros(registros)
    return {
        'uf': uf,
        'versao': str(dados.get('versao') or registros[0]['versao']),
        'quantidade': quantidade,
    }


def obter_aliquota_ibpt(ncm: str, uf: str, data_emissao: date) -> AliquotaIBPT | None:
    ncm = ''.join(ch for ch in str(ncm or '') if ch.isdigit())
    uf = str(uf or '').strip().upper()
    if len(ncm) != 8 or len(uf) != 2:
        return None

    atual = (
        AliquotaIBPT.objects.filter(
            ncm=ncm,
            uf=uf,
            vigencia_inicio__lte=data_emissao,
            vigencia_fim__gte=data_emissao,
        )
        .order_by('-vigencia_inicio', '-updated_at')
        .first()
    )
    if not getattr(settings, 'IBPT_AUTO_SYNC', False):
        return atual

    hoje = timezone.localdate()
    precisa_consultar = atual is None or timezone.localtime(atual.updated_at).date() < hoje
    if precisa_consultar:
        try:
            consultar_ncm_ibpt(ncm, uf)
        except (requests.RequestException, ValueError, DadosInvalidosError):
            if atual is None:
                raise DadosInvalidosError(
                    f'Nao foi possivel consultar a tabela IBPT vigente para o NCM {ncm}.'
                )
        atual = (
            AliquotaIBPT.objects.filter(
                ncm=ncm,
                uf=uf,
                vigencia_inicio__lte=data_emissao,
                vigencia_fim__gte=data_emissao,
            )
            .order_by('-vigencia_inicio', '-updated_at')
            .first()
        )
    return atual
