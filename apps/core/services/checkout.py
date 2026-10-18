"""Resolução segura da configuração do checkout entre Central e banco operacional."""

import hashlib
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.core import signing
from django.db import DEFAULT_DB_ALIAS
from django.views.decorators.debug import sensitive_variables


_SESSION_KEY_BUSCA_NOME = 'checkout_busca_nome_autorizacao'
_SESSION_KEY_DESCONTO = 'checkout_desconto_autorizacao'
_TOKEN_SALT_DESCONTO_MOBILE = 'ited.checkout.desconto.mobile'
_TOKEN_MAX_AGE_DESCONTO_MOBILE = 10 * 60


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
            .only(
                'checkout_venda_ativo', 'balanca_ean_prefixo',
                'balanca_ean_plu_digitos', 'balanca_ean_conteudo',
                'checkout_desconto_percentual_1', 'checkout_desconto_percentual_2',
                'checkout_desconto_percentual_3', 'checkout_arredondamento_maximo',
            )
            .first()
        )

    database_alias = getattr(getattr(filial, '_state', None), 'db', None) or DEFAULT_DB_ALIAS
    return (
        ParametrosSistema.objects.using(database_alias)
        .filter(filial_id=filial.pk)
        .only(
            'checkout_venda_ativo', 'balanca_ean_prefixo',
            'balanca_ean_plu_digitos', 'balanca_ean_conteudo',
            'checkout_desconto_percentual_1', 'checkout_desconto_percentual_2',
            'checkout_desconto_percentual_3', 'checkout_arredondamento_maximo',
        )
        .first()
    )


def checkout_venda_ativo(request) -> bool:
    """Retorna a flag salva pela Central para a filial ativa."""
    parametros = _parametros_checkout(request)
    return bool(parametros and parametros.checkout_venda_ativo)


def configuracao_ean_balanca(request) -> dict:
    """Retorna a composição da etiqueta Prix/MGV7 configurada para a filial."""
    parametros = _parametros_checkout(request)
    return {
        'prefixo': getattr(parametros, 'balanca_ean_prefixo', None) or '20',
        'plu_digitos': getattr(parametros, 'balanca_ean_plu_digitos', None) or 5,
        'conteudo': getattr(parametros, 'balanca_ean_conteudo', None) or 'preco_total',
    }


def configuracao_descontos_checkout(request) -> dict:
    """Política de descontos livres configurada para a filial do Checkout."""
    parametros = _parametros_checkout(request)
    percentuais = sorted({
        float(valor)
        for valor in (
            getattr(parametros, 'checkout_desconto_percentual_1', 0) or 0,
            getattr(parametros, 'checkout_desconto_percentual_2', 0) or 0,
            getattr(parametros, 'checkout_desconto_percentual_3', 0) or 0,
        )
        if Decimal(str(valor)) > 0
    })
    arredondamento = max(
        Decimal('0.00'),
        Decimal(str(getattr(parametros, 'checkout_arredondamento_maximo', 0) or 0)),
    )
    return {
        'percentuais': percentuais,
        'percentual_maximo': max(percentuais, default=0),
        'arredondamento_maximo': float(arredondamento),
    }


def desconto_checkout_requer_autorizacao(request, venda) -> bool:
    """Valida a política contra os valores finais recalculados no servidor."""
    desconto_itens = sum(
        (Decimal(item.desconto_valor or 0) for item in venda.itens.all()),
        Decimal('0.00'),
    )
    desconto_geral = Decimal(venda.valor_desconto or 0)
    return desconto_checkout_valores_requer_autorizacao(
        request,
        subtotal=Decimal(venda.valor_subtotal or 0),
        desconto_geral=desconto_geral,
        desconto_itens=desconto_itens,
        total=Decimal(venda.valor_total or 0),
    )


def desconto_checkout_valores_requer_autorizacao(
    request,
    *,
    subtotal: Decimal,
    desconto_geral: Decimal,
    desconto_itens: Decimal = Decimal('0.00'),
    total: Decimal | None = None,
) -> bool:
    """Aplica a mesma política do Checkout a valores ainda não persistidos."""
    desconto_geral = Decimal(desconto_geral or 0)
    desconto_itens = Decimal(desconto_itens or 0)
    desconto_total = desconto_itens + desconto_geral
    if desconto_total <= 0:
        return False

    configuracao = configuracao_descontos_checkout(request)
    percentual_maximo = Decimal(str(configuracao['percentual_maximo'] or 0))
    subtotal = Decimal(subtotal or 0)
    bruto = subtotal + desconto_itens
    limite_percentual = (bruto * percentual_maximo / Decimal('100')).quantize(
        Decimal('0.01'),
        rounding=ROUND_HALF_UP,
    )
    if percentual_maximo > 0 and desconto_total <= limite_percentual:
        return False

    limite_arredondamento = Decimal(str(configuracao['arredondamento_maximo'] or 0))
    excesso = max(Decimal('0.00'), desconto_total - limite_percentual)
    total = Decimal(total if total is not None else subtotal - desconto_geral)
    arredondamento_valido = bool(
        limite_arredondamento > 0
        and desconto_geral > 0
        and total == total.quantize(Decimal('1'))
        and excesso <= limite_arredondamento
    )
    return not arredondamento_valido


