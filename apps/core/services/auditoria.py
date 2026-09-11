"""Servicos para auditoria operacional explicita."""
import json
import logging
from decimal import Decimal

from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.db.utils import Error as DatabaseError

from apps.core.middleware.audit import get_client_ip
from apps.core.models import RegistroAuditoria
from apps.core.tenant_context import tenant_atomic

logger = logging.getLogger(__name__)


def snapshot_modelo(obj, campos=None):
    """Serializa campos concretos simples de um model para antes/depois."""
    if obj is None:
        return None
    dados = {}
    for field in obj._meta.concrete_fields:
        if campos and field.name not in campos:
            continue
        if field.name == 'password':
            continue
        try:
            if isinstance(field, models.ForeignKey):
                valor = getattr(obj, field.attname)
            else:
                valor = field.value_from_object(obj)
            if isinstance(valor, Decimal):
                valor = str(valor)
            dados[field.name] = json.loads(json.dumps(valor, cls=DjangoJSONEncoder))
        except Exception:
            dados[field.name] = ''
    return dados


def registrar_auditoria(
    *,
    request=None,
    usuario=None,
    filial=None,
    modulo,
    acao,
    objeto,
    descricao='',
    justificativa='',
    antes=None,
    depois=None,
    relacionado=None,
    metadados=None,
):
    """Cria um RegistroAuditoria para operacoes sensiveis."""
    usuario = usuario or getattr(request, 'user', None)
    filial = filial or getattr(request, 'filial_ativa', None) or getattr(objeto, 'filial', None)
    objeto_tipo = objeto._meta.label_lower if hasattr(objeto, '_meta') else objeto.__class__.__name__.lower()
    objeto_id = getattr(objeto, 'pk', None)
    relacionado_tipo = ''
    relacionado_id = None
    if relacionado is not None:
        relacionado_tipo = relacionado._meta.label_lower if hasattr(relacionado, '_meta') else relacionado.__class__.__name__.lower()
        relacionado_id = getattr(relacionado, 'pk', None)
    if not objeto_id:
        return None
    # `_id`, não a instância: `usuario` (quase sempre `request.user`, um
    # `SimpleLazyObject`) e `filial` podem resolver num alias de banco
    # diferente do de `RegistroAuditoria` quando o roteamento por tenant
    # está ativo -- atribuir a instância dispara `allow_relation()`
    # (`apps/core/db_router.py`) e estoura "the current database router
    # prevents this relation" se os dois lados não baterem. Atribuir só o
    # id nunca passa por essa checagem.
    usuario_id = usuario.pk if getattr(usuario, 'is_authenticated', False) else None
    filial_id = getattr(filial, 'pk', None)

    # CADA EMPRESA TEM SEU PRÓPRIO BANCO, e um operador que atende várias
    # empresas (ex.: equipe iTed) pode ter um id de usuário que existe no
    # banco de uma empresa mas não no de outra -- o `usuario_id` acima é só
    # um número, não passa pela checagem do Django, e o INSERT quebra a
    # foreign key de verdade no Postgres. Auditoria é bookkeeping: perder o
    # registro é ruim, mas travar a ação de verdade (criar o depósito, dar
    # baixa, excluir o título) por causa disso é pior. `tenant_atomic()`
    # abre um savepoint -- se o INSERT falhar, só ele desfaz; a transação
    # de quem chamou continua de pé.
    try:
        with tenant_atomic():
            return RegistroAuditoria.objects.create(
                filial_id=filial_id,
                usuario_id=usuario_id,
                modulo=modulo,
                acao=acao,
                objeto_tipo=objeto_tipo,
                objeto_id=objeto_id,
                objeto_descricao=(descricao or str(objeto))[:255],
                relacionado_tipo=relacionado_tipo,
                relacionado_id=relacionado_id,
                justificativa=justificativa or '',
                dados_anteriores=antes,
                dados_novos=depois,
                metadados=metadados or {},
                ip_acesso=get_client_ip(request) if request else None,
                user_agent=(request.META.get('HTTP_USER_AGENT', '')[:500] if request else ''),
            )
    except DatabaseError:
        logger.warning(
            'Falha ao gravar auditoria (modulo=%s acao=%s objeto=%s usuario_id=%s '
            'filial_id=%s) -- provavelmente usuario_id/filial_id sem linha '
            'correspondente neste banco de tenant.',
            modulo, acao, objeto_tipo, usuario_id, filial_id,
            exc_info=True,
        )
        return None


def auditoria_para_objeto(obj, limit=20):
    return RegistroAuditoria.objects.filter(
        objeto_tipo=obj._meta.label_lower,
        objeto_id=obj.pk,
    ).select_related('usuario', 'filial')[:limit]


def auditoria_relacionada(obj, limit=20):
    return RegistroAuditoria.objects.filter(
        relacionado_tipo=obj._meta.label_lower,
        relacionado_id=obj.pk,
    ).select_related('usuario', 'filial')[:limit]
