"""Entrega opcional por filial, independente da situação financeira."""
from apps.core.models.parametros import ParametrosSistema
from apps.core.services.exceptions import DomainError


def entrega_receber_habilitada(filial):
    return bool(filial and ParametrosSistema.objects.filter(
        filial=filial, controlar_entrega_contas_receber=True,
    ).exists())


def validar_entrega_receber(status, data, complemento):
    if status not in ('sem_previsao', 'prevista', 'entregue'):
        raise DomainError('Situação de entrega inválida.')
    if status == 'prevista' and not (data or complemento):
        raise DomainError('Informe a data prevista ou uma previsão aproximada, como Outubro/2026.')
    if status == 'sem_previsao' and (data or complemento):
        raise DomainError('Para informar data ou complemento, selecione entrega Prevista ou Entregue.')
