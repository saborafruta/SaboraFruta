"""Resolve links públicos para o banco correto sem expor seus tokens."""
import hashlib
import re

from django.apps import apps

from apps.core.models import EmpresaBanco, TenantPublicLink
from apps.core.tenant_registry import register_tenant_database


class TenantPublicLinkService:
    ROUTES = (
        (re.compile(r'^/comprovante/([^/]+)(?:/pdf/)?$'), 'pdv', 'pdv.VendaPDV', 'comprovante_token'),
        (re.compile(r'^/cardapio/([^/]+)(?:/.*)?$'), 'cardapio', 'food_service.Mesa', 'qr_token'),
        (re.compile(r'^/pedido/entrega/([^/]+)/?$'), 'entrega', 'moda.Expedicao', 'codigo'),
        (re.compile(r'^/pedido/([^/]+)(?:/.*)?$'), 'pedido', 'moda.PedidoProducao', 'token_publico'),
    )

    @staticmethod
    def token_hash(token):
        return hashlib.sha256(token.encode('utf-8')).hexdigest()

    @classmethod
    def route_for_path(cls, path):
        for pattern, kind, model_label, field in cls.ROUTES:
            match = pattern.match(path)
            if match:
                return kind, model_label, field, match.group(1)
        return None

    @classmethod
    def resolve_path(cls, path):
        route = cls.route_for_path(path)
        if not route:
            return None
        kind, model_label, field, token = route
        token_hash = cls.token_hash(token)
        indexed = TenantPublicLink.objects.using('default').filter(
            tipo=kind, token_hash=token_hash,
        ).first()
        if indexed:
            banco = EmpresaBanco.objects.using('default').filter(
                db_alias=indexed.db_alias,
                ativo=True,
                status=EmpresaBanco.Status.ATIVO,
            ).first()
            if banco and register_tenant_database(banco):
                return banco.db_alias

        model = apps.get_model(model_label)
        for banco in EmpresaBanco.objects.using('default').filter(
            ativo=True, status=EmpresaBanco.Status.ATIVO,
        ).order_by('pk'):
            if not register_tenant_database(banco):
                continue
            if model._base_manager.using(banco.db_alias).filter(**{field: token}).exists():
                TenantPublicLink.objects.using('default').update_or_create(
                    tipo=kind,
                    token_hash=token_hash,
                    defaults={'db_alias': banco.db_alias},
                )
                return banco.db_alias
        return None

    @classmethod
    def rebuild_for_banco(cls, banco):
        if not register_tenant_database(banco):
            raise RuntimeError(f'Conexão indisponível para {banco.db_alias}.')
        created = 0
        for _pattern, kind, model_label, field in cls.ROUTES:
            model = apps.get_model(model_label)
            tokens = (
                model._base_manager.using(banco.db_alias)
                .exclude(**{f'{field}__isnull': True})
                .exclude(**{field: ''})
                .values_list(field, flat=True)
                .iterator(chunk_size=1000)
            )
            for token in tokens:
                TenantPublicLink.objects.using('default').update_or_create(
                    tipo=kind,
                    token_hash=cls.token_hash(token),
                    defaults={'db_alias': banco.db_alias},
                )
                created += 1
        return created
