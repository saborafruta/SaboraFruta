"""Importa para o gerencial o diretório mínimo de uma empresa já existente."""
from django.db import transaction

from apps.core.models import (
    Empresa, EmpresaBanco, Filial, PerfilAcesso, Permissao,
    PoliticaReplicacao, PoliticaReplicacaoFilial, Usuario,
    UsuarioFilialAcesso,
)
from apps.core.services.empresa_banco_service import EmpresaBancoService


class TenantDirectoryImportService:
    @staticmethod
    def _dados(obj, *, excluir=(), substituir=None):
        dados = {
            field.attname: getattr(obj, field.attname)
            for field in obj._meta.concrete_fields
            if not field.primary_key and field.attname not in excluir
        }
        dados.update(substituir or {})
        return dados

    @classmethod
    def importar(
        cls,
        source_alias,
        *,
        cnpj='',
        database_url_env_var='',
        railway_service_id='',
        railway_service_name='',
        ativar=False,
    ):
        empresas = Empresa.objects.using(source_alias).all().order_by('pk')
        if cnpj:
            empresas = empresas.filter(cnpj=cnpj)
        if empresas.count() != 1:
            raise RuntimeError('Informe um CNPJ que identifique exatamente uma empresa.')
        origem = empresas.get()

        with transaction.atomic(using='default'):
            empresa, _ = Empresa.objects.using('default').update_or_create(
                cnpj=origem.cnpj,
                defaults=cls._dados(origem, excluir=('cnpj',)),
            )

            filiais = {}
            for filial_origem in Filial.objects.using(source_alias).filter(
                empresa_id=origem.pk,
            ).order_by('pk'):
                conflito = Filial.objects.using('default').filter(
                    cnpj=filial_origem.cnpj,
                ).exclude(empresa=empresa).first()
                if conflito:
                    raise RuntimeError(f'CNPJ de filial já pertence a {conflito.empresa}.')
                filial, _ = Filial.objects.using('default').update_or_create(
                    cnpj=filial_origem.cnpj,
                    defaults=cls._dados(
                        filial_origem,
                        excluir=('cnpj', 'empresa_id'),
                        substituir={'empresa_id': empresa.pk},
                    ),
                )
                filiais[filial_origem.pk] = filial

            perfis = {}
            for perfil_origem in PerfilAcesso.objects.using(source_alias).filter(
                empresa_id=origem.pk,
            ).order_by('pk'):
                perfil, _ = PerfilAcesso.objects.using('default').update_or_create(
                    empresa=empresa,
                    nome=perfil_origem.nome,
                    defaults=cls._dados(
                        perfil_origem,
                        excluir=('empresa_id', 'nome'),
                    ),
                )
                perfis[perfil_origem.pk] = perfil
                for permissao_origem in Permissao.objects.using(source_alias).filter(
                    perfil_id=perfil_origem.pk,
                ):
                    Permissao.objects.using('default').update_or_create(
                        perfil=perfil,
                        modulo=permissao_origem.modulo,
                        defaults=cls._dados(
                            permissao_origem,
                            excluir=('perfil_id', 'modulo'),
                        ),
                    )

            usuarios = {}
            for usuario_origem in Usuario.objects.using(source_alias).filter(
                empresa_id=origem.pk,
            ).order_by('pk'):
                conflito = Usuario.objects.using('default').filter(
                    email__iexact=usuario_origem.email,
                ).exclude(empresa=empresa).first()
                if conflito:
                    raise RuntimeError(
                        f'E-mail {usuario_origem.email} já pertence a outra empresa.',
                    )
                usuario, _ = Usuario.objects.using('default').update_or_create(
                    email=usuario_origem.email,
                    defaults=cls._dados(
                        usuario_origem,
                        excluir=('email', 'empresa_id', 'filial_id', 'perfil_id'),
                        substituir={
                            'empresa_id': empresa.pk,
                            'filial_id': (
                                filiais[usuario_origem.filial_id].pk
                                if usuario_origem.filial_id else None
                            ),
                            'perfil_id': perfis[usuario_origem.perfil_id].pk,
                            # Superusuário do tenant não é administrador global
                            # da plataforma. No banco da empresa, o perfil admin
                            # continua concedendo todas as permissões funcionais.
                            'is_superuser': False,
                            'is_staff': False,
                        },
                    ),
                )
                usuarios[usuario_origem.pk] = usuario

            for acesso_origem in UsuarioFilialAcesso.objects.using(source_alias).filter(
                filial__empresa_id=origem.pk,
            ):
                UsuarioFilialAcesso.objects.using('default').update_or_create(
                    usuario=usuarios[acesso_origem.usuario_id],
                    filial=filiais[acesso_origem.filial_id],
                    defaults=cls._dados(
                        acesso_origem,
                        excluir=('usuario_id', 'filial_id', 'perfil_id'),
                        substituir={'perfil_id': perfis[acesso_origem.perfil_id].pk},
                    ),
                )

            politica = PoliticaReplicacao.objects.using(source_alias).filter(
                empresa_id=origem.pk,
            ).first()
            if politica:
                PoliticaReplicacao.objects.using('default').update_or_create(
                    empresa=empresa,
                    defaults=cls._dados(politica, excluir=('empresa_id',)),
                )
            for politica_filial in PoliticaReplicacaoFilial.objects.using(
                source_alias,
            ).filter(filial__empresa_id=origem.pk):
                PoliticaReplicacaoFilial.objects.using('default').update_or_create(
                    filial=filiais[politica_filial.filial_id],
                    defaults=cls._dados(politica_filial, excluir=('filial_id',)),
                )

            banco, _ = EmpresaBancoService.ensure_for_empresa(empresa)
            banco.db_alias = source_alias
            banco.database_url_env_var = (
                database_url_env_var or banco.database_url_env_var
            )
            banco.railway_database_service_id = railway_service_id
            banco.railway_database_service_name = railway_service_name
            banco.status = (
                EmpresaBanco.Status.ATIVO
                if ativar else EmpresaBanco.Status.CONFIGURADO
            )
            banco.ativo = True
            banco.ultimo_erro = ''
            banco.save(using='default')

        return banco, {
            'filiais': len(filiais),
            'perfis': len(perfis),
            'usuarios': len(usuarios),
        }