def _escopo_busca_nome(request) -> str:
    filial = getattr(request, 'filial_ativa', None)
    tenant_alias = getattr(request, 'tenant_db_alias', None)
    database_alias = getattr(getattr(filial, '_state', None), 'db', None) or DEFAULT_DB_ALIAS
    identificador_filial = getattr(filial, 'cnpj', None) or getattr(filial, 'pk', '')
    return f'{tenant_alias or database_alias}:{identificador_filial}'


def _banco_operacional(request) -> str:
    filial = getattr(request, 'filial_ativa', None)
    return (
        getattr(getattr(filial, '_state', None), 'db', None)
        or getattr(request, 'tenant_db_alias', None)
        or DEFAULT_DB_ALIAS
    )


def _versao_senha(senha_hash: str) -> str:
    return hashlib.sha256((senha_hash or '').encode('utf-8')).hexdigest()


def _autorizador_elegivel(request, usuario) -> bool:
    filial = getattr(request, 'filial_ativa', None)
    if not usuario or not filial or not usuario.is_active:
        return False
    if usuario.is_superuser:
        return True
    if usuario.empresa_id != filial.empresa_id:
        return False
    if not usuario.pode_acessar_filial(filial):
        return False
    perfil = usuario.perfil_para_filial(filial)
    if not perfil or not perfil.ativo:
        return False
    usuario._perfil_ativo = perfil
    return usuario.tem_permissao('pdv', 'aprovar')


def usuarios_autorizadores_checkout(request) -> list[dict]:
    """Lista aprovadores locais e superusuários do diretório central."""
    from apps.core.models import Usuario

    banco_operacional = _banco_operacional(request)
    usuarios_locais = (
        Usuario.objects.using(banco_operacional).select_related('perfil')
        .filter(
            empresa_id=request.filial_ativa.empresa_id,
            ativo=True,
            is_superuser=False,
        )
    )
    superusuarios = Usuario.objects.using(DEFAULT_DB_ALIAS).filter(
        is_superuser=True,
        ativo=True,
    )
    autorizadores = [
        {
            'id': f'usuario:{usuario.pk}',
            'nome': usuario.nome or usuario.email,
            'email': usuario.email,
        }
        for usuario in usuarios_locais
        if _autorizador_elegivel(request, usuario)
    ]
    autorizadores.extend({
        'id': f'super:{usuario.pk}',
        'nome': usuario.nome or usuario.email,
        'email': usuario.email,
    } for usuario in superusuarios)
    return sorted(
        autorizadores,
        key=lambda item: ((item['nome'] or '').casefold(), item['email'].casefold()),
    )


@sensitive_variables('senha')
def validar_autorizador_checkout_busca_nome(request, usuario_id, senha: str):
    """Valida as credenciais de um usuário com Aprovar no PDV da filial."""
    from apps.core.models import Usuario

    referencia = str(usuario_id or '')
    tipo, separador, valor_id = referencia.partition(':')
    if not separador:
        tipo, valor_id = 'usuario', referencia
    try:
        usuario_pk = int(valor_id)
    except (TypeError, ValueError):
        return None
    if not senha or len(senha) > 128:
        return None
    if tipo == 'super':
        banco = DEFAULT_DB_ALIAS
        filtro = {'pk': usuario_pk, 'is_superuser': True, 'ativo': True}
    elif tipo == 'usuario':
        banco = _banco_operacional(request)
        filtro = {
            'pk': usuario_pk,
            'empresa_id': request.filial_ativa.empresa_id,
            'ativo': True,
            'is_superuser': False,
        }
    else:
        return None
    autorizador = (
        Usuario.objects.using(banco).select_related('perfil')
        .filter(**filtro)
        .first()
    )
    if not _autorizador_elegivel(request, autorizador):
        return None
    if not autorizador.check_password(senha):
        return None
    return autorizador


@sensitive_variables('senha')
def validar_autorizador_checkout_por_email(request, email: str, senha: str):
    """Valida um aprovador pelo e-mail, adequado a clientes móveis."""
    from apps.core.models import Usuario

    email = str(email or '').strip().lower()
    if not email or not senha or len(email) > 254 or len(senha) > 128:
        return None

    banco_operacional = _banco_operacional(request)
    candidatos = []
    local = (
        Usuario.objects.using(banco_operacional).select_related('perfil')
        .filter(
            email__iexact=email,
            empresa_id=request.filial_ativa.empresa_id,
            ativo=True,
        )
        .first()
    )
    if local:
        candidatos.append(local)
    if banco_operacional != DEFAULT_DB_ALIAS:
        central = (
            Usuario.objects.using(DEFAULT_DB_ALIAS).select_related('perfil')
            .filter(email__iexact=email, is_superuser=True, ativo=True)
            .first()
        )
        if central:
            candidatos.append(central)

    for autorizador in candidatos:
        if _autorizador_elegivel(request, autorizador) and autorizador.check_password(senha):
            return autorizador
    return None


