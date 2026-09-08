"""Vincula cadastros compartilhados as filiais da mesma empresa."""
from __future__ import annotations

from django.core.exceptions import ObjectDoesNotExist

from apps.core.tenant_context import tenant_atomic
from apps.cadastros.models import (
    Representante, RepresentanteFilial, Transportadora, TransportadoraFilial,
)
from apps.core.models import Filial


def _politica(filial):
    if not filial or not filial.empresa_id:
        return None
    try:
        politica = filial.politica_replicacao
    except ObjectDoesNotExist:
        try:
            politica = filial.empresa.politica_replicacao
        except ObjectDoesNotExist:
            return None
    if not getattr(politica, 'ativo', True):
        return None
    return politica


def _filiais_destino(filial, campo_politica=None):
    filiais = Filial.objects.filter(
        empresa_id=filial.empresa_id,
        ativo=True,
        participa_replicacao=True,
    )
    if not campo_politica:
        return filiais
    filiais_permitidas = [
        destino.pk
        for destino in filiais
        if getattr(_politica(destino), campo_politica, False)
    ]
    return filiais.filter(pk__in=filiais_permitidas)


def _vincular(vinculo_modelo, campo_objeto, objeto, filial):
    vinculo_modelo.objects.update_or_create(
        **{campo_objeto: objeto, 'filial': filial},
        defaults={'ativo': True},
    )


class CompartilhamentoCadastrosService:
    @staticmethod
    @tenant_atomic
    def sincronizar_transportadora(transportadora):
        politica = _politica(transportadora.filial)
        filiais = _filiais_destino(transportadora.filial, 'replicar_transportadoras') if (
            politica
            and politica.replicar_transportadoras
            and getattr(transportadora.filial, 'participa_replicacao', True)
        ) else [transportadora.filial]
        for filial in filiais:
            _vincular(TransportadoraFilial, 'transportadora', transportadora, filial)

    @staticmethod
    @tenant_atomic
    def sincronizar_transportadoras_da_filial(filial):
        politica = _politica(filial)
        if (
            not politica
            or not politica.replicar_transportadoras
            or not getattr(filial, 'participa_replicacao', True)
        ):
            return 0
        total = 0
        for transportadora in Transportadora.objects.filter(filial=filial).iterator():
            CompartilhamentoCadastrosService.sincronizar_transportadora(transportadora)
            total += 1
        return total

    @staticmethod
    @tenant_atomic
    def sincronizar_representante(representante):
        politica = _politica(representante.filial)
        filiais = _filiais_destino(representante.filial, 'replicar_representantes') if (
            politica
            and politica.replicar_representantes
            and getattr(representante.filial, 'participa_replicacao', True)
        ) else [representante.filial]
        for filial in filiais:
            _vincular(RepresentanteFilial, 'representante', representante, filial)

    @staticmethod
    @tenant_atomic
    def sincronizar_representantes_da_filial(filial):
        politica = _politica(filial)
        if (
            not politica
            or not politica.replicar_representantes
            or not getattr(filial, 'participa_replicacao', True)
        ):
            return 0
        total = 0
        for representante in Representante.objects.filter(filial=filial).iterator():
            CompartilhamentoCadastrosService.sincronizar_representante(representante)
            total += 1
        return total
