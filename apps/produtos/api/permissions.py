"""RBAC da API de produtos -- reaproveita `tem_permissao`, o mesmo RBAC por
modulo/perfil ja usado em toda tela HTML do ERP (ver apps/estoque/api/permissions.py,
mesmo padrao).
"""
from rest_framework.permissions import BasePermission

ACAO_PADRAO_POR_METODO = {
    'GET': 'ver', 'HEAD': 'ver', 'OPTIONS': 'ver',
    'POST': 'criar',
    'PATCH': 'editar', 'PUT': 'editar',
    'DELETE': 'excluir',
}


class TemPermissaoProdutos(BasePermission):
    """
    `view.permissao_acao`, se declarado, define a acao a checar contra
    `usuario.tem_permissao('produtos', acao)`. Sem isso, a acao e' inferida
    do verbo HTTP (GET->ver, POST->criar, PATCH/PUT->editar, DELETE->excluir)
    -- necessario porque uma mesma view aqui frequentemente atende GET
    (listar) e POST (criar), que exigem permissoes diferentes.

    O RBAC do projeto nao tem granularidade abaixo de modulo+acao (nao da
    pra expressar "pode editar produto mas nao fator de conversao" aqui) --
    guardrails de campo critico (fator_conversao, unidade, flags principal_*)
    sao responsabilidade da view, que audita a alteracao via
    `apps.core.services.auditoria.registrar_auditoria`.
    """

    message = 'Voce nao tem permissao para esta acao.'

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        acao = getattr(view, 'permissao_acao', None) or ACAO_PADRAO_POR_METODO.get(request.method, 'ver')
        return request.user.tem_permissao('produtos', acao)
