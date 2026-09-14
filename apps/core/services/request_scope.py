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
