from django.urls import reverse


def usuario_e_administrador(usuario, filial=None):
    """Aplica a mesma regra de perfil ativo usada pelas permissoes do ERP."""
    if not getattr(usuario, 'is_authenticated', False):
        return False
    if getattr(usuario, 'is_superuser', False):
        return True

    perfil = getattr(usuario, '_perfil_ativo', None)
    if perfil is None and filial is not None:
        try:
            perfil = usuario.perfil_para_filial(filial)
        except Exception:
            perfil = None
    if perfil is None:
        perfil = getattr(usuario, 'perfil', None)
    return bool(perfil and perfil.is_admin)


def nome_rota_inicial(usuario, filial=None):
    return 'core:dashboard' if usuario_e_administrador(usuario, filial) else 'core:inicio'


def url_inicial(usuario, filial=None):
    return reverse(nome_rota_inicial(usuario, filial))
