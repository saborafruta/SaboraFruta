import re

from django.db import migrations


def _telefone(valor):
    digitos = re.sub(r'\D', '', str(valor or '').split('@', 1)[0])
    if len(digitos) in (10, 11):
        digitos = f'55{digitos}'
    if (
        len(digitos) == 12
        and digitos.startswith('55')
        and digitos[4:5] in {'6', '7', '8', '9'}
    ):
        digitos = f'{digitos[:4]}9{digitos[4:]}'
    return digitos if 12 <= len(digitos) <= 15 else ''


def canonizar_e_unificar(apps, schema_editor):
    Conversa = apps.get_model('whatsapp_agent', 'ConversaWhatsApp')
    Mensagem = apps.get_model('whatsapp_agent', 'MensagemWhatsApp')
    Orcamento = apps.get_model('whatsapp_agent', 'OrcamentoWhatsApp')
    Leitura = apps.get_model('whatsapp_agent', 'LeituraConversaWhatsApp')
    alias = schema_editor.connection.alias
    grupos = {}
    conversas = Conversa.objects.using(alias).filter(
        ativa=True, conexao_vendedor__isnull=False,
    ).order_by('created_at', 'pk')
    for conversa in conversas.iterator():
        telefone = _telefone(conversa.telefone or conversa.remote_jid)
        if telefone:
            grupos.setdefault((conversa.conexao_vendedor_id, telefone), []).append(conversa)

    for (_conexao_id, telefone), itens in grupos.items():
        itens.sort(key=lambda item: (
            item.fora_carteira,
            item.cliente_operacional_id is None,
            item.created_at,
            item.pk,
        ))
        principal = itens[0]
        for duplicada in itens[1:]:
            for mensagem in Mensagem.objects.using(alias).filter(
                conversa_id=duplicada.pk,
            ).iterator():
                conflito = bool(
                    mensagem.identificador_externo
                    and Mensagem.objects.using(alias).filter(
                        conversa_id=principal.pk,
                        identificador_externo=mensagem.identificador_externo,
                    ).exists()
                )
                if conflito:
                    mensagem.delete(using=alias)
                else:
                    mensagem.conversa_id = principal.pk
                    mensagem.save(using=alias, update_fields=['conversa'])
            Orcamento.objects.using(alias).filter(
                conversa_id=duplicada.pk,
            ).update(conversa_id=principal.pk)
            for leitura in Leitura.objects.using(alias).filter(
                conversa_id=duplicada.pk,
            ).iterator():
                existente = Leitura.objects.using(alias).filter(
                    conversa_id=principal.pk,
                    usuario_email=leitura.usuario_email,
                ).first()
                if existente:
                    if leitura.ultima_mensagem_id > existente.ultima_mensagem_id:
                        existente.ultima_mensagem_id = leitura.ultima_mensagem_id
                        existente.save(using=alias, update_fields=[
                            'ultima_mensagem_id', 'updated_at',
                        ])
                    leitura.delete(using=alias)
                else:
                    leitura.conversa_id = principal.pk
                    leitura.save(using=alias, update_fields=['conversa'])
            for campo in ('ultima_mensagem_em', 'ultima_mensagem_cliente_em'):
                valor = getattr(duplicada, campo)
                if valor and (not getattr(principal, campo) or valor > getattr(principal, campo)):
                    setattr(principal, campo, valor)
            duplicada.ativa = False
            duplicada.save(using=alias, update_fields=['ativa', 'updated_at'])

        principal.telefone = telefone
        principal.save(using=alias, update_fields=[
            'telefone', 'ultima_mensagem_em',
            'ultima_mensagem_cliente_em', 'updated_at',
        ])


class Migration(migrations.Migration):
    dependencies = [('whatsapp_agent', '0030_leituraconversawhatsapp')]

    operations = [
        migrations.RunPython(canonizar_e_unificar, migrations.RunPython.noop),
    ]
