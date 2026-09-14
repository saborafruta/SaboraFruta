def empresa_operacional(request):
    """Retorna a empresa do banco operacional selecionado na requisição.

    Um superusuário continua autenticado pelo diretório central mesmo depois
    de entrar em um tenant. Nesse caso, ``request.user.empresa`` pertence ao
    banco central e seu PK não pode ser reutilizado em consultas no tenant,
    pois bancos independentes podem ter empresas diferentes com o mesmo PK.
    A filial ativa, por outro lado, é resolvida no banco operacional correto.
    """
    filial = getattr(request, "filial_ativa", None)
    if filial is not None:
        return filial.empresa
    return request.user.empresa


def usuario_operacional(request, *, obrigatorio=False):
    """Resolve o ator no mesmo banco em que a operação será persistida.

    O superusuário permanece autenticado no diretório central para conservar
    suas permissões globais. Relações operacionais, porém, precisam apontar
    para a cópia desse usuário dentro do tenant ativo.
    """
    from apps.core.models import Usuario
    from apps.core.services.exceptions import DomainError
    from apps.core.tenant_context import get_current_database_alias

    usuario = request.user
    if not getattr(usuario, "is_authenticated", False):
        usuario = None
    else:
        alias = get_current_database_alias()
        if getattr(getattr(usuario, "_state", None), "db", None) != alias:
            email = (getattr(usuario, "email", "") or "").strip()
            usuario = (
                Usuario.objects.using(alias)
                .filter(email__iexact=email, ativo=True)
                .first()
                if email
                else None
            )
    if usuario is None and obrigatorio:
        raise DomainError(
            "Não foi possível identificar seu usuário no banco desta empresa. "
            "Saia, entre novamente e, se o problema continuar, fale com o suporte."
        )
    return usuario
