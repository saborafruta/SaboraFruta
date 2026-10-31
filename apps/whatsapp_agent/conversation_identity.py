import re

from django.db.models import Q


def normalizar_telefone_whatsapp(valor):
    """Retorna apenas o telefone real; identificadores LID não são telefones."""
    valor = str(valor or '').strip()
    if '@lid' in valor.lower():
        return ''
    digitos = re.sub(r'\D', '', valor.split('@', 1)[0])
    if len(digitos) in (10, 11):
        digitos = f'55{digitos}'
    return digitos if 12 <= len(digitos) <= 15 else ''


def identidade_mensagem_evolution(chave, dados=None):
    """Resolve JID e telefone estáveis mesmo quando o WhatsApp envia um LID."""
    chave = chave if isinstance(chave, dict) else {}
    dados = dados if isinstance(dados, dict) else {}
    remoto = str(chave.get('remoteJid') or dados.get('remoteJid') or '')
    candidatos = [
        chave.get('remoteJidAlt'), chave.get('senderPn'),
        dados.get('remoteJidAlt'), dados.get('senderPn'), remoto,
    ]
    jid_telefone = next(
        (str(item) for item in candidatos if normalizar_telefone_whatsapp(item)),
        '',
    )
    telefone = normalizar_telefone_whatsapp(jid_telefone)
    return {
        'remote_jid': remoto or jid_telefone,
        'remote_jid_telefone': jid_telefone,
        'telefone': telefone,
    }


def conversa_ativa_por_telefone(queryset, *, telefone='', remote_jid=''):
    """Telefone é a identidade; nome e variações de JID são apenas metadados."""
    telefone = normalizar_telefone_whatsapp(telefone)
    filtro = Q()
    if telefone:
        filtro |= Q(telefone=telefone)
    if remote_jid:
        filtro |= Q(remote_jid=remote_jid)
    if not filtro:
        return None
    return (
        queryset.filter(filtro, ativa=True)
        .order_by('fora_carteira', 'created_at')
        .first()
    )
