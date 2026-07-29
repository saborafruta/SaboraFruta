from django.db import migrations


def reabrir_notificacoes(apps, schema_editor):
    ConferenciaTransferencia = apps.get_model(
        'estoque',
        'ConferenciaTransferencia',
    )
    Notificacao = apps.get_model('core', 'Notificacao')
    NotificacaoLeitura = apps.get_model('core', 'NotificacaoLeitura')

    for conferencia in ConferenciaTransferencia.objects.filter(
        status='aguardando',
    ).select_related('filial_origem'):
        origem = (
            conferencia.filial_origem.nome_fantasia
            or conferencia.filial_origem.razao_social
        )
        notificacao, _ = Notificacao.objects.update_or_create(
            filial_id=conferencia.filial_destino_id,
            tipo='transferencia_recebida',
            referencia_tipo='conferencia_transferencia',
            referencia_id=str(conferencia.pk),
            defaults={
                'titulo': 'Transferencia reativada para conferencia',
                'mensagem': (
                    f'{origem} reativou a transferencia '
                    f'{conferencia.documento_numero}.'
                ),
                'url': (
                    '/estoque/outras-movimentacoes/transferencia-lojas/'
                    f'recebimentos/{conferencia.pk}/'
                ),
                'ativa': True,
            },
        )
        NotificacaoLeitura.objects.filter(
            notificacao_id=notificacao.pk,
        ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0036_notificacao'),
        ('estoque', '0008_conferencia_transferencia'),
    ]

    operations = [
        migrations.RunPython(
            reabrir_notificacoes,
            migrations.RunPython.noop,
        ),
    ]