def gerar_token_desconto_checkout_mobile(request, autorizador) -> str:
    """Emite aprovação curta, vinculada à filial e à versão atual da senha."""
    return signing.dumps(
        {
            'escopo': _escopo_busca_nome(request),
            'usuario_id': autorizador.pk,
            'banco': autorizador._state.db or DEFAULT_DB_ALIAS,
            'versao': _versao_senha(autorizador.password),
        },
        salt=_TOKEN_SALT_DESCONTO_MOBILE,
        compress=True,
    )


def token_desconto_checkout_mobile_valido(request, token: str) -> bool:
    """Confirma uma aprovação móvel recente e vinculada ao contexto atual."""
    if not token or len(token) > 2000:
        return False
    try:
        autorizacao = signing.loads(
            token,
            salt=_TOKEN_SALT_DESCONTO_MOBILE,
            max_age=_TOKEN_MAX_AGE_DESCONTO_MOBILE,
        )
    except signing.BadSignature:
        return False
    if autorizacao.get('escopo') != _escopo_busca_nome(request):
        return False

    from apps.core.models import Usuario

    banco = autorizacao.get('banco') or _banco_operacional(request)
    if banco not in {DEFAULT_DB_ALIAS, _banco_operacional(request)}:
        return False
    autorizador = (
        Usuario.objects.using(banco).select_related('perfil')
        .filter(pk=autorizacao.get('usuario_id'), ativo=True)
        .first()
    )
    return bool(
        _autorizador_elegivel(request, autorizador)
        and autorizacao.get('versao') == _versao_senha(autorizador.password)
    )


def autorizar_checkout_busca_nome(request, autorizador) -> None:
    """Autoriza a seleção de um único produto pesquisado por nome."""
    request.session[_SESSION_KEY_BUSCA_NOME] = {
        'escopo': _escopo_busca_nome(request),
        'usuario_id': autorizador.pk,
        'banco': autorizador._state.db or DEFAULT_DB_ALIAS,
        'versao': _versao_senha(autorizador.password),
    }


def autorizar_checkout_desconto(request, autorizador) -> None:
    """Autoriza um desconto na venda atual do checkout."""
    request.session[_SESSION_KEY_DESCONTO] = {
        'escopo': _escopo_busca_nome(request),
        'usuario_id': autorizador.pk,
        'banco': autorizador._state.db or DEFAULT_DB_ALIAS,
        'versao': _versao_senha(autorizador.password),
    }


def checkout_busca_nome_liberada(request) -> bool:
    """Confirma a autorização de uso único vinculada à filial e à senha atual."""
    autorizacao = request.session.get(_SESSION_KEY_BUSCA_NOME) or {}
    if autorizacao.get('escopo') != _escopo_busca_nome(request):
        return False
    from apps.core.models import Usuario

    banco = autorizacao.get('banco') or _banco_operacional(request)
    if banco not in {DEFAULT_DB_ALIAS, _banco_operacional(request)}:
        request.session.pop(_SESSION_KEY_BUSCA_NOME, None)
        return False
    autorizador = (
        Usuario.objects.using(banco).select_related('perfil')
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


def checkout_desconto_liberado(request) -> bool:
    """Confirma a aprovação de desconto vinculada à filial e à senha atual."""
    autorizacao = request.session.get(_SESSION_KEY_DESCONTO) or {}
    if autorizacao.get('escopo') != _escopo_busca_nome(request):
        return False
    from apps.core.models import Usuario

    banco = autorizacao.get('banco') or _banco_operacional(request)
    if banco not in {DEFAULT_DB_ALIAS, _banco_operacional(request)}:
        request.session.pop(_SESSION_KEY_DESCONTO, None)
        return False
    autorizador = (
        Usuario.objects.using(banco).select_related('perfil')
        .filter(pk=autorizacao.get('usuario_id'), ativo=True)
        .first()
    )
    liberada = bool(
        _autorizador_elegivel(request, autorizador)
        and autorizacao.get('versao') == _versao_senha(autorizador.password)
    )
    if not liberada:
        request.session.pop(_SESSION_KEY_DESCONTO, None)
    return liberada


def encerrar_checkout_busca_nome(request) -> None:
    """Revoga qualquer autorização ainda não consumida."""
    request.session.pop(_SESSION_KEY_BUSCA_NOME, None)


def encerrar_checkout_desconto(request) -> None:
    """Revoga a aprovação de desconto da venda atual."""
    request.session.pop(_SESSION_KEY_DESCONTO, None)


def consumir_checkout_busca_nome(request) -> bool:
    """Consome de forma definitiva a autorização depois da escolha do item."""
    if not checkout_busca_nome_liberada(request):
        return False
    encerrar_checkout_busca_nome(request)
    return True


def consumir_checkout_desconto(request) -> bool:
    """Consome a aprovação quando o checkout finaliza uma venda com desconto."""
    if not checkout_desconto_liberado(request):
        return False
    encerrar_checkout_desconto(request)
    return True
