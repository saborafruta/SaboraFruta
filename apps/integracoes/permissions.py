from rest_framework.permissions import BasePermission


class PossuiEscopoIntegracao(BasePermission):
    message = 'A credencial não possui o escopo necessário para este recurso.'

    def has_permission(self, request, view):
        escopo = getattr(view, 'escopo_necessario', None)
        return not escopo or request.auth.possui_escopo(escopo)
