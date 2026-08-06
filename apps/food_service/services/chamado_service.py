from __future__ import annotations

from django.utils import timezone

from apps.core.services.exceptions import DadosInvalidosError
from apps.food_service.models import ChamadoMesa, Mesa


class ChamadoService:
    """Chamados do cliente pelo Cardápio Digital: chamar garçom / pedir a conta."""

    @classmethod
    def criar_chamado(cls, *, mesa: Mesa, tipo: str) -> ChamadoMesa:
        if tipo not in ChamadoMesa.Tipo.values:
            raise DadosInvalidosError('Tipo de chamado inválido.')
        return ChamadoMesa.objects.create(mesa=mesa, tipo=tipo)

    @classmethod
    def atender_chamado(cls, *, chamado: ChamadoMesa, usuario) -> None:
        if chamado.status != ChamadoMesa.Status.PENDENTE:
            raise DadosInvalidosError('Chamado já foi atendido.')
        chamado.status = ChamadoMesa.Status.ATENDIDO
        chamado.atendido_em = timezone.now()
        chamado.atendido_por = usuario
        chamado.save(update_fields=['status', 'atendido_em', 'atendido_por'])
