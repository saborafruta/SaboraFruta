"""Resolucao da configuracao do checkout entre Central e banco operacional."""

from django.conf import settings
from django.db import DEFAULT_DB_ALIAS


def checkout_venda_ativo(request) -> bool:
    """Retorna a flag salva pela Central para a filial ativa.

    Em producao, rotas ``/gestao/`` usam o Banco Gerencial, enquanto ``/pdv/``
    usa o banco operacional do tenant. Portanto, no PDV a filial precisa ser
    traduzida para sua copia central por empresa e CNPJ antes de ler a flag.
    Instalacoes sem multibanco continuam consultando o banco atual.
    """
    from apps.core.models import EmpresaBanco, Filial, ParametrosSistema

    filial = getattr(request, 'filial_ativa', None)
    if filial is None:
        return False

    tenant_alias = getattr(request, 'tenant_db_alias', None)
    if getattr(settings, 'TENANT_DATABASE_ROUTING_ENABLED', False) and tenant_alias:
        try:
            banco = (
                EmpresaBanco.objects.using(DEFAULT_DB_ALIAS)
                .only('empresa_id')
                .get(
                    db_alias=tenant_alias,
                    ativo=True,
                    status=EmpresaBanco.Status.ATIVO,
                )
            )
            filial_central_id = (
                Filial.objects.using(DEFAULT_DB_ALIAS)
                .filter(
                    empresa_id=banco.empresa_id,
                    cnpj=filial.cnpj,
                    ativo=True,
                )
                .values_list('pk', flat=True)
                .first()
            )
            if not filial_central_id:
                return False
            return bool(
                ParametrosSistema.objects.using(DEFAULT_DB_ALIAS)
                .filter(filial_id=filial_central_id)
                .values_list('checkout_venda_ativo', flat=True)
                .first()
            )
        except (EmpresaBanco.DoesNotExist, Filial.DoesNotExist):
            return False

    database_alias = getattr(getattr(filial, '_state', None), 'db', None) or DEFAULT_DB_ALIAS
    return bool(
        ParametrosSistema.objects.using(database_alias)
        .filter(filial_id=filial.pk)
        .values_list('checkout_venda_ativo', flat=True)
        .first()
    )
