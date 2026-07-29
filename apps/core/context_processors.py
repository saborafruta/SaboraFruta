"""Context processors: disponibilizam dados em todos os templates."""
from apps.core.models import Filial


def parametros_sistema(request):
    """Injeta os parâmetros do sistema (logo) em todos os templates.

    Logado: usa a logo da filial ativa. Sem filial (ex.: tela de login):
    usa a primeira logo cadastrada como fallback.
    """
    from apps.core.models.parametros import ParametrosSistema
    params = None
    logo_url_fallback = ''
    try:
        filial = getattr(request, 'filial_ativa', None)
        if filial is not None:
            params = ParametrosSistema.objects.filter(filial=filial).first()
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
    return {'parametros_sistema': params, 'empresa_logo_url': logo_url_fallback}


def filial_context(request):
    """Injeta filial ativa e filiais disponíveis em todos os templates."""
    ctx = {
        'filial_ativa': getattr(request, 'filial_ativa', None),
        'filiais_disponiveis': [],
    }
    if not request.user.is_authenticated:
        return ctx
    try:
        user = request.user
        qs = Filial.objects.filter(ativo=True)
        perfil = getattr(user, 'perfil', None)
        is_admin = user.is_superuser or (perfil is not None and perfil.is_admin)
        if user.is_superuser and ctx['filial_ativa']:
            qs = qs.filter(empresa_id=ctx['filial_ativa'].empresa_id)
        elif not user.is_superuser:
            qs = qs.filter(empresa=user.empresa)
        if not is_admin:
            acessos_ids = list(user.acessos_filiais.filter(ativo=True).values_list('filial_id', flat=True))
            if acessos_ids:
                qs = qs.filter(pk__in=acessos_ids)
            elif user.filial_id:
                qs = qs.filter(pk=user.filial_id)
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

        base = Notificacao.objects.filter(filial=filial, ativa=True)
        nao_lidas = base.exclude(leituras__usuario=request.user)
        ctx['notificacoes_nao_lidas'] = nao_lidas.count()
        ids_lidas = set(
            base.filter(leituras__usuario=request.user)
            .values_list('pk', flat=True)
        )
        recentes = list(base[:15])
        for notificacao in recentes:
            notificacao.foi_lida = notificacao.pk in ids_lidas
        ctx['notificacoes_recentes'] = recentes
    except Exception:
        pass
    return ctx
