"""RBAC da API do PDV -- mesmo padrao de apps.produtos.api.permissions."""
from rest_framework.permissions import BasePermission

ACAO_PADRAO_POR_METODO = {
    'GET': 'ver', 'HEAD': 'ver', 'OPTIONS': 'ver',
    'POST': 'criar',
    'PATCH': 'editar', 'PUT': 'editar',
    'DELETE': 'excluir',
}


class TemPermissaoPDV(BasePermission):
    message = 'Voce nao tem permissao para esta acao.'

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        acao = getattr(view, 'permissao_acao', None) or ACAO_PADRAO_POR_METODO.get(request.method, 'ver')
        return request.user.tem_permissao('pdv', acao)
