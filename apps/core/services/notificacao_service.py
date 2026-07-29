from django.urls import reverse

from apps.core.models import Notificacao


def notificar_transferencia_recebida(conferencia):
    return Notificacao.objects.update_or_create(
        filial=conferencia.filial_destino,
        tipo=Notificacao.Tipo.TRANSFERENCIA_RECEBIDA,
        referencia_tipo='conferencia_transferencia',
        referencia_id=str(conferencia.pk),
        defaults={
            'titulo': 'Nova transferencia para conferencia',
            'mensagem': (
                f'{conferencia.filial_origem.nome_fantasia or conferencia.filial_origem.razao_social} '
                f'enviou a transferencia {conferencia.documento_numero}.'
            ),
            'url': reverse(
                'estoque:transferencia-conferencia-detail',
                kwargs={'pk': conferencia.pk},
            ),
            'ativa': True,
        },
    )[0]


def notificar_transferencia_conferida(conferencia):
    titulos = {
        conferencia.Status.CONFERIDA: 'Transferencia conferida no destino',
        conferencia.Status.COM_DIVERGENCIA: 'Divergencia na transferencia',
        conferencia.Status.CANCELADA: 'Transferencia cancelada no destino',
    }
    return Notificacao.objects.update_or_create(
        filial=conferencia.filial_origem,
        tipo=Notificacao.Tipo.TRANSFERENCIA_CONFERIDA,
        referencia_tipo='conferencia_transferencia',
        referencia_id=str(conferencia.pk),
        defaults={
            'titulo': titulos.get(
                conferencia.status,
                'Atualizacao da transferencia',
            ),
            'mensagem': (
                f'{conferencia.documento_numero}: '
                f'{conferencia.get_status_display()}.'
            ),
            'url': reverse('estoque:transferencia-lojas'),
            'ativa': True,
        },
    )[0]
