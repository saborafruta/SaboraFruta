import re

from django.db import migrations, models
from django.db.models import Q


def _telefone(valor):
    digitos = re.sub(r'\D', '', str(valor or '').split('@', 1)[0])
    if len(digitos) in (10, 11):
        digitos = f'55{digitos}'
    return digitos if 12 <= len(digitos) <= 15 else ''


def unificar_conversas(apps, schema_editor):
    Conversa = apps.get_model('whatsapp_agent', 'ConversaWhatsApp')
    Mensagem = apps.get_model('whatsapp_agent', 'MensagemWhatsApp')
    Orcamento = apps.get_model('whatsapp_agent', 'OrcamentoWhatsApp')
    alias = schema_editor.connection.alias
    grupos = {}
    conversas = Conversa.objects.using(alias).filter(
        ativa=True, conexao_vendedor__isnull=False,
    ).order_by('created_at', 'pk')
    for conversa in conversas.iterator():
        telefone = _telefone(conversa.telefone or conversa.remote_jid)
        if not telefone:
            continue
        grupos.setdefault((conversa.conexao_vendedor_id, telefone), []).append(conversa)

    for (_conexao_id, telefone), itens in grupos.items():
        itens.sort(key=lambda item: (
            item.fora_carteira,
            item.cliente_operacional_id is None,
            item.created_at,
            item.pk,
        ))
        principal = itens[0]
        principal.telefone = telefone
        for duplicada in itens[1:]:
            mensagens = Mensagem.objects.using(alias).filter(conversa_id=duplicada.pk)
            for mensagem in mensagens.iterator():
                conflito = bool(
                    mensagem.identificador_externo
                    and Mensagem.objects.using(alias).filter(
                        conversa_id=principal.pk,
                        identificador_externo=mensagem.identificador_externo,
                    ).exists()
                )
                if not conflito:
                    mensagem.conversa_id = principal.pk
                    mensagem.save(using=alias, update_fields=['conversa'])
            Orcamento.objects.using(alias).filter(
                conversa_id=duplicada.pk,
            ).update(conversa_id=principal.pk)
            for campo in ('ultima_mensagem_em', 'ultima_mensagem_cliente_em'):
                valor = getattr(duplicada, campo)
                if valor and (not getattr(principal, campo) or valor > getattr(principal, campo)):
                    setattr(principal, campo, valor)
            if not principal.nome_contato and duplicada.nome_contato:
                principal.nome_contato = duplicada.nome_contato
            duplicada.ativa = False
            duplicada.save(using=alias, update_fields=['ativa', 'updated_at'])
        principal.save(using=alias, update_fields=[
            'telefone', 'nome_contato', 'ultima_mensagem_em',
            'ultima_mensagem_cliente_em', 'updated_at',
        ])


class Migration(migrations.Migration):
    dependencies = [('whatsapp_agent', '0027_orcamento_multibanco')]

    operations = [
        migrations.RunPython(unificar_conversas, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='conversawhatsapp',
            constraint=models.UniqueConstraint(
                fields=('conexao_vendedor', 'telefone'),
                condition=Q(
                    ativa=True, conexao_vendedor__isnull=False,
                ) & ~Q(telefone=''),
                name='whatsapp_conversa_vendedor_telefone_ativo_unico',
            ),
        ),
    ]
