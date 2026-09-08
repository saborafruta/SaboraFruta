"""Serviços para separar uma filial e transformá-la em empresa independente."""
from __future__ import annotations

import json
import re
import threading
import zipfile
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.apps import apps
from django.conf import settings
from django.core.files import File
from django.core.files.storage import default_storage
from django.core.management.color import no_style
from django.core.serializers.json import DjangoJSONEncoder
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connections, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify

from apps.core.models import (
    Empresa,
    EmpresaBanco,
    Filial,
    PerfilAcesso,
    Permissao,
    PoliticaReplicacao,
    PoliticaReplicacaoFilial,
    SeparacaoFilial,
    Usuario,
    UsuarioFilialAcesso,
)
from apps.core.services.auditoria import registrar_auditoria, snapshot_modelo
from apps.core.services.empresa_banco_service import EmpresaBancoService
from apps.core.services.superadmin_access_service import SuperAdminAccessService
from apps.core.tenant_registry import register_tenant_database


class SeparacaoFilialError(Exception):
    """Erro controlado do fluxo de separação de filial."""


class SeparacaoFilialService:
    """Orquestra simulação e execução da separação de uma filial."""

    MODELOS_NAO_COPIAR = {
        # Preferencias e credenciais temporarias pertencem ao ambiente central,
        # nao ao historico operacional da filial.
        'core.filialfavorita',
        'core.sessaousuario',
        'pdv.pdvcache',
    }

    STATUS_ABERTOS = {
        'aberto',
        'aberta',
        'pendente',
        'em_andamento',
        'em andamento',
        'processando',
        'rascunho',
        'digitacao',
        'em_digitacao',
        'aguardando',
        'separacao',
    }

    @classmethod
    def replicacao_bloqueada(cls, filial):
        """Separacoes concluidas so podem ser reunificadas por conciliacao assistida."""
        if not filial or not filial.pk:
            return False
        return SeparacaoFilial.objects.filter(
            filial_origem_id=filial.pk,
            empresa_destino_id=filial.empresa_id,
            status=SeparacaoFilial.Status.CONCLUIDO,
        ).exists()

    @classmethod
    def obter_ou_criar(cls, filial, usuario=None):
        processo = (
            SeparacaoFilial.objects
            .filter(
                filial_origem=filial,
                status__in=[
                    SeparacaoFilial.Status.RASCUNHO,
                SeparacaoFilial.Status.SIMULADO,
                SeparacaoFilial.Status.BLOQUEADO,
                SeparacaoFilial.Status.ERRO,
                SeparacaoFilial.Status.CONCLUIDO,
            ],
            )
            .order_by('-created_at')
            .first()
        )
        if processo:
            return processo
        return SeparacaoFilial.objects.create(
            filial_origem=filial,
            empresa_origem=filial.empresa,
            iniciado_por=usuario if getattr(usuario, 'is_authenticated', False) else None,
        )

    @classmethod
    def simular(cls, processo):
        if processo.status in [SeparacaoFilial.Status.CONCLUIDO, SeparacaoFilial.Status.EXECUTANDO]:
            return processo.simulacao_json or {}
        filial = Filial.objects.select_related('empresa').get(pk=processo.filial_origem_id)
        if processo.empresa_destino_id and filial.empresa_id == processo.empresa_destino_id:
            simulacao = dict(processo.simulacao_json or {})
            simulacao['bloqueios'] = []
            simulacao.setdefault('avisos', []).append(
                'Retomando uma separacao interrompida sem repetir a mudanca administrativa.'
            )
            return simulacao
        blockers = []
        warnings = []

        if not filial.ativo:
            blockers.append('A filial está inativa.')
        if not ''.join(ch for ch in (filial.cnpj or '') if ch.isdigit()):
            blockers.append('A filial não possui CNPJ válido para virar empresa.')
        empresa_mesmo_cnpj = Empresa.objects.filter(cnpj=filial.cnpj).first()
        if empresa_mesmo_cnpj:
            if empresa_mesmo_cnpj.pk == filial.empresa_id:
                blockers.append('O CNPJ da filial é igual ao CNPJ da empresa atual.')
            else:
                blockers.append('Já existe uma empresa cadastrada com o CNPJ desta filial.')

        filiais_ativas = Filial.objects.filter(empresa=filial.empresa, ativo=True).count()
        if filiais_ativas <= 1:
            blockers.append('A empresa atual precisa ter pelo menos duas filiais ativas para separar uma delas.')
        if filial.is_matriz and filiais_ativas > 1:
            warnings.append('A filial selecionada é matriz. A empresa original precisará definir outra matriz.')

        usuarios = cls._impacto_usuarios(filial)
        if usuarios['multifiliais']:
            warnings.append(
                'Usuários que também acessam outras filiais terão o acesso a esta unidade '
                'suspenso para revisão manual depois da separação.'
            )

        operacoes_abertas = cls._operacoes_abertas(filial)
        if operacoes_abertas:
            blockers.append('Existem operações abertas vinculadas à filial.')

        contagens = cls._contagens_por_filial(filial)
        plano = cls._plano_empresa_destino(filial)
        bancos = cls._info_bancos(filial)
        auditoria_modulos = cls._auditoria_modulos(contagens, operacoes_abertas)
        simulacao = {
            'filial': {
                'id': filial.pk,
                'razao_social': filial.razao_social,
                'nome_fantasia': filial.nome_fantasia,
                'cnpj': filial.cnpj,
                'cidade': filial.cidade,
                'uf': filial.uf,
            },
            'empresa_origem': {
                'id': filial.empresa_id,
                'razao_social': filial.empresa.razao_social,
            },
            'plano': plano,
            'usuarios': usuarios,
            'contagens': contagens,
            'operacoes_abertas': operacoes_abertas,
            'auditoria_modulos': auditoria_modulos,
            'bancos': bancos,
            'bloqueios': blockers,
            'avisos': warnings,
            'simulado_em': timezone.now().isoformat(),
        }
        processo.plano_json = plano
        processo.simulacao_json = simulacao
        processo.status = SeparacaoFilial.Status.BLOQUEADO if blockers else SeparacaoFilial.Status.SIMULADO
        processo.ultimo_erro = '\n'.join(blockers)
        processo.save(update_fields=['plano_json', 'simulacao_json', 'status', 'ultimo_erro', 'updated_at'])
        return simulacao

    @classmethod
    def executar(cls, processo, usuario=None, request=None):
        simulacao = cls.simular(processo)
        if simulacao['bloqueios']:
            raise SeparacaoFilialError('A separação ainda possui bloqueios: ' + '; '.join(simulacao['bloqueios']))
        if processo.status == SeparacaoFilial.Status.CONCLUIDO:
            return processo

        try:
            cls._atualizar_progresso(processo, 5, 'Validando a separação')
            with transaction.atomic(using='default'):
                processo = SeparacaoFilial.objects.select_for_update().get(pk=processo.pk)
                if processo.status == SeparacaoFilial.Status.CONCLUIDO:
                    return processo
                simulacao = cls.simular(processo)
                if simulacao['bloqueios']:
                    raise SeparacaoFilialError(
                        'A separação ainda possui bloqueios: ' + '; '.join(simulacao['bloqueios'])
                    )

                processo.status = SeparacaoFilial.Status.EXECUTANDO
                processo.confirmado_por = usuario if getattr(usuario, 'is_authenticated', False) else None
                processo.save(update_fields=['status', 'confirmado_por', 'updated_at'])

            cls._atualizar_progresso(processo, 15, 'Gerando backups obrigatórios')
            filial_pre = Filial.objects.select_related('empresa').get(pk=processo.filial_origem_id)
            empresa_origem_pre = processo.empresa_origem
            banco_origem_pre = EmpresaBanco.objects.filter(empresa=empresa_origem_pre).first()
            if processo.backup_path and processo.backup_exportacao_path:
                backups = {
                    'restauracao': processo.backup_path,
                    'exportacao': processo.backup_exportacao_path,
                }
            else:
                backups = cls._gerar_backups_pre_separacao(
                    processo, filial_pre, banco_origem_pre, simulacao
                )

            cls._atualizar_progresso(processo, 35, 'Separando empresa, filial e usuários')
            usuarios_revisao = []
            with transaction.atomic(using='default'):
                processo = SeparacaoFilial.objects.select_for_update().get(pk=processo.pk)
                filial = Filial.objects.select_for_update().select_related('empresa').get(pk=processo.filial_origem_id)
                empresa_origem = processo.empresa_origem
                banco_origem = EmpresaBanco.objects.filter(empresa=empresa_origem).first()
                antes_filial = snapshot_modelo(filial)

                empresa_destino = processo.empresa_destino or cls._criar_empresa_destino(filial)
                perfis_map = cls._clonar_perfis(empresa_origem, empresa_destino)
                cls._copiar_politica_empresa(empresa_origem, empresa_destino)

                if filial.empresa_id != empresa_destino.pk:
                    filial.empresa = empresa_destino
                    filial.is_matriz = True
                    filial.ativo = True
                    filial.participa_replicacao = False
                    filial.save(update_fields=[
                        'empresa',
                        'is_matriz',
                        'ativo',
                        'participa_replicacao',
                        'updated_at',
                    ])

                    cls._desativar_politicas_apos_separacao(filial, empresa_destino)
                    usuarios_revisao = cls._ajustar_usuarios_filial(filial, perfis_map)
                    SuperAdminAccessService.garantir_para_filiais([filial])
                    cls._garantir_matriz_origem(empresa_origem)

                banco, _ = EmpresaBancoService.ensure_for_empresa(empresa_destino, usuario)
                processo.empresa_destino = empresa_destino
                processo.banco_destino = banco
                processo.save(update_fields=['empresa_destino', 'banco_destino', 'updated_at'])

            cls._atualizar_progresso(processo, 65, 'Preparando o banco dedicado')
            cls._preparar_banco_destino(banco)
            banco.refresh_from_db()
            if (
                getattr(settings, 'TENANT_DATABASE_PROVISIONING_MODE', 'manual') != 'manual'
                and banco.status != EmpresaBanco.Status.ATIVO
            ):
                raise SeparacaoFilialError(
                    banco.ultimo_erro or 'O banco destino ainda nao ficou ativo. Tente retomar em instantes.'
                )

            cls._atualizar_progresso(processo, 82, 'Copiando dados operacionais da filial')
            copia_tenant = cls._copiar_dados_filial_tenant(
                banco_origem,
                banco,
                filial,
                empresa_origem=empresa_origem,
            )
            if banco.status == EmpresaBanco.Status.ATIVO and not copia_tenant.get('executado'):
                raise SeparacaoFilialError(
                    copia_tenant.get('motivo') or 'A copia dos dados operacionais nao foi executada.'
                )

            cls._atualizar_progresso(processo, 94, 'Registrando auditoria e relatório final')
            with transaction.atomic(using='default'):
                processo = SeparacaoFilial.objects.select_for_update().get(pk=processo.pk)

                registrar_auditoria(
                    request=request,
                    usuario=usuario,
                    filial=filial,
                    modulo='estoque',
                    acao='transferir',
                    objeto=filial,
                    descricao='Filial separada para nova empresa',
                    justificativa='Separação de filial para empresa independente.',
                    antes=antes_filial,
                    depois=snapshot_modelo(filial),
                    metadados={
                        'processo_id': processo.pk,
                        'empresa_origem_id': empresa_origem.pk,
                        'empresa_destino_id': empresa_destino.pk,
                        'banco_destino_id': banco.pk,
                        'copia_tenant': copia_tenant,
                        'backup_restauracao': backups.get('restauracao'),
                        'backup_exportacao': backups.get('exportacao'),
                    },
                )

                relatorio = cls._gerar_relatorio_final(
                    processo=processo,
                    filial=filial,
                    empresa_origem=empresa_origem,
                    empresa_destino=empresa_destino,
                    banco_origem=banco_origem,
                    banco_destino=banco,
                    simulacao=simulacao,
                    backups=backups,
                    copia_tenant=copia_tenant,
                    usuarios_revisao=usuarios_revisao,
                )
                processo.relatorio_json = relatorio
                processo.rollback_json = relatorio.get('rollback', {})
                processo.status = SeparacaoFilial.Status.CONCLUIDO
                processo.ultimo_erro = ''
                processo.etapa_atual = 'Separação concluída'
                processo.progresso_percentual = 100
                processo.concluido_em = timezone.now()
                processo.save(update_fields=[
                    'relatorio_json',
                    'rollback_json',
                    'status',
                    'ultimo_erro',
                    'etapa_atual',
                    'progresso_percentual',
                    'concluido_em',
                    'updated_at',
                ])
                return processo
        except Exception as exc:
            processo.status = SeparacaoFilial.Status.ERRO
            processo.ultimo_erro = str(exc)
            processo.etapa_atual = 'Erro na separação'
            processo.save(update_fields=['status', 'ultimo_erro', 'etapa_atual', 'updated_at'])
            if isinstance(exc, SeparacaoFilialError):
                raise
            raise SeparacaoFilialError(str(exc)) from exc

    @classmethod
    def confirmar_execucao(cls, processo, usuario=None):
        if processo.status == SeparacaoFilial.Status.CONCLUIDO:
            return processo
        simulacao = cls.simular(processo)
        if simulacao['bloqueios']:
            raise SeparacaoFilialError('A separação ainda possui bloqueios: ' + '; '.join(simulacao['bloqueios']))
        processo.confirmado_por = usuario if getattr(usuario, 'is_authenticated', False) else None
        processo.etapa_atual = 'Aguardando início da separação'
        processo.progresso_percentual = max(processo.progresso_percentual, 3)
        processo.save(update_fields=['confirmado_por', 'etapa_atual', 'progresso_percentual', 'updated_at'])
        return processo

    @classmethod
    def enfileirar_execucao(cls, processo, usuario=None):
        processo = SeparacaoFilial.objects.select_related('confirmado_por').get(pk=processo.pk)
        if processo.status == SeparacaoFilial.Status.CONCLUIDO:
            return processo
        if processo.status == SeparacaoFilial.Status.EXECUTANDO:
            return processo

        simulacao = cls.simular(processo)
        if simulacao['bloqueios']:
            raise SeparacaoFilialError('A separação ainda possui bloqueios: ' + '; '.join(simulacao['bloqueios']))

        usuario_id = usuario.pk if getattr(usuario, 'is_authenticated', False) else None
        processo.status = SeparacaoFilial.Status.EXECUTANDO
        processo.confirmado_por = usuario if getattr(usuario, 'is_authenticated', False) else processo.confirmado_por
        processo.etapa_atual = 'Separação enfileirada'
        processo.progresso_percentual = max(processo.progresso_percentual, 4)
        processo.ultimo_erro = ''
        processo.despachado_em = timezone.now()
        processo.executor_backend = ''
        processo.task_id = ''
        processo.save(update_fields=[
            'status',
            'confirmado_por',
            'etapa_atual',
            'progresso_percentual',
            'ultimo_erro',
            'despachado_em',
            'executor_backend',
            'task_id',
            'updated_at',
        ])

        modo = getattr(settings, 'SEPARACAO_FILIAL_ASYNC_MODE', 'celery')
        if modo == 'inline':
            processo.executor_backend = 'inline'
            processo.save(update_fields=['executor_backend', 'updated_at'])
            cls.executar(processo, usuario)
            processo.refresh_from_db()
            return processo
        if modo == 'thread':
            processo.status = SeparacaoFilial.Status.ERRO
            processo.etapa_atual = 'Executor inseguro desativado'
            processo.ultimo_erro = (
                'Execucao em thread foi desativada por seguranca. Configure o worker Celery.'
            )
            processo.save(update_fields=['status', 'etapa_atual', 'ultimo_erro', 'updated_at'])
            raise SeparacaoFilialError(
                processo.ultimo_erro
            )

        try:
            from apps.core.tasks import executar_separacao_filial

            result = executar_separacao_filial.apply_async(
                args=[processo.pk, usuario_id],
                retry=False,
            )
            processo.task_id = result.id or ''
            processo.executor_backend = 'celery'
            processo.save(update_fields=['task_id', 'executor_backend', 'updated_at'])
        except Exception as exc:
            processo.status = SeparacaoFilial.Status.ERRO
            processo.ultimo_erro = f'Nao foi possivel enfileirar no worker Celery: {exc}'
            processo.etapa_atual = 'Fila indisponivel'
            processo.save(update_fields=['status', 'ultimo_erro', 'etapa_atual', 'updated_at'])
            raise SeparacaoFilialError(processo.ultimo_erro) from exc
        processo.refresh_from_db()
        return processo

    @classmethod
    def executar_por_id(cls, processo_id, usuario_id=None):
        close_old_connections()
        try:
            processo = SeparacaoFilial.objects.get(pk=processo_id)
            if getattr(settings, 'TENANT_DATABASE_PROVISIONING_MODE', 'manual') == 'railway_api':
                try:
                    from apps.core.services.railway_provisioner import RailwayProvisioner

                    RailwayProvisioner.sync_tenant_variables_from_control_app()
                except Exception as exc:
                    processo.status = SeparacaoFilial.Status.ERRO
                    processo.etapa_atual = 'Falha ao carregar conexões dos bancos'
                    processo.ultimo_erro = str(exc)
                    processo.save(update_fields=[
                        'status', 'etapa_atual', 'ultimo_erro', 'updated_at',
                    ])
                    raise SeparacaoFilialError(processo.ultimo_erro) from exc
            usuario = None
            if usuario_id:
                usuario = get_user_model().objects.filter(pk=usuario_id).first()
            return cls.executar(processo, usuario)
        finally:
            close_old_connections()

    @classmethod
    def _despachar_thread(cls, processo_id, usuario_id=None):
        SeparacaoFilial.objects.filter(pk=processo_id).update(
            executor_backend='thread',
            etapa_atual='Separação em execução local',
            updated_at=timezone.now(),
        )
        thread = threading.Thread(
            target=cls.executar_por_id,
            args=(processo_id, usuario_id),
            name=f'separacao-filial-{processo_id}',
            daemon=True,
        )
        thread.start()

    @classmethod
    def payload_progresso(cls, processo):
        processo.refresh_from_db()
        simulacao = processo.simulacao_json or {}
        relatorio = processo.relatorio_json or {}
        return {
            'id': processo.pk,
            'filial': simulacao.get('filial', {}).get('razao_social') or str(processo.filial_origem),
            'empresa_origem': simulacao.get('empresa_origem', {}).get('razao_social') or str(processo.empresa_origem),
            'empresa_destino': relatorio.get('empresa_destino', {}).get('razao_social') or '',
            'banco_destino': relatorio.get('banco_destino', {}).get('alias') or '',
            'status': processo.status,
            'status_label': processo.get_status_display(),
            'stage_label': processo.etapa_atual or processo.get_status_display(),
            'progress': processo.progresso_percentual,
            'executor_backend': processo.executor_backend,
            'task_id': processo.task_id,
            'ready': processo.status == SeparacaoFilial.Status.CONCLUIDO,
            'error': processo.status == SeparacaoFilial.Status.ERRO,
            'detail': processo.ultimo_erro,
            'backup_restauracao': bool(processo.backup_path),
            'backup_exportacao': bool(processo.backup_exportacao_path),
            'relatorio': relatorio,
        }

    @classmethod
    def _atualizar_progresso(cls, processo, percentual, etapa):
        SeparacaoFilial.objects.filter(pk=processo.pk).update(
            progresso_percentual=max(0, min(100, percentual)),
            etapa_atual=etapa,
            updated_at=timezone.now(),
        )
        processo.progresso_percentual = max(0, min(100, percentual))
        processo.etapa_atual = etapa

    @classmethod
    def _gerar_backups_pre_separacao(cls, processo, filial, banco_origem, simulacao):
        timestamp = timezone.now().strftime('%Y%m%dT%H%M%SZ')
        base_name = f'separacao_filial_{filial.pk}_{timestamp}'
        central_sql = cls._gerar_sql_restauracao_central(filial)
        export_json = cls._gerar_exportacao_dados(filial, simulacao)

        with TemporaryDirectory() as tmpdir:
            backup_dir = Path(tmpdir)
            restore_zip = backup_dir / f'{base_name}_restauracao.zip'
            export_path = backup_dir / f'{base_name}_exportacao.json'
            with zipfile.ZipFile(restore_zip, 'w', compression=zipfile.ZIP_DEFLATED) as pacote:
                pacote.writestr('LEIA-ME.txt', cls._texto_readme_restauracao(filial, banco_origem))
                pacote.writestr('central_restore.sql', central_sql)
                if banco_origem:
                    try:
                        tenant_filename, tenant_path = EmpresaBancoService.gerar_backup_completo_em_arquivo(
                            banco_origem, backup_dir,
                        )
                        pacote.write(tenant_path, f'banco_origem_{tenant_filename}')
                        tenant_path.unlink(missing_ok=True)
                    except Exception as exc:
                        pacote.writestr(
                            'banco_origem_nao_exportado.txt',
                            'Não foi possível gerar o pacote SQL/CSV do banco dedicado de origem.\n'
                            f'Motivo: {exc}\n'
                            'O backup central e a exportação JSON foram gerados normalmente.\n',
                        )
                else:
                    pacote.writestr(
                        'banco_origem_nao_existia.txt',
                        'A empresa origem não possuía banco dedicado registrado antes da separação.\n',
                    )
            export_path.write_text(
                json.dumps(export_json, ensure_ascii=False, indent=2, cls=DjangoJSONEncoder),
                encoding='utf-8',
            )
            prefixo = 'backups/separacoes_filiais'
            with restore_zip.open('rb') as arquivo:
                restore_name = default_storage.save(
                    f'{prefixo}/{restore_zip.name}', File(arquivo),
                )
            try:
                with export_path.open('rb') as arquivo:
                    export_name = default_storage.save(
                        f'{prefixo}/{export_path.name}', File(arquivo),
                    )
            except Exception:
                default_storage.delete(restore_name)
                raise

        processo.backup_path = restore_name
        processo.backup_exportacao_path = export_name
        processo.save(update_fields=['backup_path', 'backup_exportacao_path', 'updated_at'])
        return {
            'restauracao': restore_name,
            'exportacao': export_name,
        }

    @classmethod
    def _texto_readme_restauracao(cls, filial, banco_origem):
        banco_label = banco_origem.db_alias if banco_origem else 'sem banco dedicado de origem'
        return (
            'Backup obrigatório gerado antes da separação de filial.\n\n'
            f'Filial: {filial.razao_social} ({filial.cnpj})\n'
            f'Empresa origem: {filial.empresa.razao_social}\n'
            f'Banco origem: {banco_label}\n\n'
            'Arquivos:\n'
            '- central_restore.sql: registros centrais afetados antes da separação.\n'
            '- banco_origem_backup_*.zip: SQL restaurável, CSVs por tabela e manifesto do banco de origem.\n\n'
            'Use este pacote apenas em procedimento assistido de restauração. '
            'Antes de restaurar, faça backup do estado atual e valide o banco de destino.\n'
        )

    @classmethod
    def _gerar_sql_restauracao_central(cls, filial):
        queries = cls._querysets_backup_central(filial)
        connection = connections['default']
        partes = [
            '-- Backup central para restauração assistida da separação de filial',
            f'-- Filial: {filial.pk} - {filial.razao_social}',
            f'-- Gerado em: {timezone.now().isoformat()}',
            '-- Revise antes de executar em produção.',
            '',
            'BEGIN;',
            'SET CONSTRAINTS ALL DEFERRED;',
            '',
        ]
        for model, queryset in queries:
            partes.extend(cls._sql_upsert_queryset(connection, model, queryset))
        partes.extend(['COMMIT;', ''])
        return '\n'.join(partes)

    @classmethod
    def _querysets_backup_central(cls, filial):
        empresa = filial.empresa
        acessos = UsuarioFilialAcesso.objects.filter(filial=filial).select_related('usuario', 'perfil')
        usuario_ids = list(acessos.values_list('usuario_id', flat=True))
        perfil_ids = list(
            PerfilAcesso.objects.filter(empresa=empresa).values_list('pk', flat=True)
        )
        queries = [
            (Empresa, Empresa.objects.filter(pk=empresa.pk)),
            (Filial, Filial.objects.filter(empresa=empresa)),
            (PerfilAcesso, PerfilAcesso.objects.filter(pk__in=perfil_ids)),
            (Permissao, Permissao.objects.filter(perfil_id__in=perfil_ids)),
            (Usuario, Usuario.objects.filter(pk__in=usuario_ids)),
            (UsuarioFilialAcesso, UsuarioFilialAcesso.objects.filter(usuario_id__in=usuario_ids)),
            (PoliticaReplicacao, PoliticaReplicacao.objects.filter(empresa=empresa)),
            (EmpresaBanco, EmpresaBanco.objects.filter(empresa=empresa)),
        ]
        politica_filial_model = apps.get_model('core', 'PoliticaReplicacaoFilial')
        queries.append((politica_filial_model, politica_filial_model.objects.filter(filial__empresa=empresa)))
        return queries

    @classmethod
    def _sql_upsert_queryset(cls, connection, model, queryset):
        qn = connection.ops.quote_name
        opts = model._meta
        rows = list(queryset.order_by(opts.pk.name))
        if not rows:
            return []
        linhas = [f'-- {opts.label_lower} ({len(rows)} registro(s))']
        fields = list(opts.concrete_fields)
        colunas = [field.column for field in fields]
        colunas_sql = ', '.join(qn(coluna) for coluna in colunas)
        pk_col = opts.pk.column
        updates = ', '.join(
            f'{qn(coluna)} = EXCLUDED.{qn(coluna)}'
            for coluna in colunas
            if coluna != pk_col
        )
        for obj in rows:
            valores = ', '.join(
                EmpresaBancoService._sql_literal(field.value_from_object(obj))
                for field in fields
            )
            if updates:
                linhas.append(
                    f'INSERT INTO {qn(opts.db_table)} ({colunas_sql}) VALUES ({valores}) '
                    f'ON CONFLICT ({qn(pk_col)}) DO UPDATE SET {updates};'
                )
            else:
                linhas.append(
                    f'INSERT INTO {qn(opts.db_table)} ({colunas_sql}) VALUES ({valores}) '
                    f'ON CONFLICT ({qn(pk_col)}) DO NOTHING;'
                )
        linhas.append('')
        return linhas

    @classmethod
    def _gerar_exportacao_dados(cls, filial, simulacao):
        modelos = []
        for model in cls._modelos_para_exportacao(filial):
            rows = []
            for obj in cls._queryset_exportacao(model, filial).order_by('pk').iterator(chunk_size=500):
                rows.append({
                    'pk': obj.pk,
                    'fields': {
                        field.name: cls._valor_exportavel(field.value_from_object(obj))
                        for field in model._meta.concrete_fields
                    },
                })
            if rows:
                modelos.append({
                    'app': model._meta.app_label,
                    'modelo': model._meta.model_name,
                    'label': model._meta.label_lower,
                    'tabela': model._meta.db_table,
                    'total': len(rows),
                    'registros': rows,
                })
        return {
            'tipo': 'exportacao_dados_filial',
            'gerado_em': timezone.now().isoformat(),
            'filial': simulacao.get('filial', {}),
            'empresa_origem': simulacao.get('empresa_origem', {}),
            'usuarios': simulacao.get('usuarios', {}),
            'auditoria_modulos': simulacao.get('auditoria_modulos', []),
            'modelos': modelos,
        }

    @classmethod
    def _modelos_para_exportacao(cls, filial):
        modelos = [Empresa, Filial, PerfilAcesso, Permissao, Usuario, UsuarioFilialAcesso]
        modelos.extend(cls._modelos_para_copia_operacional())
        vistos = set()
        unicos = []
        for model in modelos:
            label = model._meta.label_lower
            if label not in vistos:
                vistos.add(label)
                unicos.append(model)
        return unicos

    @classmethod
    def _queryset_exportacao(cls, model, filial):
        if model is Empresa:
            return model.objects.filter(pk=filial.empresa_id)
        if model is Filial:
            return model.objects.filter(pk=filial.pk)
        if model is PerfilAcesso:
            return model.objects.filter(empresa=filial.empresa)
        if model is Permissao:
            return model.objects.filter(perfil__empresa=filial.empresa)
        if model is Usuario:
            usuarios = UsuarioFilialAcesso.objects.filter(filial=filial).values('usuario_id')
            return model.objects.filter(pk__in=usuarios)
        if model is UsuarioFilialAcesso:
            return model.objects.filter(filial=filial)
        field_names = {field.name for field in model._meta.concrete_fields}
        if 'filial' in field_names:
            return model._default_manager.filter(filial=filial)
        return model._default_manager.none()

    @staticmethod
    def _valor_exportavel(value):
        if hasattr(value, 'name') and hasattr(value, 'storage'):
            return value.name
        return value

    @classmethod
    def _auditoria_modulos(cls, contagens, operacoes_abertas):
        abertos_por_app = {}
        for item in operacoes_abertas:
            app = item['label'].split('.', 1)[0]
            abertos_por_app[app] = abertos_por_app.get(app, 0) + item['total']
        totais_por_app = {}
        for item in contagens:
            app = item['app']
            totais_por_app[app] = totais_por_app.get(app, 0) + item['total']
        apps_labels = sorted(set(totais_por_app) | set(abertos_por_app))
        return [
            {
                'app': app,
                'total_registros': totais_por_app.get(app, 0),
                'operacoes_abertas': abertos_por_app.get(app, 0),
                'status': 'bloqueado' if abertos_por_app.get(app, 0) else 'ok',
            }
            for app in apps_labels
        ]

    @classmethod
    def _gerar_relatorio_final(
        cls,
        processo,
        filial,
        empresa_origem,
        empresa_destino,
        banco_origem,
        banco_destino,
        simulacao,
        backups,
        copia_tenant,
        usuarios_revisao,
    ):
        rollback = {
            'tipo': 'assistido',
            'automatico': False,
            'motivo': (
                'Rollback automático não é aplicado por botão para evitar sobrescrever dados novos. '
                'Use o pacote de restauração com acompanhamento do suporte.'
            ),
            'backup_restauracao': backups.get('restauracao', ''),
            'passos': [
                'Bloquear novos acessos temporariamente.',
                'Gerar backup do estado atual antes de restaurar.',
                'Restaurar o SQL central do pacote de restauração.',
                'Restaurar o SQL do banco de origem, se o pacote contiver esse arquivo.',
                'Validar usuários, filial matriz, estoque, financeiro e documentos fiscais.',
            ],
        }
        return {
            'processo_id': processo.pk,
            'concluido_em': timezone.now().isoformat(),
            'filial': {
                'id': filial.pk,
                'razao_social': filial.razao_social,
                'cnpj': filial.cnpj,
                'empresa_id': filial.empresa_id,
            },
            'empresa_origem': {
                'id': empresa_origem.pk,
                'razao_social': empresa_origem.razao_social,
            },
            'empresa_destino': {
                'id': empresa_destino.pk,
                'razao_social': empresa_destino.razao_social,
                'cnpj': empresa_destino.cnpj,
            },
            'banco_origem': {
                'id': banco_origem.pk if banco_origem else None,
                'alias': banco_origem.db_alias if banco_origem else '',
            },
            'banco_destino': {
                'id': banco_destino.pk,
                'alias': banco_destino.db_alias,
                'status': banco_destino.status,
            },
            'usuarios': simulacao.get('usuarios', {}),
            'usuarios_revisao_manual': usuarios_revisao,
            'auditoria_modulos': simulacao.get('auditoria_modulos', []),
            'contagens': simulacao.get('contagens', []),
            'copia_tenant': copia_tenant,
            'backups': backups,
            'rollback': rollback,
        }

    @classmethod
    def _plano_empresa_destino(cls, filial):
        documento = ''.join(ch for ch in (filial.cnpj or '') if ch.isdigit())
        nome = slugify(filial.razao_social or filial.nome_fantasia or documento)
        nome = re.sub(r'[^a-z0-9-]', '', nome).strip('-') or 'empresa'
        alias = f'empresa_{nome.replace("-", "_")}_{documento}' if documento else f'empresa_{nome.replace("-", "_")}'
        return {
            'razao_social': filial.razao_social,
            'nome_fantasia': filial.nome_fantasia,
            'cnpj': documento,
            'db_alias_previsto': alias[:80],
            'acao_filial': 'Mover a filial preservando o ID e o histórico',
            'usuarios': 'Mover usuários exclusivos e bloquear usuários com acesso a outras filiais',
        }

    @classmethod
    def _info_bancos(cls, filial):
        origem = EmpresaBanco.objects.filter(empresa=filial.empresa).first()
        return {
            'origem': origem.db_alias if origem else '',
            'origem_status': origem.status if origem else 'sem_banco',
        }

    @classmethod
    def _impacto_usuarios(cls, filial):
        acessos = list(
            UsuarioFilialAcesso.objects
            .filter(filial=filial, ativo=True)
            .select_related('usuario', 'perfil')
            .order_by('usuario__nome')
        )
        exclusivos = []
        multifiliais = []
        superadmins = []
        for acesso in acessos:
            usuario = acesso.usuario
            if usuario.is_superuser:
                superadmins.append({'id': usuario.pk, 'email': usuario.email, 'nome': usuario.nome})
                continue
            outros = UsuarioFilialAcesso.objects.filter(
                usuario=usuario,
                ativo=True,
            ).exclude(filial=filial).count()
            item = {'id': usuario.pk, 'email': usuario.email, 'nome': usuario.nome, 'outros_acessos': outros}
            if outros:
                multifiliais.append(item)
            else:
                exclusivos.append(item)
        return {
            'total_acessos': len(acessos),
            'exclusivos': exclusivos,
            'multifiliais': multifiliais,
            'superadmins': superadmins,
        }

    @classmethod
    def _contagens_por_filial(cls, filial):
        contagens = []
        for model in apps.get_models():
            if not model._meta.managed or model._meta.proxy:
                continue
            field_names = {field.name for field in model._meta.concrete_fields}
            if 'filial' not in field_names:
                continue
            try:
                total = model._default_manager.filter(filial=filial).count()
            except Exception:
                continue
            if total:
                contagens.append({
                    'app': model._meta.app_label,
                    'modelo': model._meta.verbose_name_plural.title(),
                    'label': model._meta.label_lower,
                    'total': total,
                })
        return sorted(contagens, key=lambda item: (item['app'], item['modelo']))

    @classmethod
    def _operacoes_abertas(cls, filial):
        abertas = []
        for model in apps.get_models():
            if not model._meta.managed or model._meta.proxy:
                continue
            fields = {field.name: field for field in model._meta.concrete_fields}
            if 'filial' not in fields:
                continue
            status_fields = [name for name in ('status', 'situacao', 'estado') if name in fields]
            if not status_fields:
                continue
            query = Q()
            for field_name in status_fields:
                query |= Q(**{f'{field_name}__in': cls.STATUS_ABERTOS})
            try:
                total = model._default_manager.filter(filial=filial).filter(query).count()
            except Exception:
                continue
            if total:
                abertas.append({
                    'label': model._meta.label_lower,
                    'modelo': model._meta.verbose_name_plural.title(),
                    'total': total,
                })
        return abertas

    @classmethod
    def _criar_empresa_destino(cls, filial):
        documento = ''.join(ch for ch in (filial.cnpj or '') if ch.isdigit())
        defaults = {
            'razao_social': filial.razao_social,
            'nome_fantasia': filial.nome_fantasia,
            'inscricao_estadual': filial.inscricao_estadual,
            'inscricao_municipal': filial.inscricao_municipal,
            'regime_tributario': filial.regime_tributario or filial.empresa.regime_tributario,
            'codigo_regime_tributario': filial.codigo_regime_tributario or filial.empresa.codigo_regime_tributario,
            'endereco': filial.endereco,
            'numero': filial.numero,
            'complemento': filial.complemento,
            'bairro': filial.bairro,
            'cidade': filial.cidade,
            'uf': filial.uf,
            'cep': filial.cep,
            'codigo_municipio_ibge': filial.codigo_municipio_ibge,
            'telefone': filial.telefone,
            'email': filial.email,
            'ambiente_nfe': filial.ambiente_nfe,
            'ativo': True,
        }
        empresa, created = Empresa.objects.get_or_create(cnpj=documento, defaults=defaults)
        if not created and empresa.pk != filial.empresa_id:
            raise SeparacaoFilialError('Já existe outra empresa com o CNPJ desta filial.')
        if not created and empresa.pk == filial.empresa_id:
            raise SeparacaoFilialError('O CNPJ da filial é igual ao da empresa atual.')
        return empresa

    @classmethod
    def _clonar_perfis(cls, empresa_origem, empresa_destino):
        perfis_map = {}
        for perfil in PerfilAcesso.objects.filter(empresa=empresa_origem).order_by('pk'):
            novo, _ = PerfilAcesso.objects.get_or_create(
                empresa=empresa_destino,
                nome=perfil.nome,
                defaults={
                    'descricao': perfil.descricao,
                    'is_admin': perfil.is_admin,
                    'ativo': perfil.ativo,
                },
            )
            perfis_map[perfil.pk] = novo
            for permissao in Permissao.objects.filter(perfil=perfil).order_by('pk'):
                Permissao.objects.update_or_create(
                    perfil=novo,
                    modulo=permissao.modulo,
                    defaults={
                        'pode_ver': permissao.pode_ver,
                        'pode_criar': permissao.pode_criar,
                        'pode_editar': permissao.pode_editar,
                        'pode_excluir': permissao.pode_excluir,
                        'pode_cancelar': permissao.pode_cancelar,
                        'pode_aprovar': permissao.pode_aprovar,
                        'pode_exportar': permissao.pode_exportar,
                    },
                )
        return perfis_map

    @classmethod
    def _copiar_politica_empresa(cls, empresa_origem, empresa_destino):
        try:
            politica = empresa_origem.politica_replicacao
        except PoliticaReplicacao.DoesNotExist:
            return
        data = {
            field.name: getattr(politica, field.name)
            for field in PoliticaReplicacao._meta.concrete_fields
            if field.name not in {'id', 'empresa', 'created_at', 'updated_at'}
        }
        PoliticaReplicacao.objects.update_or_create(empresa=empresa_destino, defaults=data)

    @classmethod
    def _desativar_politicas_apos_separacao(cls, filial, empresa):
        campos = {
            field.name: False
            for field in PoliticaReplicacaoFilial._meta.concrete_fields
            if field.name.startswith('replicar_')
        }
        campos.update({'ativo': False, 'perguntar_ao_salvar': False})
        PoliticaReplicacaoFilial.objects.update_or_create(filial=filial, defaults=campos)

        campos_empresa = {
            field.name: False
            for field in PoliticaReplicacao._meta.concrete_fields
            if field.name.startswith('replicar_')
        }
        campos_empresa.update({'ativo': False, 'perguntar_ao_salvar': False})
        PoliticaReplicacao.objects.update_or_create(empresa=empresa, defaults=campos_empresa)

    @classmethod
    def _ajustar_usuarios_filial(cls, filial, perfis_map):
        acessos = UsuarioFilialAcesso.objects.select_related('usuario', 'perfil').filter(filial=filial, ativo=True)
        revisao_manual = []
        for acesso in acessos:
            usuario = acesso.usuario
            if usuario.is_superuser:
                continue
            outros = list(
                UsuarioFilialAcesso.objects
                .filter(usuario=usuario, ativo=True)
                .exclude(filial=filial)
                .select_related('filial__empresa', 'perfil')
                .order_by('-is_padrao', 'filial__razao_social')
            )
            if outros:
                novo_perfil = perfis_map.get(acesso.perfil_id)
                if not novo_perfil:
                    novo_perfil = PerfilAcesso.objects.filter(
                        empresa=filial.empresa,
                        is_admin=acesso.perfil.is_admin,
                    ).order_by('nome').first()
                if novo_perfil:
                    acesso.perfil = novo_perfil
                acesso.ativo = False
                acesso.is_padrao = False
                acesso.save(update_fields=['perfil', 'ativo', 'is_padrao', 'updated_at'])
                acesso_restante = outros[0]
                if usuario.filial_id == filial.pk:
                    usuario.empresa = acesso_restante.filial.empresa
                    usuario.filial = acesso_restante.filial
                    usuario.perfil = acesso_restante.perfil
                    usuario.save(update_fields=['empresa', 'filial', 'perfil', 'updated_at'])
                revisao_manual.append({
                    'id': usuario.pk,
                    'nome': usuario.nome,
                    'email': usuario.email,
                    'motivo': 'Acesso suspenso na empresa separada; demais acessos foram mantidos.',
                    'acessos_mantidos': [
                        {
                            'empresa': item.filial.empresa.nome_fantasia,
                            'filial': item.filial.nome_fantasia,
                            'perfil': item.perfil.nome,
                        }
                        for item in outros
                    ],
                })
                continue

            novo_perfil = perfis_map.get(acesso.perfil_id)
            if not novo_perfil:
                novo_perfil = PerfilAcesso.objects.filter(empresa=filial.empresa, is_admin=True).first()
            if novo_perfil:
                acesso.perfil = novo_perfil
                acesso.save(update_fields=['perfil', 'updated_at'])
            usuario.empresa = filial.empresa
            usuario.filial = filial
            if novo_perfil:
                usuario.perfil = novo_perfil
            usuario.save(update_fields=['empresa', 'filial', 'perfil', 'updated_at'])
        return revisao_manual

    @classmethod
    def _garantir_matriz_origem(cls, empresa_origem):
        if Filial.objects.filter(empresa=empresa_origem, ativo=True, is_matriz=True).exists():
            return
        proxima = Filial.objects.filter(empresa=empresa_origem, ativo=True).order_by('razao_social').first()
        if proxima:
            proxima.is_matriz = True
            proxima.save(update_fields=['is_matriz', 'updated_at'])

    @classmethod
    def _preparar_banco_destino(cls, banco):
        if banco.status == EmpresaBanco.Status.PENDENTE:
            EmpresaBancoService.solicitar_provisionamento(banco)
            banco.refresh_from_db()
        if banco.status in [EmpresaBanco.Status.CONFIGURADO, EmpresaBanco.Status.ATIVO]:
            EmpresaBancoService.migrar_banco(banco, verbosity=0)
        elif getattr(settings, 'TENANT_DATABASE_PROVISIONING_MODE', 'manual') == 'manual':
            # Em modo manual, o banco fica registrado para configuração externa.
            return
        elif register_tenant_database(banco):
            EmpresaBancoService.migrar_banco(banco, verbosity=0)

    @classmethod
    def _copiar_dados_filial_tenant(
        cls,
        banco_origem,
        banco_destino,
        filial,
        empresa_origem=None,
    ):
        if not banco_origem or not banco_destino:
            return {'executado': False, 'motivo': 'Banco de origem ou destino ausente.'}
        if banco_origem.pk == banco_destino.pk:
            return {'executado': False, 'motivo': 'Banco de origem e destino são iguais.'}
        if not register_tenant_database(banco_origem):
            return {'executado': False, 'motivo': 'Banco de origem sem conexão registrada.'}
        if not register_tenant_database(banco_destino):
            return {'executado': False, 'motivo': 'Banco de destino sem conexão registrada.'}

        source_alias = banco_origem.db_alias
        target_alias = banco_destino.db_alias
        source_config = connections[source_alias].settings_dict
        target_config = connections[target_alias].settings_dict
        source_location = tuple(source_config.get(key) for key in ('ENGINE', 'HOST', 'PORT', 'NAME'))
        target_location = tuple(target_config.get(key) for key in ('ENGINE', 'HOST', 'PORT', 'NAME'))
        if source_location == target_location:
            return {
                'executado': False,
                'motivo': 'Banco de origem e destino apontam para a mesma instancia fisica.',
            }
        tabelas_origem = set(connections[source_alias].introspection.table_names())
        tabelas_destino = set(connections[target_alias].introspection.table_names())
        modelos = [
            model for model in cls._modelos_para_copia_operacional()
            if model._meta.db_table in tabelas_origem and model._meta.db_table in tabelas_destino
        ]
        pks_por_modelo = cls._selecionar_pks_copia(modelos, source_alias, filial.pk)
        total = 0
        por_modelo = []
        remapeados = 0
        empresa_origem_id = getattr(empresa_origem, 'pk', None)

        with transaction.atomic(using=target_alias):
            usuarios_historicos = cls._sincronizar_usuarios_referenciados(
                modelos,
                pks_por_modelo,
                source_alias,
                target_alias,
                filial,
            )
            for model in modelos:
                pks = pks_por_modelo.get(model._meta.label_lower, set())
                if not pks:
                    continue
                copied = 0
                queryset = model._default_manager.using(source_alias).filter(pk__in=pks)
                for obj in queryset.order_by('pk').iterator(chunk_size=500):
                    data, foi_remapeado = cls._dados_objeto_separado(
                        obj,
                        filial,
                        empresa_origem_id,
                    )
                    destino_qs = model._default_manager.using(target_alias).filter(pk=obj.pk)
                    existente = destino_qs.first()
                    if existente:
                        cls._validar_colisao_identidade(model, existente, obj)
                        destino_qs.update(**{
                            key: value for key, value in data.items()
                            if key != model._meta.pk.attname
                        })
                    else:
                        model(**data).save_base(
                            raw=True,
                            using=target_alias,
                            force_insert=True,
                        )
                    copied += 1
                    remapeados += int(foi_remapeado)
                if copied:
                    label = model._meta.label_lower
                    total += copied
                    por_modelo.append({'label': label, 'total': copied})

            cls._reset_sequences_destino(target_alias, modelos)

        return {
            'executado': True,
            'total': total,
            'modelos': por_modelo,
            'dependencias_compartilhadas_remapeadas': remapeados,
            'usuarios_historicos': usuarios_historicos,
        }

    @classmethod
    def _modelos_para_copia_operacional(cls):
        excluidos = {
            'core.empresa',
            'core.filial',
            'core.politicareplicacao',
            'core.politicareplicacaofilial',
            'core.perfilacesso',
            'core.permissao',
            'core.usuario',
            'core.usuariofilialacesso',
            'core.empresabanco',
            'core.separacaofilial',
            'contenttypes.contenttype',
            'sessions.session',
        }
        todos = [
            model for model in apps.get_models()
            if (
                model._meta.managed
                and not model._meta.proxy
                and model._meta.label_lower not in excluidos
                and model._meta.label_lower not in cls.MODELOS_NAO_COPIAR
            )
        ]
        incluidos = {
            model for model in todos
            if any(field.name == 'filial' for field in model._meta.concrete_fields)
        }
        changed = True
        while changed:
            changed = False
            for model in todos:
                pais = {
                    field.remote_field.model
                    for field in model._meta.concrete_fields
                    if getattr(field, 'many_to_one', False)
                    and field.remote_field
                    and field.remote_field.model in todos
                    and field.remote_field.model is not model
                }
                if model in incluidos:
                    novos_pais = pais - incluidos
                    if novos_pais:
                        incluidos.update(novos_pais)
                        changed = True
                elif pais & incluidos:
                    incluidos.add(model)
                    changed = True
        return cls._ordenar_modelos_por_dependencia(list(incluidos))

    @classmethod
    def _ordenar_modelos_por_dependencia(cls, modelos):
        modelos_set = set(modelos)
        ordenados = []
        pendentes = set(modelos)
        while pendentes:
            progresso = False
            for model in list(pendentes):
                deps = {
                    field.remote_field.model
                    for field in model._meta.concrete_fields
                    if getattr(field, 'many_to_one', False)
                    and field.remote_field
                    and field.remote_field.model in modelos_set
                    and field.remote_field.model is not model
                }
                if deps.issubset(set(ordenados)):
                    ordenados.append(model)
                    pendentes.remove(model)
                    progresso = True
            if not progresso:
                ordenados.extend(sorted(pendentes, key=lambda item: item._meta.label_lower))
                break
        return ordenados

    @classmethod
    def _selecionar_pks_copia(cls, modelos, source_alias, filial_id):
        tabelas_origem = set(connections[source_alias].introspection.table_names())
        modelos = [model for model in modelos if model._meta.db_table in tabelas_origem]
        modelos_set = set(modelos)
        selecionados = {model._meta.label_lower: set() for model in modelos}

        for model in modelos:
            if any(field.name == 'filial' for field in model._meta.concrete_fields):
                selecionados[model._meta.label_lower].update(
                    model._default_manager.using(source_alias)
                    .filter(filial_id=filial_id)
                    .values_list('pk', flat=True)
                )

        mudou = True
        while mudou:
            mudou = False
            for model in modelos:
                label = model._meta.label_lower
                pks = selecionados[label]

                if pks:
                    for field in model._meta.concrete_fields:
                        if (
                            not getattr(field, 'many_to_one', False)
                            or not field.remote_field
                            or field.remote_field.model not in modelos_set
                        ):
                            continue
                        parent = field.remote_field.model
                        parent_label = parent._meta.label_lower
                        parent_ids = set(
                            model._default_manager.using(source_alias)
                            .filter(pk__in=pks)
                            .exclude(**{f'{field.attname}__isnull': True})
                            .values_list(field.attname, flat=True)
                        )
                        novos = parent_ids - selecionados[parent_label]
                        if novos:
                            selecionados[parent_label].update(novos)
                            mudou = True

                possui_filial = any(
                    field.name == 'filial' for field in model._meta.concrete_fields
                )
                if possui_filial:
                    continue
                query = Q()
                encontrou_pai = False
                for field in model._meta.concrete_fields:
                    if (
                        not getattr(field, 'many_to_one', False)
                        or not field.remote_field
                        or field.remote_field.model not in modelos_set
                    ):
                        continue
                    parent_pks = selecionados[field.remote_field.model._meta.label_lower]
                    if parent_pks:
                        query |= Q(**{f'{field.attname}__in': parent_pks})
                        encontrou_pai = True
                if encontrou_pai:
                    filhos = set(
                        model._default_manager.using(source_alias)
                        .filter(query)
                        .values_list('pk', flat=True)
                    )
                    novos = filhos - pks
                    if novos:
                        pks.update(novos)
                        mudou = True

        return selecionados

    @classmethod
    def _dados_objeto_separado(cls, obj, filial_destino, empresa_origem_id):
        data = {}
        remapeado = False
        for field in obj._meta.concrete_fields:
            value = getattr(obj, field.attname)
            if getattr(field, 'many_to_one', False) and field.remote_field:
                remote_model = field.remote_field.model
                if remote_model is Empresa and empresa_origem_id and value == empresa_origem_id:
                    value = filial_destino.empresa_id
                    remapeado = True
                elif remote_model is Filial and value and value != filial_destino.pk:
                    if field.name == 'filial':
                        value = filial_destino.pk
                    elif field.null:
                        # Nao transforme uma referencia historica externa em
                        # uma operacao contra a propria filial separada.
                        value = None
                    else:
                        raise SeparacaoFilialError(
                            f'Referencia obrigatoria a outra filial em '
                            f'{obj._meta.label_lower}.{field.name}.'
                        )
                    remapeado = True
            data[field.attname] = value
        return data, remapeado

    @classmethod
    def _ids_usuarios_referenciados(cls, modelos, pks_por_modelo, source_alias):
        ids = set()
        for model in modelos:
            pks = pks_por_modelo.get(model._meta.label_lower, set())
            if not pks:
                continue
            for field in model._meta.concrete_fields:
                if (
                    not getattr(field, 'many_to_one', False)
                    or not field.remote_field
                    or field.remote_field.model is not Usuario
                ):
                    continue
                ids.update(
                    model._default_manager.using(source_alias)
                    .filter(pk__in=pks)
                    .exclude(**{f'{field.attname}__isnull': True})
                    .values_list(field.attname, flat=True)
                )
        return ids

    @classmethod
    def _sincronizar_usuarios_referenciados(
        cls,
        modelos,
        pks_por_modelo,
        source_alias,
        target_alias,
        filial_destino,
    ):
        ids = cls._ids_usuarios_referenciados(modelos, pks_por_modelo, source_alias)
        if not ids:
            return {'referenciados': 0, 'criados_inativos': 0}

        existentes = set(
            Usuario.objects.using(target_alias)
            .filter(pk__in=ids)
            .values_list('pk', flat=True)
        )
        pendentes = ids - existentes
        if not pendentes:
            return {'referenciados': len(ids), 'criados_inativos': 0}

        perfis_destino = list(
            PerfilAcesso.objects.using(target_alias)
            .filter(empresa_id=filial_destino.empresa_id)
            .order_by('-is_admin', 'nome', 'pk')
        )
        if not perfis_destino:
            raise SeparacaoFilialError(
                'O banco destino nao possui perfil para preservar os autores historicos.'
            )
        perfil_por_nome = {perfil.nome.casefold(): perfil.pk for perfil in perfis_destino}
        perfil_fallback_id = perfis_destino[0].pk
        nomes_perfis_origem = dict(
            Usuario.objects.using(source_alias)
            .filter(pk__in=pendentes)
            .values_list('pk', 'perfil__nome')
        )

        criados = 0
        for usuario in Usuario.objects.using(source_alias).filter(pk__in=pendentes).order_by('pk'):
            perfil_nome = nomes_perfis_origem.get(usuario.pk, '')
            data = {
                field.attname: getattr(usuario, field.attname)
                for field in Usuario._meta.concrete_fields
            }
            data.update({
                'empresa_id': filial_destino.empresa_id,
                'filial_id': filial_destino.pk,
                'perfil_id': perfil_por_nome.get(perfil_nome.casefold(), perfil_fallback_id),
                'password': '!',
                'ativo': False,
                'is_staff': False,
                'is_superuser': False,
            })
            Usuario(**data).save_base(
                raw=True,
                using=target_alias,
                force_insert=True,
            )
            criados += 1

        copiados = set(
            Usuario.objects.using(target_alias)
            .filter(pk__in=pendentes)
            .values_list('pk', flat=True)
        )
        if pendentes - copiados:
            raise SeparacaoFilialError(
                'Nao foi possivel preservar todos os autores historicos no banco destino.'
            )
        return {'referenciados': len(ids), 'criados_inativos': criados}

    @classmethod
    def _validar_colisao_identidade(cls, model, existente, origem):
        marcadores = ('replication_uuid', 'grupo_replicacao', 'id_externo')
        for nome in marcadores:
            try:
                model._meta.get_field(nome)
            except Exception:
                continue
            valor_existente = getattr(existente, nome, None)
            valor_origem = getattr(origem, nome, None)
            if valor_existente and valor_origem and valor_existente != valor_origem:
                raise SeparacaoFilialError(
                    f'Colisao de identidade em {model._meta.label_lower} PK {origem.pk}.'
                )

    @classmethod
    def _reset_sequences_destino(cls, target_alias, modelos):
        connection = connections[target_alias]
        if connection.vendor != 'postgresql':
            return
        sql_list = connection.ops.sequence_reset_sql(no_style(), modelos)
        if not sql_list:
            return
        with connection.cursor() as cursor:
            for sql in sql_list:
                cursor.execute(sql)
