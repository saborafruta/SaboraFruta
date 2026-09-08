from collections import defaultdict

from django.db import transaction

from apps.core.models import PerfilAcesso, Usuario, UsuarioFilialAcesso


class SuperAdminAccessService:
    """Garante vinculos explicitos dos superadmins nas filiais administradas."""

    PERFIL_NOME = 'Super Administrador do Sistema'

    @classmethod
    def garantir_para_filiais(cls, filiais):
        filiais_por_empresa = defaultdict(list)
        for filial in filiais:
            filiais_por_empresa[filial.empresa_id].append(filial)
        if not filiais_por_empresa:
            return 0

        superadmins = list(Usuario.objects.using('default').filter(
            is_superuser=True,
            ativo=True,
        ))
        if not superadmins:
            return 0

        vinculados = 0
        with transaction.atomic(using='default'):
            for empresa_id, filiais_empresa in filiais_por_empresa.items():
                perfil, _ = PerfilAcesso.objects.using('default').get_or_create(
                    empresa_id=empresa_id,
                    nome=cls.PERFIL_NOME,
                    defaults={
                        'descricao': 'Acesso automatico dos superadministradores do sistema.',
                        'is_admin': True,
                        'ativo': True,
                    },
                )
                if not perfil.is_admin or not perfil.ativo:
                    perfil.is_admin = True
                    perfil.ativo = True
                    perfil.save(using='default', update_fields=['is_admin', 'ativo', 'updated_at'])

                for superadmin in superadmins:
                    for filial in filiais_empresa:
                        UsuarioFilialAcesso.objects.using('default').update_or_create(
                            usuario=superadmin,
                            filial=filial,
                            defaults={
                                'perfil': perfil,
                                'ativo': True,
                                'is_padrao': False,
                            },
                        )
                        vinculados += 1
        return vinculados
