"""Ciclo seguro de configuração dos bancos dedicados."""
import csv
import io
import json
import os
import re
import zipfile
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from django.apps import apps
from django.conf import settings
from django.core.files import File
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.core.management.color import no_style
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connections, transaction
from django.utils import timezone
from django.utils.text import slugify

from apps.core.models import EmpresaBanco
from apps.core.services.perfil_padrao_service import PerfilPadraoService
from apps.core.services.railway_provisioner import RailwayProvisioner
from apps.core.services.tenant_bootstrap_service import TenantBootstrapService
from apps.core.tenant_context import tenant_db
from apps.core.tenant_registry import register_tenant_database


class EmpresaBancoService:
    ENV_PREFIX = 'TENANT_DATABASE_URL_'

    @classmethod
    def validar_empresa_preparavel(cls, banco):
        if EmpresaBanco.objects.using('default').filter(
            pk=banco.pk, empresa_id=banco.empresa_id, empresa__ativo=True,
        ).exists():
            return True, ''
        mensagem = (
            'Esta empresa foi desativada ou removida. Reative a empresa antes '
            'de criar ou migrar o banco dedicado.'
        )
        banco.status = EmpresaBanco.Status.INATIVO
        banco.ultimo_erro = mensagem
        banco.save(using='default', update_fields=['status', 'ultimo_erro', 'updated_at'])
        return False, mensagem

    @classmethod
    def tem_banco_provisionado(cls, banco):
        if banco.provisionado_em or banco.railway_database_service_id:
            return True
        if banco.db_alias in connections.databases:
            return True
        return bool(banco.database_url_env_var and os.environ.get(banco.database_url_env_var))

    @classmethod
    def ensure_for_empresa(cls, empresa, actor=None):
        PerfilPadraoService.garantir_para_empresa(empresa)
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
        status_anterior = banco.status
        banco.ultima_verificacao_em = timezone.now()
        try:
            if not register_tenant_database(banco):
                raise RuntimeError(f'Variável {banco.database_url_env_var} não configurada.')
            with connections[banco.db_alias].cursor() as cursor:
                cursor.execute('SELECT 1')
            banco.status = (
                EmpresaBanco.Status.ATIVO
                if status_anterior == EmpresaBanco.Status.ATIVO
                else EmpresaBanco.Status.CONFIGURADO
            )
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
    def migrar_banco(cls, banco, verbosity=0):
        if not register_tenant_database(banco):
            return False, 'Configuração de conexão indisponível.'
        try:
            # RunPython migrations que não usam explicitamente o alias precisam
            # receber o contexto do tenant; sem ele, o ORM cairia no banco central.
            with tenant_db(banco.db_alias):
                call_command(
                    'migrate', database=banco.db_alias, interactive=False, verbosity=verbosity,
                )
                resumo = TenantBootstrapService.sincronizar_empresa(
                    banco.empresa, banco.db_alias,
                )
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
    def gerar_backup_sql(cls, banco):
        """Compatibilidade para consumidores que ainda precisam do backup em memoria."""
        with TemporaryDirectory() as tmpdir:
            filename, path = cls.gerar_backup_sql_em_arquivo(banco, tmpdir)
            return filename, path.read_bytes()

    @classmethod
    def gerar_backup_sql_em_arquivo(cls, banco, destino_dir):
        """Gera o SQL incrementalmente, sem manter o banco inteiro na memoria."""
        if not register_tenant_database(banco):
            raise RuntimeError('Banco ainda nao possui configuracao de conexao para backup.')

        alias = banco.db_alias
        connection = connections[alias]
        timestamp = timezone.now().strftime('%Y%m%dT%H%M%SZ')
        filename = f'backup_{banco.slug}_{timestamp}.sql'
        destino_dir = Path(destino_dir)
        destino_dir.mkdir(parents=True, exist_ok=True)
        backup_path = destino_dir / filename

        try:
            cls._escrever_backup_sql(banco, connection, backup_path)
        except Exception:
            backup_path.unlink(missing_ok=True)
            raise
        finally:
            try:
                connection.close()
            except Exception:
                pass

        return filename, backup_path

    @classmethod
    def gerar_backup_completo_em_arquivo(cls, banco, destino_dir):
        """Gera pacote ZIP com SQL restauravel, CSV por tabela e manifesto."""
        if not register_tenant_database(banco):
            raise RuntimeError('Banco ainda nao possui configuracao de conexao para backup.')

        alias = banco.db_alias
        connection = connections[alias]
        timestamp = timezone.now().strftime('%Y%m%dT%H%M%SZ')
        base_name = f'backup_{banco.slug}_{timestamp}'
        destino_dir = Path(destino_dir)
        destino_dir.mkdir(parents=True, exist_ok=True)
        sql_path = destino_dir / f'{base_name}.sql'
        archive_path = destino_dir / f'{base_name}.zip'

        try:
            with transaction.atomic(using=alias):
                if connection.vendor == 'postgresql':
                    with connection.cursor() as cursor:
                        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
                cls._escrever_backup_sql(banco, connection, sql_path)
                with zipfile.ZipFile(
                    archive_path,
                    mode='w',
                    compression=zipfile.ZIP_DEFLATED,
                    allowZip64=True,
                ) as archive:
                    archive.write(sql_path, sql_path.name)
                    tabelas = cls._adicionar_csvs_ao_zip(connection, archive)
                    manifesto = {
                        'formato': 'ited-backup-completo-v1',
                        'empresa': banco.empresa.razao_social,
                        'banco': banco.db_alias,
                        'gerado_em': timezone.now().isoformat(),
                        'arquivo_sql': sql_path.name,
                        'csv': tabelas,
                        'observacao': (
                            'Use o SQL para restauracao. Os CSVs facilitam conferencia '
                            'e importacao manual por tabela.'
                        ),
                    }
                    archive.writestr(
                        'manifesto.json',
                        json.dumps(
                            manifesto,
                            ensure_ascii=False,
                            indent=2,
                            cls=DjangoJSONEncoder,
                        ),
                    )
        except Exception:
            archive_path.unlink(missing_ok=True)
            raise
        finally:
            sql_path.unlink(missing_ok=True)
            try:
                connection.close()
            except Exception:
                pass

        return archive_path.name, archive_path

    @classmethod
    def gerar_backup_completo_persistente(cls, banco, pasta='backups/tenant_sql'):
        """Gera o pacote localmente e o persiste no storage compartilhado."""
        with TemporaryDirectory() as tmpdir:
            filename, archive_path = cls.gerar_backup_completo_em_arquivo(banco, tmpdir)
            storage_name = f'{pasta.strip("/")}/{filename}'
            with archive_path.open('rb') as arquivo:
                storage_name = default_storage.save(storage_name, File(arquivo))
        return filename, storage_name

    @classmethod
    def _escrever_backup_sql(cls, banco, connection, backup_path):
        qn = connection.ops.quote_name
        with backup_path.open('w', encoding='utf-8', newline='\n') as arquivo, connection.cursor() as cursor:
            arquivo.write(
                f'-- Backup SQL do banco dedicado: {banco.db_alias}\n'
                f'-- Empresa: {banco.empresa.razao_social}\n'
                f'-- Gerado em: {timezone.now().isoformat()}\n'
                '-- Restaure em um banco com as migrations do sistema ja aplicadas.\n\n'
                'BEGIN;\nSET CONSTRAINTS ALL DEFERRED;\n'
            )
            tabelas = connection.introspection.table_names(cursor)
            if tabelas:
                tabelas_sql = ', '.join(qn(tabela) for tabela in tabelas)
                arquivo.write(f'TRUNCATE TABLE {tabelas_sql} RESTART IDENTITY CASCADE;\n\n')

            for tabela in tabelas:
                descricao = connection.introspection.get_table_description(cursor, tabela)
                colunas = [col.name for col in descricao]
                if not colunas:
                    continue
                colunas_sql = ', '.join(qn(coluna) for coluna in colunas)
                cursor.execute(f'SELECT {colunas_sql} FROM {qn(tabela)}')
                arquivo.write(f'-- Dados de {tabela}\n')
                while True:
                    rows = cursor.fetchmany(500)
                    if not rows:
                        break
                    for row in rows:
                        valores = ', '.join(cls._sql_literal(valor) for valor in row)
                        arquivo.write(
                            f'INSERT INTO {qn(tabela)} ({colunas_sql}) VALUES ({valores});\n'
                        )
                arquivo.write('\n')
            if connection.vendor == 'postgresql':
                arquivo.write('-- Reposiciona sequences apos restaurar IDs explicitos.\n')
                for statement in connection.ops.sequence_reset_sql(
                    no_style(),
                    [
                        model for model in apps.get_models()
                        if model._meta.managed and model._meta.db_table in tabelas
                    ],
                ):
                    if statement:
                        arquivo.write(statement.rstrip(';') + ';\n')
                arquivo.write('\n')
            arquivo.write('COMMIT;\n')

    @classmethod
    def _adicionar_csvs_ao_zip(cls, connection, archive):
        qn = connection.ops.quote_name
        resumo = []
        with connection.cursor() as cursor:
            tabelas = connection.introspection.table_names(cursor)
        for tabela in tabelas:
            with connection.cursor() as cursor:
                descricao = connection.introspection.get_table_description(cursor, tabela)
                colunas = [col.name for col in descricao]
                if not colunas:
                    continue
                colunas_sql = ', '.join(qn(coluna) for coluna in colunas)
                cursor.execute(f'SELECT {colunas_sql} FROM {qn(tabela)}')
                total = 0
                with archive.open(f'csv/{tabela}.csv', mode='w', force_zip64=True) as raw_file:
                    text_file = io.TextIOWrapper(raw_file, encoding='utf-8-sig', newline='')
                    writer = csv.writer(text_file, dialect='excel')
                    writer.writerow(colunas)
                    while True:
                        rows = cursor.fetchmany(500)
                        if not rows:
                            break
                        writer.writerows(
                            [cls._csv_value(value) for value in row]
                            for row in rows
                        )
                        total += len(rows)
                    text_file.flush()
                    text_file.detach()
                resumo.append({
                    'tabela': tabela,
                    'arquivo': f'csv/{tabela}.csv',
                    'registros': total,
                })
        return resumo

    @classmethod
    def excluir_banco_com_backup(cls, banco):
        filename, backup_path = cls.gerar_backup_completo_persistente(banco)
        resultado = {}
        if banco.railway_database_service_id or banco.provisionamento_modo == 'railway_api':
            resultado = RailwayProvisioner.delete_postgres(banco)
        else:
            raise RuntimeError(
                'O banco não possui um serviço Railway identificado; a exclusão automática '
                'foi bloqueada para não remover apenas o cadastro central.'
            )

        banco.delete(using='default')
        resultado['backup_path'] = str(backup_path)
        return filename, backup_path, resultado

    @staticmethod
    def _csv_value(value):
        if value is None:
            return ''
        if isinstance(value, bytes):
            return value.hex()
        if isinstance(value, (dict, list)):
            return json.dumps(
                value,
                ensure_ascii=False,
                separators=(',', ':'),
                cls=DjangoJSONEncoder,
            )
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, (datetime, date, time)):
            return value.isoformat()
        return value

    @staticmethod
    def _sql_literal(value):
        if value is None:
            return 'NULL'
        if isinstance(value, bool):
            return 'TRUE' if value else 'FALSE'
        if isinstance(value, (int, float, Decimal)):
            return str(value)
        if isinstance(value, bytes):
            return "'\\\\x" + value.hex() + "'::bytea"
        if isinstance(value, (dict, list)):
            text = json.dumps(
                value,
                ensure_ascii=False,
                separators=(',', ':'),
                cls=DjangoJSONEncoder,
            ).replace("'", "''")
            return f"'{text}'"
        if isinstance(value, UUID):
            value = str(value)
        elif isinstance(value, (datetime, date, time)):
            value = value.isoformat()
        text = str(value).replace("'", "''")
        return f"'{text}'"

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
