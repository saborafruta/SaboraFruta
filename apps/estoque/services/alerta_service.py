"""Servico de geracao de alertas de vencimento."""
from __future__ import annotations

from django.utils import timezone

from apps.core.tenant_context import tenant_atomic
from apps.estoque.models import AlertaVencimento, LoteProduto


class AlertaService:
    """Calcula nivel de risco e gera registros de alerta."""

    # Thresholds operacionais: (dias_limite, nivel)
    LIMITES = [
        (1,   AlertaVencimento.NivelRisco.D1),
        (7,   AlertaVencimento.NivelRisco.D7),
        (30,  AlertaVencimento.NivelRisco.D30),
        (60,  AlertaVencimento.NivelRisco.D60),
        (90,  AlertaVencimento.NivelRisco.D90),
        (180, AlertaVencimento.NivelRisco.D180),
    ]

    # Janela máxima de monitoramento
    JANELA_DIAS = 180

    @classmethod
    def classificar_risco(cls, dias_para_vencer: int) -> str:
        """Retorna nivel_risco baseado em dias para vencimento."""
        for limite, nivel in cls.LIMITES:
            if dias_para_vencer <= limite:
                return nivel
        return ''

    @classmethod
    @tenant_atomic
    def gerar_alertas_lote(cls, lote: LoteProduto) -> AlertaVencimento | None:
        """Gera ou atualiza alerta para um lote especifico."""
        if (
            not lote.data_validade
            or lote.quantidade_atual <= 0
            or lote.status != LoteProduto.Status.ATIVO
        ):
            cls.resolver_alertas_lote(lote)
            return None

        dias = lote.dias_para_vencer
        if dias is None or dias > cls.JANELA_DIAS or dias < 0:
            cls.resolver_alertas_lote(lote)
            return None

        nivel = cls.classificar_risco(dias)
        if not nivel:
            cls.resolver_alertas_lote(lote)
            return None

        AlertaVencimento.objects.filter(
            lote=lote,
            resolvido=False,
        ).exclude(nivel_risco=nivel).update(
            resolvido=True,
            notificado_em=timezone.now(),
        )

        alerta, _ = AlertaVencimento.objects.update_or_create(
            filial=lote.filial,
            produto=lote.produto,
            lote=lote,
            nivel_risco=nivel,
            resolvido=False,
            defaults={
                'data_validade': lote.data_validade,
                'quantidade_em_risco': lote.quantidade_atual,
                'dias_para_vencer': dias,
            },
        )
        return alerta

    @classmethod
    @tenant_atomic
    def bloquear_lote_vencido(cls, lote: LoteProduto) -> bool:
        """Bloqueia lote vencido para impedir vendas."""
        if not lote.esta_vencido:
            return False
        if lote.status == LoteProduto.Status.VENCIDO:
            cls.resolver_alertas_lote(lote)
            return False
        lote.status = LoteProduto.Status.VENCIDO
        lote.motivo_bloqueio = (
            f'Bloqueio automatico por vencimento em {lote.data_validade:%d/%m/%Y}'
        )
        lote.save(update_fields=['status', 'motivo_bloqueio', 'updated_at'])
        cls.resolver_alertas_lote(lote)
        return True

    @staticmethod
    def resolver_alertas_lote(lote: LoteProduto):
        """Marca como resolvidos todos os alertas ativos de um lote."""
        AlertaVencimento.objects.filter(lote=lote, resolvido=False).update(
            resolvido=True,
            notificado_em=timezone.now(),
        )
