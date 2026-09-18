"""Resolve a identidade operacional de um usuário autenticado no diretório central."""

from django.db import transaction

from apps.core.models import (
    EmpresaBanco,
    Filial,
    PerfilAcesso,
    Usuario,
    UsuarioFilialAcesso,
)


class TenantUserService:
    """Mantém uma identidade local para o superadministrador dentro do tenant.

    A sessão Django continua pertencendo ao Banco Gerencial. Em requisições
    operacionais, porém, qualquer FK para ``Usuario`` precisa apontar para uma
    linha do mesmo banco dos dados. Centralizar essa tradução aqui evita que
    cada tela tente contornar o roteador por conta própria.
    """

    PERFIL_GLOBAL = 'Administrador do Sistema'

    @staticmethod
    def _perfil_local(alias, empresa_id, perfil_central):
        perfis = PerfilAcesso.objects.using(alias).filter(
            empresa_id=empresa_id,
            ativo=True,
        )
        perfil = perfis.filter(nome__iexact=perfil_central.nome).first()
        if perfil is None and perfil_central.is_admin:
            perfil = perfis.filter(is_admin=True).order_by('pk').first()
        if perfil is None:
            raise PerfilAcesso.DoesNotExist(
                f'O perfil {perfil_central.nome!r} nao existe no banco operacional.'
            )
        return perfil

    @classmethod
    def resolver_usuario(cls, *, alias, usuario_central, filial_id=None):
        """Cria/atualiza a identidade operacional a partir do diretório central.

        A senha nunca e copiada. O banco gerencial autentica a pessoa e os
        tenants recebem somente a identidade necessaria para FKs, permissoes
        e auditoria operacional.
        """
        banco = EmpresaBanco.objects.using('default').get(
            db_alias=alias,
            ativo=True,
            status=EmpresaBanco.Status.ATIVO,
        )
        filial = (
            Filial.objects.using(alias)
            .select_related('empresa')
            .filter(pk=filial_id, ativo=True, empresa__ativo=True)
            .first()
            if filial_id
            else None
        )
        if filial is None:
            raise Filial.DoesNotExist(
                'O banco operacional nao possui a filial selecionada.'
            )

        filial_central = (
            Filial.objects.using('default')
            .filter(
                empresa_id=banco.empresa_id,
                cnpj=filial.cnpj,
                ativo=True,
                empresa__ativo=True,
            )
            .first()
        )
        if filial_central is None:
            raise Filial.DoesNotExist(
                'A filial operacional nao foi localizada no diretorio gerencial.'
            )

        acessos_ativos = list(
            UsuarioFilialAcesso.objects.using('default')
            .filter(usuario_id=usuario_central.pk, ativo=True)
            .select_related('filial', 'perfil')
        )
        if acessos_ativos:
            acessos_empresa = [
                acesso for acesso in acessos_ativos
                if acesso.filial.empresa_id == banco.empresa_id
            ]
            acesso_selecionado = next(
                (
                    acesso for acesso in acessos_empresa
                    if acesso.filial.cnpj == filial.cnpj
                ),
                None,
            )
            if acesso_selecionado is None:
                raise Usuario.DoesNotExist(
                    'O usuario nao possui acesso a filial selecionada.'
                )
            perfil_central = acesso_selecionado.perfil
        else:
            if usuario_central.empresa_id != banco.empresa_id:
                raise Usuario.DoesNotExist(
                    'O usuario nao possui acesso a empresa selecionada.'
                )
            perfil_central = PerfilAcesso.objects.using('default').get(
                pk=usuario_central.perfil_id,
            )
            if perfil_central.is_admin:
                acessos_empresa = []
            elif usuario_central.filial_id == filial_central.pk:
                acessos_empresa = []
            else:
                raise Usuario.DoesNotExist(
                    'O usuario nao possui acesso a filial selecionada.'
                )

        perfil = cls._perfil_local(alias, filial.empresa_id, perfil_central)
        usuarios = Usuario.objects.using(alias)

        with transaction.atomic(using=alias):
            usuario = usuarios.filter(email__iexact=usuario_central.email).first()
            if usuario is None:
                usuario = usuarios.create(
                    email=usuario_central.email.lower(),
                    empresa_id=filial.empresa_id,
                    filial_id=filial.pk,
                    perfil_id=perfil.pk,
                    nome=usuario_central.nome,
                    cpf=usuario_central.cpf,
                    telefone=usuario_central.telefone,
                    menu_favoritos=list(usuario_central.menu_favoritos or []),
                    preferencias_tabelas=dict(
                        usuario_central.preferencias_tabelas or {},
                    ),
                    password='!',
                    ativo=True,
                    is_staff=False,
                    is_superuser=False,
                )
            else:
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
                    'is_staff': False,
                    'is_superuser': False,
                }
                alterados = []
                for campo, valor in campos.items():
                    if getattr(usuario, campo) != valor:
                        setattr(usuario, campo, valor)
                        alterados.append(campo)
                if alterados:
                    usuario.save(
                        using=alias,
                        update_fields=[*alterados, 'updated_at'],
                    )

            # Vinculos explicitos do gerencial sao a fronteira de acesso.
            # Eles sao reconciliados por CNPJ/nome porque PKs de bancos
            # independentes podem coincidir sem representar a mesma entidade.
            acessos_locais = {}
            for acesso in acessos_empresa:
                filial_local = (
                    Filial.objects.using(alias)
                    .filter(
                        empresa_id=filial.empresa_id,
                        cnpj=acesso.filial.cnpj,
                        ativo=True,
                    )
                    .first()
                )
                if filial_local is None:
                    continue
                perfil_local = cls._perfil_local(
                    alias,
                    filial.empresa_id,
                    acesso.perfil,
                )
                acessos_locais[filial_local.pk] = perfil_local.pk

            existentes = {
                acesso.filial_id: acesso
                for acesso in UsuarioFilialAcesso.objects.using(alias).filter(
                    usuario_id=usuario.pk,
                )
            }
            for filial_local_id, perfil_local_id in acessos_locais.items():
                acesso = existentes.get(filial_local_id)
                if acesso is None:
                    UsuarioFilialAcesso.objects.using(alias).create(
                        usuario_id=usuario.pk,
                        filial_id=filial_local_id,
                        perfil_id=perfil_local_id,
                        ativo=True,
                        is_padrao=filial_local_id == filial.pk,
                    )
                    continue
                campos_acesso = []
                valores = {
                    'perfil_id': perfil_local_id,
                    'ativo': True,
                    'is_padrao': filial_local_id == filial.pk,
                }
                for campo, valor in valores.items():
                    if getattr(acesso, campo) != valor:
                        setattr(acesso, campo, valor)
                        campos_acesso.append(campo)
                if campos_acesso:
                    acesso.save(
                        using=alias,
                        update_fields=[*campos_acesso, 'updated_at'],
                    )

            stale_ids = set(existentes) - set(acessos_locais)
            if stale_ids:
                UsuarioFilialAcesso.objects.using(alias).filter(
                    usuario_id=usuario.pk,
                    filial_id__in=stale_ids,
                    ativo=True,
                ).update(ativo=False, is_padrao=False)

        return usuario

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
