"""Resolve a identidade operacional de um usuário autenticado no diretório central."""

from django.db import transaction

from apps.core.models import Filial, PerfilAcesso, Usuario


class TenantUserService:
    """Mantém uma identidade local para o superadministrador dentro do tenant.

    A sessão Django continua pertencendo ao Banco Gerencial. Em requisições
    operacionais, porém, qualquer FK para ``Usuario`` precisa apontar para uma
    linha do mesmo banco dos dados. Centralizar essa tradução aqui evita que
    cada tela tente contornar o roteador por conta própria.
    """

    PERFIL_GLOBAL = 'Administrador do Sistema'

    @classmethod
    def resolver_superusuario(cls, *, alias, usuario_central, filial_id=None):
        filial = (
            Filial.objects.using(alias)
            .select_related('empresa')
            .filter(pk=filial_id, ativo=True)
            .first()
            if filial_id
            else None
        )
        if filial is None:
            filial = (
                Filial.objects.using(alias)
                .select_related('empresa')
                .filter(ativo=True, empresa__ativo=True)
                .order_by('-is_matriz', 'pk')
                .first()
            )
        if filial is None:
            raise Filial.DoesNotExist(
                'O banco operacional não possui filial ativa para o usuário global.'
            )

        with transaction.atomic(using=alias):
            perfil = (
                PerfilAcesso.objects.using(alias)
                .filter(
                    empresa_id=filial.empresa_id,
                    is_admin=True,
                    ativo=True,
                )
                .order_by('pk')
                .first()
            )
            if perfil is None:
                perfil, _ = PerfilAcesso.objects.using(alias).get_or_create(
                    empresa_id=filial.empresa_id,
                    nome=cls.PERFIL_GLOBAL,
                    defaults={
                        'descricao': (
                            'Perfil técnico da identidade operacional do '
                            'superadministrador global.'
                        ),
                        'is_admin': True,
                        'ativo': True,
                    },
                )
                if not perfil.is_admin or not perfil.ativo:
                    PerfilAcesso.objects.using(alias).filter(pk=perfil.pk).update(
                        is_admin=True,
                        ativo=True,
                    )
                    perfil.is_admin = True
                    perfil.ativo = True

            usuarios = Usuario.objects.using(alias)
            usuario = usuarios.filter(
                email__iexact=usuario_central.email,
            ).first()
            criado = usuario is None
            if criado:
                usuario, criado = usuarios.get_or_create(
                    email=usuario_central.email.lower(),
                    defaults={
                        'empresa_id': filial.empresa_id,
                        'filial_id': filial.pk,
                        'perfil_id': perfil.pk,
                        'nome': usuario_central.nome,
                        'cpf': usuario_central.cpf,
                        'telefone': usuario_central.telefone,
                        'menu_favoritos': list(usuario_central.menu_favoritos or []),
                        'preferencias_tabelas': dict(
                            usuario_central.preferencias_tabelas or {},
                        ),
                        'password': '!',
                        'ativo': True,
                        'is_staff': True,
                        'is_superuser': True,
                    },
                )
            campos = {
                'empresa_id': filial.empresa_id,
                'filial_id': filial.pk,
                'perfil_id': perfil.pk,
                'nome': usuario_central.nome,
                'cpf': usuario_central.cpf,
                'telefone': usuario_central.telefone,
                'menu_favoritos': list(usuario_central.menu_favoritos or []),
                'preferencias_tabelas': dict(
                    usuario_central.preferencias_tabelas or {},
                ),
                'ativo': True,
                'is_staff': True,
                'is_superuser': True,
            }
            if not criado:
                alterados = []
                for campo, valor in campos.items():
                    if getattr(usuario, campo) != valor:
                        setattr(usuario, campo, valor)
                        alterados.append(campo)
                if alterados:
                    usuario.save(using=alias, update_fields=[*alterados, 'updated_at'])

        return usuario
