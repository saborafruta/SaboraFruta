from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from apps.core.models import Usuario
from apps.core.services.auth_service import AuthService
from apps.core.services.exceptions import DadosInvalidosError


@sensitive_variables('senha')
def validar_administrador(request, email, senha):
    """Autoriza uma operação sem trocar o usuário da sessão do operador."""
    autorizado = None
    if isinstance(email, str) and isinstance(senha, str) and email.strip() and senha:
        with transaction.atomic():
            admin = Usuario.objects.select_for_update().filter(
                Q(empresa_id=request.filial_ativa.empresa_id) | Q(is_superuser=True),
                email__iexact=email.strip(), ativo=True,
            ).first()
            perfil = admin.perfil_para_filial(request.filial_ativa) if admin else None
            elegivel = admin and admin.pode_acessar_filial(request.filial_ativa) and (
                admin.is_superuser or (perfil and perfil.ativo and perfil.is_admin)
            )
            if elegivel and not (admin.bloqueado_ate and admin.bloqueado_ate > timezone.now()):
                if admin.check_password(senha):
                    Usuario.objects.filter(pk=admin.pk).update(tentativas_login_falhas=0)
                    autorizado = admin
                else:
                    # Usa o bloqueio persistente já empregado no login, com a linha travada.
                    AuthService._registrar_falha(request, admin.email)
    if autorizado is None:
        raise DadosInvalidosError('Administrador ou senha inválidos, sem acesso à filial ou temporariamente bloqueado.')
    return autorizado
