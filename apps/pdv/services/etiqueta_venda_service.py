from apps.core.models import ConfiguracaoEtiquetaVenda, Filial, ParametrosSistema


def _arquivo_url(arquivo):
    if not arquivo:
        return ''
    try:
        return arquivo.url
    except (ValueError, OSError):
        return ''


def configuracao_etiqueta_filial(filial):
    """Retorna configuração e identidade centrais equivalentes à filial tenant."""
    filial_central = (
        Filial.objects.using('default')
        .select_related('empresa')
        .filter(cnpj=filial.cnpj)
        .first()
    )
    config = None
    if filial_central:
        config = (
            ConfiguracaoEtiquetaVenda.objects.using('default')
            .filter(filial_id=filial_central.pk)
            .first()
        )
    return config, filial_central


def contexto_etiqueta_venda(venda):
    """Monta a etiqueta sem misturar as PKs do banco tenant e do central."""
    filial_venda = venda.filial
    config, filial_central = configuracao_etiqueta_filial(filial_venda)
    if config is None:
        config = ConfiguracaoEtiquetaVenda(
            filial_id=getattr(filial_central, 'pk', filial_venda.pk),
        )

    filial_identidade = filial_central or filial_venda
    logo_url = _arquivo_url(getattr(filial_identidade, 'imagem', None))
    if not logo_url:
        banco_venda = venda._state.db or 'default'
        parametros = (
            ParametrosSistema.objects.using(banco_venda)
            .filter(filial_id=filial_venda.pk)
            .first()
        )
        if parametros:
            logo_url = parametros.logo_url or _arquivo_url(parametros.logo)

    return {
        'config': config,
        'filial_identidade': filial_identidade,
        'logo_url': logo_url,
        'cliente_nome': venda.cliente.nome_display if venda.cliente else 'Consumidor Final',
    }
