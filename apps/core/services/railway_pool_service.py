"""Seleção e monitoramento dos projetos que hospedam bancos dedicados."""
import os

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.models import EmpresaBanco, RailwayProjectPool


class RailwayPoolUnavailableError(RuntimeError):
    pass


class RailwayPoolService:
    @classmethod
    def token_for(cls, pool):
        token = os.environ.get(pool.token_env_var, '').strip()
        if not token:
            raise RailwayPoolUnavailableError(
                f'Variável protegida {pool.token_env_var} não configurada.'
            )
        return token

    @classmethod
    def select_for_banco(cls, banco):
        """Reserva logicamente uma vaga antes de criar recursos externos.

        A associação da empresa ao pool é a reserva. Se o Railway falhar, ela
        permanece para que uma repetição seja idempotente e não consuma uma
        segunda vaga em outro projeto.
        """
        if not settings.RAILWAY_MULTI_PROJECT_ENABLED:
            return None
        if getattr(banco, 'railway_project_pool_id', None):
            return banco.railway_project_pool

        with transaction.atomic(using='default'):
            locked_banco = (
                EmpresaBanco.objects.using('default')
                .select_for_update()
                .select_related('railway_project_pool')
                .get(pk=banco.pk)
            )
            if locked_banco.railway_project_pool_id:
                banco.railway_project_pool = locked_banco.railway_project_pool
                return locked_banco.railway_project_pool

            pools = (
                RailwayProjectPool.objects.using('default')
                .select_for_update()
                .filter(ativo=True, status=RailwayProjectPool.Status.ATIVO)
                .order_by('prioridade', 'pk')
            )
            for pool in pools:
                assigned = pool.bancos.using('default').filter(
                    Q(ativo=True) & ~Q(status=EmpresaBanco.Status.INATIVO)
                ).count()
                observed = pool.ultimo_total_volumes or 0
                if max(assigned, observed) >= pool.capacidade_operacional:
                    continue
                locked_banco.railway_project_pool = pool
                locked_banco.save(
                    using='default', update_fields=['railway_project_pool', 'updated_at'],
                )
                banco.railway_project_pool = pool
                return pool

        raise RailwayPoolUnavailableError(
            'Nenhum projeto Railway ativo possui vaga operacional disponível.'
        )

    @classmethod
    def validate_and_activate(cls, pool):
        """Confere credencial/ambiente e só então libera o pool para uso."""
        from apps.core.services.railway_provisioner import RailwayApiClient

        try:
            # Reaplica as invariantes do model também para chamadas que não
            # passaram por ModelForm/Admin (scripts, shell ou integrações).
            pool.full_clean()
            client = RailwayApiClient(
                pool.railway_project_id,
                pool.railway_environment_id,
                project_token=cls.token_for(pool),
            )
            volume_count = client.volume_count()
        except Exception as exc:
            pool.ativo = False
            pool.status = RailwayProjectPool.Status.ERRO
            pool.ultimo_erro = str(exc)
            pool.ultima_verificacao_em = timezone.now()
            pool.save(using='default', update_fields=[
                'ativo', 'status', 'ultimo_erro', 'ultima_verificacao_em', 'updated_at',
            ])
            return False, str(exc)

        pool.ativo = volume_count < pool.capacidade_operacional
        pool.status = (
            RailwayProjectPool.Status.ATIVO
            if pool.ativo else RailwayProjectPool.Status.LOTADO
        )
        pool.ultimo_total_volumes = volume_count
        pool.ultimo_erro = ''
        pool.ultima_verificacao_em = timezone.now()
        pool.save(using='default', update_fields=[
            'ativo', 'status', 'ultimo_total_volumes', 'ultimo_erro',
            'ultima_verificacao_em', 'updated_at',
        ])
        if pool.ativo:
            return True, f'Projeto validado e ativado com {volume_count} volumes.'
        return False, (
            f'Projeto validado, mas sem vagas operacionais ({volume_count}/'
            f'{pool.capacidade_operacional}).'
        )

    @classmethod
    def update_observation(cls, pool, volume_count, error=''):
        pool.ultimo_total_volumes = volume_count
        pool.ultima_verificacao_em = timezone.now()
        pool.ultimo_erro = error
        if error:
            pool.status = RailwayProjectPool.Status.ERRO
        elif volume_count >= pool.capacidade_operacional:
            pool.status = RailwayProjectPool.Status.LOTADO
        elif pool.status in {
            RailwayProjectPool.Status.LOTADO,
            RailwayProjectPool.Status.ERRO,
        }:
            pool.status = RailwayProjectPool.Status.ATIVO
        pool.save(using='default', update_fields=[
            'ultimo_total_volumes', 'ultima_verificacao_em', 'ultimo_erro',
            'status', 'updated_at',
        ])
        return pool
