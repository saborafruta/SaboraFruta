"""Copia apenas os cadastros centrais necessários para um banco de empresa."""
from django.core.management.color import no_style
from django.db import connections, transaction

from apps.core.models import (
    Empresa, Filial, PerfilAcesso, Permissao, PoliticaReplicacao,
    PoliticaReplicacaoFilial, Usuario, UsuarioFilialAcesso,
)


class TenantBootstrapService:
    MODELS = [
        Empresa, Filial, PoliticaReplicacao, PoliticaReplicacaoFilial,
        PerfilAcesso, Permissao, Usuario, UsuarioFilialAcesso,
    ]

    @classmethod
    def sincronizar_empresa(cls, empresa, alias):
        filiais = list(
            Filial.objects.using('default').filter(empresa=empresa).order_by('pk')
        )
        perfis = list(
            PerfilAcesso.objects.using('default').filter(empresa=empresa).order_by('pk')
        )
        usuarios = list(
            Usuario.objects.using('default').filter(empresa=empresa).order_by('pk')
        )
        with transaction.atomic(using=alias):
            cls._copy_instance(empresa, alias)
            for filial in filiais:
                cls._copy_instance(filial, alias)
            for politica in PoliticaReplicacao.objects.using('default').filter(empresa=empresa):
                cls._copy_instance(politica, alias)
            for politica in PoliticaReplicacaoFilial.objects.using('default').filter(
                filial__empresa=empresa
            ):
                cls._copy_instance(politica, alias)
            for perfil in perfis:
                cls._copy_instance(perfil, alias)
            for permissao in Permissao.objects.using('default').filter(perfil__empresa=empresa):
                cls._copy_instance(permissao, alias)
            for usuario in usuarios:
                cls._copy_instance(usuario, alias)
            for acesso in UsuarioFilialAcesso.objects.using('default').filter(
                filial__empresa=empresa
            ):
                cls._copy_instance(acesso, alias)
            cls._reset_sequences(alias)
        return {
            'filiais': len(filiais), 'perfis': len(perfis), 'usuarios': len(usuarios),
        }

    @staticmethod
    def _copy_instance(obj, alias):
        model = obj.__class__
        data = {
            field.attname: getattr(obj, field.attname)
            for field in model._meta.concrete_fields
            if not field.primary_key
        }
        model.objects.using(alias).update_or_create(pk=obj.pk, defaults=data)

    @classmethod
    def _reset_sequences(cls, alias):
        if connections[alias].vendor != 'postgresql':
            return
        statements = connections[alias].ops.sequence_reset_sql(no_style(), cls.MODELS)
        with connections[alias].cursor() as cursor:
            for statement in statements:
                if statement:
                    cursor.execute(statement)
