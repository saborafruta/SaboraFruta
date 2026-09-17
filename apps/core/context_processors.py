"""Context processors: disponibilizam dados em todos os templates."""
import base64
import hashlib
import hmac
import json
import time

from django.conf import settings

from apps.core.services.checkout import checkout_venda_ativo as checkout_venda_habilitado
from apps.core.services.modulos import modulos_ativos


def orla_widget_host_enabled(request):
    """Confirma se o Widget foi habilitado para o host atual."""
    if not getattr(settings, 'ORLA_WIDGET_ENABLED', False):
        return False
    allowed_hosts = {
        str(host).strip().lower()
        for host in getattr(settings, 'ORLA_WIDGET_ALLOWED_HOSTS', [])
        if str(host).strip()
    }
    try:
        request_host = str(request.get_host()).split(':', 1)[0].lower()
    except Exception:
        return False
    return not allowed_hosts or request_host in allowed_hosts


def parametros_sistema(request):
    """Injeta os parâmetros do sistema (logo) em todos os templates.

    Logado: usa a logo da filial ativa. Sem filial (ex.: tela de login):
    usa a primeira logo cadastrada como fallback.
    """
    from apps.core.models.parametros import ParametrosSistema
    params = None
    checkout_venda_ativo = False
    logo_url_fallback = ''
    try:
        filial = getattr(request, 'filial_ativa', None)
        if filial is not None:
            params = ParametrosSistema.objects.filter(filial=filial).first()
            checkout_venda_ativo = checkout_venda_habilitado(request)
            # Tenta logo_url da empresa como fallback externo
            empresa = getattr(filial, 'empresa', None)
            if empresa:
                logo_url_fallback = getattr(empresa, 'logo_url', '') or ''
        if params is None or not params.logo:
            fallback = (
                ParametrosSistema.objects
                .exclude(logo='').exclude(logo__isnull=True)
                .first()
            )
            if fallback is not None:
                params = fallback
    except Exception:
        params = None
    # Prioridade: params.logo_url > empresa.logo_url
    if params and getattr(params, 'logo_url', ''):
        logo_url_fallback = params.logo_url
    return {
        'parametros_sistema': params,
        'empresa_logo_url': logo_url_fallback,
        # Esta flag deve sempre refletir a filial ativa. `params` pode receber
        # parâmetros de outra filial apenas como fallback visual para a logo.
        'checkout_venda_ativo': checkout_venda_ativo,
    }


def filial_context(request):
    """Injeta filial ativa e filiais disponíveis em todos os templates."""
    ctx = {
        'filial_ativa': getattr(request, 'filial_ativa', None),
        'filiais_disponiveis': [],
        # Chaves dos módulos que esta filial enxerga. O sidebar testa
        # `'chave' in modulos_ativos` -- mesma resposta que o middleware usa
        # para barrar a URL, então menu e acesso nunca discordam.
        'modulos_ativos': set(),
    }
    if not request.user.is_authenticated:
        return ctx
    ctx['modulos_ativos'] = modulos_ativos(ctx['filial_ativa'])
    try:
        user = request.user
        # Mesma lista da tela de escolha e do middleware -- ver
        # `Usuario.filiais_permitidas`. O seletor do cabecalho oferecendo
        # mais que a tela de escolha e' a mesma falha por outra porta.
        qs = user.filiais_permitidas()
        if user.is_superuser and ctx['filial_ativa']:
            # Superuser troca dentro da empresa em que esta; a Central e' o
            # caminho para pular de empresa.
            qs = qs.filter(empresa_id=ctx['filial_ativa'].empresa_id)
        ctx['filiais_disponiveis'] = list(qs.order_by('nome_fantasia', 'razao_social'))
    except Exception:
        pass
    return ctx


def notificacoes_context(request):
    ctx = {'notificacoes_recentes': [], 'notificacoes_nao_lidas': 0}
    if not request.user.is_authenticated:
        return ctx
    filial = getattr(request, 'filial_ativa', None)
    if not filial:
        return ctx
    try:
        from apps.core.models import Notificacao
        from apps.estoque.services.conferencia_transferencia import (
            garantir_conferencias_recebidas,
        )

        garantir_conferencias_recebidas(filial)
        base = Notificacao.objects.filter(filial=filial, ativa=True)
        nao_lidas = base.exclude(leituras__usuario_id=request.user.pk)
        ctx['notificacoes_nao_lidas'] = nao_lidas.count()
        ids_lidas = set(
            base.filter(leituras__usuario_id=request.user.pk)
            .values_list('pk', flat=True)
        )
        recentes = list(base[:15])
        for notificacao in recentes:
            notificacao.foi_lida = notificacao.pk in ids_lidas
        ctx['notificacoes_recentes'] = recentes
    except Exception:
        pass
    return ctx


