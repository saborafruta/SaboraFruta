"""RBAC da API de equalização -- reaproveita o mesmo `tem_permissao` usado pelas telas HTML, não um esquema novo."""
from rest_framework.permissions import BasePermission


class TemPermissaoEstoque(BasePermission):
    """
    `view.permissao_acao` (padrão 'ver') define qual ação checar contra
    `usuario.tem_permissao('estoque', acao)` -- o mesmo RBAC por
    módulo/perfil já usado em toda tela HTML do ERP.
    """

    message = "Você não tem permissão para esta ação."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        acao = getattr(view, "permissao_acao", "ver")
        return request.user.tem_permissao("estoque", acao)
