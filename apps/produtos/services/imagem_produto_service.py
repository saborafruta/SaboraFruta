from django.urls import reverse


def url_absoluta(request, valor):
    valor = str(valor or '').strip()
    if not valor:
        return ''
    if valor.startswith(('http://', 'https://')) or request is None:
        return valor
    return request.build_absolute_uri(valor)


def foto_produto_url(request, produto):
    """Resolve a foto no storage sem depender de uma segunda consulta ao tenant."""
    if not (produto.foto_url or '').strip():
        return ''
    try:
        valor = produto.foto_url_resolvida
    except Exception:
        # A imagem e auxiliar: uma falha no storage nao pode derrubar a busca.
        valor = produto.foto_url
    return url_absoluta(request, valor)


def foto_produto_thumbnail_url(request, produto):
    """Retorna a miniatura otimizada usada nas telas autenticadas do ERP."""
    if not (produto.foto_url or '').strip():
        return ''
    caminho = reverse('produtos:produto-image-file', kwargs={'pk': produto.pk})
    return request.build_absolute_uri(f'{caminho}?v=thumb')
