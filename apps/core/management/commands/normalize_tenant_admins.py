from django.core.management.base import BaseCommand
from django.db import connections, transaction

from apps.core.models import EmpresaBanco, PerfilAcesso, Usuario
from apps.core.tenant_registry import register_tenant_database


class Command(BaseCommand):
    help = 'Converte superusuários de tenant em administradores da própria empresa.'

    def handle(self, *args, **options):
        total = 0
        bancos = EmpresaBanco.objects.using('default').filter(
            ativo=True,
            status__in=[EmpresaBanco.Status.CONFIGURADO, EmpresaBanco.Status.ATIVO],
        ).order_by('pk')
        for banco in bancos:
            if not register_tenant_database(banco):
                self.stderr.write(self.style.ERROR(f'{banco.db_alias}: conexão indisponível'))
                continue
            try:
                with transaction.atomic(using=banco.db_alias):
                    usuarios = Usuario.objects.using(banco.db_alias).filter(
                        is_superuser=True,
                    ).select_related('perfil')
                    ids = []
                    for usuario in usuarios:
                        if not usuario.perfil.is_admin:
                            PerfilAcesso.objects.using(banco.db_alias).filter(
                                pk=usuario.perfil_id,
                            ).update(is_admin=True)
                        ids.append(usuario.pk)
                    alterados = Usuario.objects.using(banco.db_alias).filter(
                        pk__in=ids,
                    ).update(is_superuser=False, is_staff=False)
                    total += alterados
                    self.stdout.write(f'{banco.db_alias}: {alterados} normalizados')
            finally:
                connections[banco.db_alias].close()
        self.stdout.write(self.style.SUCCESS(f'Total: {total} usuários normalizados.'))