def orla_widget_context(request):
    """Prepara o Widget Orla com uma identidade curta assinada no servidor."""
    disabled = {'orla_widget': {'enabled': False}}
    if not orla_widget_host_enabled(request):
        return disabled
    resolver_match = getattr(request, 'resolver_match', None)
    if getattr(resolver_match, 'view_name', '') == 'pdv:checkout':
        return disabled

    base_url = str(getattr(settings, 'ORLA_WIDGET_URL', '') or '').rstrip('/')
    public_key = str(getattr(settings, 'ORLA_WIDGET_PUBLIC_KEY', '') or '').strip()
    signing_secret = str(
        getattr(settings, 'ORLA_WIDGET_SIGNING_SECRET', '') or ''
    )
    signing_private_key = str(
        getattr(settings, 'ORLA_WIDGET_SIGNING_PRIVATE_KEY', '') or ''
    ).replace('\\n', '\n')
    user = getattr(request, 'user', None)
    if not all((base_url, public_key)) or not getattr(
        user, 'is_authenticated', False,
    ):
        return disabled

    try:
        filial = getattr(request, 'filial_ativa', None)
        empresa = getattr(filial, 'empresa', None) or getattr(user, 'empresa', None)
        email = str(getattr(user, 'email', '') or '').strip().lower()
        user_id = str(getattr(user, 'pk', '') or '')
        if not email and not user_id:
            return disabled

        metadata = {'system': 'iTED'}
        if empresa is not None:
            metadata.update({
                'empresa_id': str(getattr(empresa, 'pk', '') or ''),
                'empresa': str(
                    getattr(empresa, 'nome_fantasia', '')
                    or getattr(empresa, 'razao_social', '')
                    or empresa
                )[:120],
            })
        if filial is not None:
            metadata.update({
                'filial_id': str(getattr(filial, 'pk', '') or ''),
                'filial': str(
                    getattr(filial, 'nome_fantasia', '')
                    or getattr(filial, 'razao_social', '')
                    or filial
                )[:120],
            })
        metadata = {key: value for key, value in metadata.items() if value}

        # O e-mail é único no diretório de usuários e continua identificando a
        # mesma pessoa quando o middleware troca a cópia central pela do tenant.
        subject = f'ited:user:{email}' if email else f'ited:user-id:{user_id}'
        payload = {
            'sub': subject,
            'name': str(getattr(user, 'nome', '') or email or 'Usuário iTED')[:120],
            'email': email,
            'aud': public_key,
            'exp': int(time.time()) + 300,
            'metadata': metadata,
        }
        user_token = ''
        if signing_private_key:
            from cryptography.hazmat.primitives.serialization import (
                load_pem_private_key,
            )

            payload.update({'iss': 'ited', 'alg': 'EdDSA'})
            encoded = base64.urlsafe_b64encode(
                json.dumps(
                    payload, ensure_ascii=False, separators=(',', ':'),
                ).encode('utf-8')
            ).rstrip(b'=').decode('ascii')
            private_key = load_pem_private_key(
                signing_private_key.encode('utf-8'), password=None,
            )
            signature = base64.urlsafe_b64encode(
                private_key.sign(encoded.encode('ascii'))
            ).rstrip(b'=').decode('ascii')
            user_token = f'{encoded}.{signature}'
        elif signing_secret:
            encoded = base64.urlsafe_b64encode(
                json.dumps(
                    payload, ensure_ascii=False, separators=(',', ':'),
                ).encode('utf-8')
            ).rstrip(b'=').decode('ascii')
            signature = base64.urlsafe_b64encode(
                hmac.new(
                    signing_secret.encode('utf-8'),
                    encoded.encode('ascii'),
                    hashlib.sha256,
                ).digest()
            ).rstrip(b'=').decode('ascii')
            user_token = f'{encoded}.{signature}'

        widget_context = {
            'system': 'iTED',
            'page': str(getattr(request, 'path', '') or '/')[:500],
            **metadata,
        }
        return {
            'orla_widget': {
                'enabled': True,
                'script_url': f'{base_url}/public/widget/v1/orla-widget.js',
                'base_url': base_url,
                'public_key': public_key,
                'user_token': user_token,
                'context_json': json.dumps(
                    widget_context, ensure_ascii=False, separators=(',', ':'),
                ),
            }
        }
    except Exception:
        # Atendimento é auxiliar: uma configuração ou relação incompleta nunca
        # pode impedir a abertura das telas operacionais do ERP.
        return disabled
