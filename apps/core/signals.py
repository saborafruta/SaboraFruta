"""Signals de auditoria automatica."""
import json
from decimal import Decimal

from django.core.serializers.json import DjangoJSONEncoder
from django.db.models.signals import post_delete, post_save, pre_save


from apps.core.tenant_context import tenant_atomic
AUDITED_MODELS = set()


def register_for_audit(model_cls, modulo: str):
    AUDITED_MODELS.add((model_cls, modulo))
    pre_save.connect(
        _audit_pre_save,
        sender=model_cls,
        dispatch_uid=f'audit_pre_save_{model_cls._meta.label_lower}',
        weak=False,
    )
    post_save.connect(
        _audit_save,
        sender=model_cls,
        dispatch_uid=f'audit_post_save_{model_cls._meta.label_lower}',
        weak=False,
    )
    post_delete.connect(
        _audit_delete,
        sender=model_cls,
        dispatch_uid=f'audit_post_delete_{model_cls._meta.label_lower}',
        weak=False,
    )


def _serialize_instance(instance):
    """Serializa campos concretos em valores legiveis para o log."""
    fields = {}
    for field in instance._meta.concrete_fields:
        if field.name == 'password':
            continue
        try:
            if field.choices:
                display = getattr(instance, f'get_{field.name}_display', None)
                value = display() if callable(display) else field.value_from_object(instance)
            elif field.is_relation and getattr(field, 'many_to_one', False):
                value = str(getattr(instance, field.name, '') or '')
            else:
                value = field.value_from_object(instance)
            if isinstance(value, Decimal):
                value = str(value)
            fields[field.name] = json.loads(json.dumps(value, cls=DjangoJSONEncoder))
        except Exception:
            fields[field.name] = ''
    return fields


def _find_modulo(sender):
    for model, modulo in AUDITED_MODELS:
        if model is sender:
            return modulo
    return 'desconhecido'


def _audit_pre_save(sender, instance, **kwargs):
    if not getattr(instance, 'pk', None):
        instance._audit_dados_anteriores = None
        return
    try:
        anterior = sender.objects.get(pk=instance.pk)
        instance._audit_dados_anteriores = _serialize_instance(anterior)
    except Exception:
        instance._audit_dados_anteriores = None


def _audit_save(sender, instance, created, **kwargs):
    try:
        from apps.core.middleware.audit import get_client_ip, get_current_request
        from apps.core.models import LogSistema

        request = get_current_request()
        if not request or not getattr(request, 'user', None) or not request.user.is_authenticated:
            return

        with tenant_atomic():
            LogSistema.objects.create(
                filial=getattr(request, 'filial_ativa', None),
                usuario=request.user,
                modulo=_find_modulo(sender),
                acao=LogSistema.Acao.CRIAR if created else LogSistema.Acao.EDITAR,
                tabela_afetada=sender._meta.db_table,
                registro_id=instance.pk,
                dados_anteriores=None if created else getattr(instance, '_audit_dados_anteriores', None),
                dados_novos=_serialize_instance(instance),
                ip_acesso=get_client_ip(request),
                user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
            )
    except Exception:
        pass


def _audit_delete(sender, instance, **kwargs):
    try:
        from apps.core.middleware.audit import get_client_ip, get_current_request
        from apps.core.models import LogSistema

        request = get_current_request()
        if not request or not getattr(request, 'user', None) or not request.user.is_authenticated:
            return

        with tenant_atomic():
            LogSistema.objects.create(
                filial=getattr(request, 'filial_ativa', None),
                usuario=request.user,
                modulo=_find_modulo(sender),
                acao=LogSistema.Acao.EXCLUIR,
                tabela_afetada=sender._meta.db_table,
                registro_id=instance.pk,
                dados_anteriores=_serialize_instance(instance),
                ip_acesso=get_client_ip(request),
                user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
            )
    except Exception:
        pass
