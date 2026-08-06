from __future__ import annotations

from apps.core.services.exceptions import DadosInvalidosError

from ..models import Comanda, Mesa


class MesaService:
    """Transições manuais de status da mesa que não dependem do ciclo de uma comanda."""

    @classmethod
    def marcar_reservada(cls, mesa: Mesa):
        if mesa.status != Mesa.Status.LIVRE:
            raise DadosInvalidosError('Só é possível reservar uma mesa livre.')
        mesa.status = Mesa.Status.RESERVADA
        mesa.save(update_fields=['status'])

    @classmethod
    def marcar_livre(cls, mesa: Mesa):
        if mesa.comandas.filter(status=Comanda.Status.ABERTA).exists():
            raise DadosInvalidosError('Mesa possui comanda aberta — feche a comanda antes de liberar a mesa.')
        mesa.status = Mesa.Status.LIVRE
        mesa.save(update_fields=['status'])
