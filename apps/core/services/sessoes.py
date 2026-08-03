"""
Encerramento remoto de sessões.

Serve ao caso de celular perdido, roubado ou trocado: derruba o acesso do
usuário em todos os aparelhos sem mexer na senha dele. Trocar a senha também
derrubaria (o Django rotaciona o hash de sessão), mas obriga a combinar uma
senha nova com quem está na rua — o que é justamente o que não dá para fazer
quando o aparelho sumiu.
"""
from __future__ import annotations

from django.contrib.sessions.models import Session
from django.utils import timezone


def sessoes_do_usuario(usuario):
    """
    Chaves das sessões ativas de um usuário.

    A tabela de sessões guarda os dados serializados, sem coluna de usuário —
    então não há como filtrar por SQL: é preciso decodificar cada sessão
    ativa. O varrimento é sobre as **não expiradas**, que num ERP interno são
    dezenas, não milhões. Se um dia virar gargalo, a saída é um índice
    próprio (tabela usuário→sessão alimentada no login), não otimizar isto.
    """
    alvo = str(usuario.pk)
    chaves = []
    for sessao in Session.objects.filter(expire_date__gte=timezone.now()):
        try:
            dados = sessao.get_decoded()
        except Exception:
            # Sessão corrompida ou assinada com uma SECRET_KEY antiga: não dá
            # para saber de quem é, e estourar aqui impediria de derrubar as
            # outras. Ela expira sozinha.
            continue
        if dados.get('_auth_user_id') == alvo:
            chaves.append(sessao.session_key)
    return chaves


def encerrar_sessoes(usuario, *, preservar=None):
    """
    Apaga as sessões do usuário e devolve quantas foram encerradas.

    `preservar` é a chave da sessão de quem está executando a ação. Sem isso,
    um administrador que encerrasse as próprias sessões se deslogaria no mesmo
    clique — comportamento que ninguém espera de um botão numa lista.
    """
    chaves = [c for c in sessoes_do_usuario(usuario) if c != preservar]
    if not chaves:
        return 0
    apagadas, _ = Session.objects.filter(session_key__in=chaves).delete()
    return apagadas
