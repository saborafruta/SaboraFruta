from django.shortcuts import render


def permission_denied(request, exception=None):
    message = str(exception) if exception else 'Voce nao tem permissao para acessar esta tela.'
    if not message:
        message = 'Voce nao tem permissao para acessar esta tela.'
    return render(request, '403.html', {'permission_message': message}, status=403)


def csrf_failure(request, reason=''):
    """
    A tela de CSRF que o cliente consegue resolver.

    A PADRÃO DO DJANGO É UM BECO: fundo amarelo, "Verificação CSRF falhou.
    Pedido cancelado." e nada para clicar. Quem recebe isso é, quase sempre,
    alguém que não fez nada de errado -- o cookie não veio porque o navegador
    bloqueia, porque a página ficou aberta tempo demais, ou porque a aba foi
    restaurada. E, no link público do pedido, quem recebe é o CLIENTE da
    confecção, que desiste e liga.

    Aqui a mesma falha vira um caminho: explica em uma frase e oferece
    recarregar. A proteção continua exatamente a mesma -- o que muda é o que
    a pessoa vê quando ela dispara.
    """
    publico = request.path.startswith('/pedido/')
    return render(request, 'core/csrf_failure.html', {
        'publico': publico,
        'destino': request.META.get('HTTP_REFERER') or request.path,
        'motivo': reason,
    }, status=403)
