"""Ciclo seguro de configuração dos bancos dedicados."""
import os
import re

from django.conf import settings
from django.core.management import call_command
from django.db import connections
from django.utils import timezone
from django.utils.text import slugify

from apps.core.models import EmpresaBanco
from apps.core.services.railway_provisioner import RailwayProvisioner
from apps.core.services.tenant_bootstrap_service import TenantBootstrapService
from apps.core.tenant_registry import register_tenant_database


class EmpresaBancoService:
    ENV_PREFIX = 'TENANT_DATABASE_URL_'

    @classmethod
    def ensure_for_empresa(cls, empresa):
        slug = cls._unique_slug(empresa)
        defaults = {
            'slug': slug,
            'db_alias': cls._unique_alias(slug),
            'database_url_env_var': f'{cls.ENV_PREFIX}{slug.upper().replace("-", "_")}',
            'provisionamento_modo': settings.TENANT_DATABASE_PROVISIONING_MODE,
        }
        return EmpresaBanco.objects.using('default').get_or_create(
            empresa=empresa, defaults=defaults,
        )

    @classmethod
    def solicitar_provisionamento(cls, banco):
        banco.provisionamento_solicitado_em = timezone.now()
        banco.status = EmpresaBanco.Status.PENDENTE
        banco.ultimo_erro = ''
        banco.save(using='default')
        if banco.provisionamento_modo != 'railway_api':
            banco.status = EmpresaBanco.Status.AGUARDANDO_CONFIGURACAO
            banco.save(using='default', update_fields=['status', 'updated_at'])
            return False, f'Configure a variável {banco.database_url_env_var}.'

        try:
            result = RailwayProvisioner.provision_postgres(banco)
            if result['database_url']:
                os.environ[banco.database_url_env_var] = result['database_url']
                if not RailwayProvisioner.wait_for_database(
                    banco.db_alias, result['database_url'], timeout=90,
                ):
                    raise RuntimeError('O banco foi criado, mas não ficou disponível a tempo.')
            banco.status = EmpresaBanco.Status.CONFIGURADO
            banco.provisionado_em = timezone.now()
            banco.save(using='default')
            return True, f'Banco {result["service_name"]} provisionado.'
        except Exception as exc:
            banco.status = EmpresaBanco.Status.ERRO
            banco.ultimo_erro = str(exc)
            banco.save(using='default')
            return False, str(exc)

    @classmethod
    def testar_conexao(cls, banco):
        banco.ultima_verificacao_em = timezone.now()
        try:
            if not register_tenant_database(banco):
                raise RuntimeError(f'Variável {banco.database_url_env_var} não configurada.')
            with connections[banco.db_alias].cursor() as cursor:
                cursor.execute('SELECT 1')
            banco.status = EmpresaBanco.Status.CONFIGURADO
            banco.ultimo_erro = ''
            ok, message = True, 'Conexão validada.'
        except Exception as exc:
            banco.status = EmpresaBanco.Status.ERRO
            banco.ultimo_erro = str(exc)
            ok, message = False, str(exc)
        finally:
            if banco.db_alias in connections.databases:
                connections[banco.db_alias].close()
        banco.save(using='default')
        return ok, message

    @classmethod
    def migrar_banco(cls, banco):
        if not register_tenant_database(banco):
            return False, 'Configuração de conexão indisponível.'
        try:
            call_command('migrate', database=banco.db_alias, interactive=False, verbosity=0)
            resumo = TenantBootstrapService.sincronizar_empresa(banco.empresa, banco.db_alias)
            banco.status = EmpresaBanco.Status.ATIVO
            banco.ultima_migracao_em = timezone.now()
            banco.ultimo_erro = ''
            banco.save(using='default')
            return True, f'Banco ativo; {resumo["usuarios"]} usuários sincronizados.'
        except Exception as exc:
            banco.status = EmpresaBanco.Status.ERRO
            banco.ultimo_erro = str(exc)
            banco.save(using='default')
            return False, str(exc)
        finally:
            connections[banco.db_alias].close()

    @classmethod
    def sincronizar_banco(cls, banco):
        if not register_tenant_database(banco):
            return False, 'Configuração de conexão indisponível.'
        try:
            resumo = TenantBootstrapService.sincronizar_empresa(banco.empresa, banco.db_alias)
            banco.ultimo_erro = ''
            banco.save(using='default', update_fields=['ultimo_erro', 'updated_at'])
            return True, f'{resumo["usuarios"]} usuários sincronizados.'
        except Exception as exc:
            banco.ultimo_erro = str(exc)
            banco.save(using='default', update_fields=['ultimo_erro', 'updated_at'])
            return False, str(exc)
        finally:
            connections[banco.db_alias].close()

    @classmethod
    def _base_slug(cls, empresa):
        document = re.sub(r'\D', '', empresa.cnpj or '')
        name = slugify(empresa.nome_fantasia or empresa.razao_social or document or empresa.pk)
        suffix = f'-{document}' if document else ''
        return f'{name[:80-len(suffix)].strip("-")}{suffix}'

    @classmethod
    def _unique_slug(cls, empresa):
        base = cls._base_slug(empresa)
        slug, counter = base, 2
        qs = EmpresaBanco.objects.using('default').exclude(empresa=empresa)
        while qs.filter(slug=slug).exists():
            suffix = f'-{counter}'
            slug, counter = f'{base[:80-len(suffix)]}{suffix}', counter + 1
        return slug

    @classmethod
    def _unique_alias(cls, slug):
        base = f'empresa_{slug.replace("-", "_")}'[:70]
        alias, counter = base, 2
        while EmpresaBanco.objects.using('default').filter(db_alias=alias).exists():
            suffix = f'_{counter}'
            alias, counter = f'{base[:80-len(suffix)]}{suffix}', counter + 1
        return alias
