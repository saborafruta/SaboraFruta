"""Resolução segura da configuração do checkout entre Central e banco operacional."""

import hashlib
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password
from django.db import DEFAULT_DB_ALIAS
from django.utils import timezone


AUTORIZACAO_BUSCA_NOME_MINUTOS = 30
_SESSION_KEY_BUSCA_NOME = 'checkout_busca_nome_autorizacao'


def _parametros_checkout(request):
    """Localiza na Central os parâmetros da filial operacional ativa."""
    from apps.core.models import EmpresaBanco, Filial, ParametrosSistema

    filial = getattr(request, 'filial_ativa', None)
    if filial is None:
        return None

    tenant_alias = getattr(request, 'tenant_db_alias', None)
    if getattr(settings, 'TENANT_DATABASE_ROUTING_ENABLED', False) and tenant_alias:
        try:
            banco = (
                EmpresaBanco.objects.using(DEFAULT_DB_ALIAS)
                .only('empresa_id')
                .get(
                    db_alias=tenant_alias,
                    ativo=True,
                    status=EmpresaBanco.Status.ATIVO,
                )
            )
        except EmpresaBanco.DoesNotExist:
            return None
        filial_central_id = (
            Filial.objects.using(DEFAULT_DB_ALIAS)
            .filter(
                empresa_id=banco.empresa_id,
                cnpj=filial.cnpj,
                ativo=True,
            )
            .values_list('pk', flat=True)
            .first()
        )
        if not filial_central_id:
            return None
        return (
            ParametrosSistema.objects.using(DEFAULT_DB_ALIAS)
            .filter(filial_id=filial_central_id)
            .only('checkout_venda_ativo', 'checkout_busca_nome_senha_hash')
            .first()
        )

    database_alias = getattr(getattr(filial, '_state', None), 'db', None) or DEFAULT_DB_ALIAS
    return (
        ParametrosSistema.objects.using(database_alias)
        .filter(filial_id=filial.pk)
        .only('checkout_venda_ativo', 'checkout_busca_nome_senha_hash')
        .first()
    )


def checkout_venda_ativo(request) -> bool:
    """Retorna a flag salva pela Central para a filial ativa."""
    parametros = _parametros_checkout(request)
    return bool(parametros and parametros.checkout_venda_ativo)


def checkout_busca_nome_configurada(request) -> bool:
    """Informa sem expor o segredo se a filial cadastrou a senha de liberação."""
    parametros = _parametros_checkout(request)
    return bool(parametros and parametros.checkout_busca_nome_senha_hash)


def _escopo_busca_nome(request) -> str:
    filial = getattr(request, 'filial_ativa', None)
    tenant_alias = getattr(request, 'tenant_db_alias', None)
    database_alias = getattr(getattr(filial, '_state', None), 'db', None) or DEFAULT_DB_ALIAS
    identificador_filial = getattr(filial, 'cnpj', None) or getattr(filial, 'pk', '')
    return f'{tenant_alias or database_alias}:{identificador_filial}'


def _versao_senha(senha_hash: str) -> str:
    return hashlib.sha256(senha_hash.encode('utf-8')).hexdigest()


def validar_senha_checkout_busca_nome(request, senha: str) -> bool:
    """Valida a senha configurada na Central sem enviá-la ao navegador."""
    parametros = _parametros_checkout(request)
    senha_hash = parametros.checkout_busca_nome_senha_hash if parametros else ''
    return bool(senha_hash and senha and check_password(senha, senha_hash))


def autorizar_checkout_busca_nome(request) -> None:
    """Autoriza por 30 minutos a filial atual na sessão autenticada."""
    parametros = _parametros_checkout(request)
    if not parametros or not parametros.checkout_busca_nome_senha_hash:
        return
    request.session[_SESSION_KEY_BUSCA_NOME] = {
        'escopo': _escopo_busca_nome(request),
        'versao': _versao_senha(parametros.checkout_busca_nome_senha_hash),
        'expira_em': (timezone.now() + timedelta(minutes=AUTORIZACAO_BUSCA_NOME_MINUTOS)).timestamp(),
    }


def checkout_busca_nome_liberada(request) -> bool:
    """Confirma a autorização vigente, vinculada à filial e à senha atual."""
    autorizacao = request.session.get(_SESSION_KEY_BUSCA_NOME) or {}
    try:
        expira_em = float(autorizacao.get('expira_em', 0))
    except (TypeError, ValueError):
        return False
    if autorizacao.get('escopo') != _escopo_busca_nome(request):
        return False
    if expira_em <= timezone.now().timestamp():
        request.session.pop(_SESSION_KEY_BUSCA_NOME, None)
        return False
    parametros = _parametros_checkout(request)
    senha_hash = parametros.checkout_busca_nome_senha_hash if parametros else ''
    return bool(
        senha_hash
        and autorizacao.get('versao') == _versao_senha(senha_hash)
    )
