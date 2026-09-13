"""Resolução segura da configuração do checkout entre Central e banco operacional."""

import hashlib

from django.conf import settings
from django.db import DEFAULT_DB_ALIAS
from django.db.models import Q
from django.views.decorators.debug import sensitive_variables


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
            .only('checkout_venda_ativo')
            .first()
        )

    database_alias = getattr(getattr(filial, '_state', None), 'db', None) or DEFAULT_DB_ALIAS
    return (
        ParametrosSistema.objects.using(database_alias)
        .filter(filial_id=filial.pk)
        .only('checkout_venda_ativo')
        .first()
    )


def checkout_venda_ativo(request) -> bool:
    """Retorna a flag salva pela Central para a filial ativa."""
    parametros = _parametros_checkout(request)
    return bool(parametros and parametros.checkout_venda_ativo)


def _escopo_busca_nome(request) -> str:
    filial = getattr(request, 'filial_ativa', None)
    tenant_alias = getattr(request, 'tenant_db_alias', None)
    database_alias = getattr(getattr(filial, '_state', None), 'db', None) or DEFAULT_DB_ALIAS
    identificador_filial = getattr(filial, 'cnpj', None) or getattr(filial, 'pk', '')
    return f'{tenant_alias or database_alias}:{identificador_filial}'


def _versao_senha(senha_hash: str) -> str:
    return hashlib.sha256((senha_hash or '').encode('utf-8')).hexdigest()


def _autorizador_elegivel(request, usuario) -> bool:
    filial = getattr(request, 'filial_ativa', None)
    if not usuario or not filial or not usuario.is_active:
        return False
    if not (usuario.is_superuser or usuario.empresa_id == filial.empresa_id):
        return False
    if not usuario.pode_acessar_filial(filial):
        return False
    perfil = usuario.perfil_para_filial(filial)
    if not perfil or not perfil.ativo:
        return False
    usuario._perfil_ativo = perfil
    return usuario.tem_permissao('pdv', 'aprovar')


def usuarios_autorizadores_checkout(request) -> list[dict]:
    """Lista os aprovadores ativos que podem atuar na filial do checkout."""
    from apps.core.models import Usuario

    usuarios = (
        Usuario.objects.select_related('perfil')
        .filter(
            Q(empresa_id=request.filial_ativa.empresa_id) | Q(is_superuser=True),
            ativo=True,
        )
        .order_by('nome', 'email')
    )
    return [
        {
            'id': usuario.pk,
            'nome': usuario.nome or usuario.email,
            'email': usuario.email,
        }
        for usuario in usuarios
        if _autorizador_elegivel(request, usuario)
    ]


@sensitive_variables('senha')
def validar_autorizador_checkout_busca_nome(request, usuario_id, senha: str):
    """Valida as credenciais de um usuário com Aprovar no PDV da filial."""
    from apps.core.models import Usuario

    try:
        usuario_id = int(usuario_id)
    except (TypeError, ValueError):
        return None
    if not senha or len(senha) > 128:
        return None
    autorizador = (
        Usuario.objects.select_related('perfil')
        .filter(
            Q(empresa_id=request.filial_ativa.empresa_id) | Q(is_superuser=True),
            pk=usuario_id,
            ativo=True,
        )
        .first()
    )
    if not _autorizador_elegivel(request, autorizador):
        return None
    if not autorizador.check_password(senha):
        return None
    return autorizador


def autorizar_checkout_busca_nome(request, autorizador) -> None:
    """Autoriza a seleção de um único produto pesquisado por nome."""
    request.session[_SESSION_KEY_BUSCA_NOME] = {
        'escopo': _escopo_busca_nome(request),
        'usuario_id': autorizador.pk,
        'versao': _versao_senha(autorizador.password),
    }


def checkout_busca_nome_liberada(request) -> bool:
    """Confirma a autorização de uso único vinculada à filial e à senha atual."""
    autorizacao = request.session.get(_SESSION_KEY_BUSCA_NOME) or {}
    if autorizacao.get('escopo') != _escopo_busca_nome(request):
        return False
    from apps.core.models import Usuario

    autorizador = (
        Usuario.objects.select_related('perfil')
        .filter(pk=autorizacao.get('usuario_id'), ativo=True)
        .first()
    )
    liberada = bool(
        _autorizador_elegivel(request, autorizador)
        and autorizacao.get('versao') == _versao_senha(autorizador.password)
    )
    if not liberada:
        request.session.pop(_SESSION_KEY_BUSCA_NOME, None)
    return liberada


def encerrar_checkout_busca_nome(request) -> None:
    """Revoga qualquer autorização ainda não consumida."""
    request.session.pop(_SESSION_KEY_BUSCA_NOME, None)


def consumir_checkout_busca_nome(request) -> bool:
    """Consome de forma definitiva a autorização depois da escolha do item."""
    if not checkout_busca_nome_liberada(request):
        return False
    encerrar_checkout_busca_nome(request)
    return True
