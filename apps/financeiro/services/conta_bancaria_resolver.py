import re
import unicodedata

from apps.financeiro.models import ContaBancaria


PALAVRAS_GENERICAS = {
    "ag", "banco", "boleto", "cartao", "credito", "conta", "debito",
    "fisico", "link", "maquininha", "pagamento", "pix", "vencimento",
}


def normalizar_nome(valor):
    texto = "".join(
        caractere
        for caractere in unicodedata.normalize("NFD", valor or "")
        if unicodedata.category(caractere) != "Mn"
    ).casefold()
    return " ".join(re.findall(r"[a-z0-9]+", texto))


def _tokens_relevantes(valor):
    return {
        token for token in normalizar_nome(valor).split()
        if len(token) >= 3 and token not in PALAVRAS_GENERICAS
    }


def resolver_conta_bancaria(forma_pagamento):
    """Resolve a conta pelo cadastro e, na falta dele, por nome sem ambiguidades."""
    if not forma_pagamento:
        return None
    if forma_pagamento.conta_bancaria_padrao_id:
        return forma_pagamento.conta_bancaria_padrao

    contas = ContaBancaria.objects.filter(ativo=True)
    if forma_pagamento.filial_id:
        contas = contas.filter(filial_id=forma_pagamento.filial_id)
    else:
        contas = contas.filter(filial__empresa_id=forma_pagamento.empresa_id)

    tokens_forma = _tokens_relevantes(forma_pagamento.descricao)
    nome_forma = normalizar_nome(forma_pagamento.descricao)
    if "maquininha" in nome_forma:
        contas_orenda = [
            conta for conta in contas
            if "orenda" in _tokens_relevantes(
                " ".join(filter(None, [conta.descricao, conta.banco_nome]))
            )
        ]
        if len(contas_orenda) == 1:
            return contas_orenda[0]

    candidatos = []
    for conta in contas:
        tokens_conta = _tokens_relevantes(
            " ".join(filter(None, [conta.descricao, conta.banco_nome]))
        )
        comuns = tokens_forma & tokens_conta
        if comuns:
            candidatos.append((sum(map(len, comuns)), conta))
    if not candidatos:
        return None

    maior_peso = max(peso for peso, _ in candidatos)
    melhores = [conta for peso, conta in candidatos if peso == maior_peso]
    return melhores[0] if len(melhores) == 1 else None


def vincular_conta_bancaria(forma_pagamento, *, salvar=True):
    conta = resolver_conta_bancaria(forma_pagamento)
    if conta and not forma_pagamento.conta_bancaria_padrao_id:
        forma_pagamento.conta_bancaria_padrao = conta
        if salvar and forma_pagamento.pk:
            type(forma_pagamento).objects.filter(pk=forma_pagamento.pk).update(
                conta_bancaria_padrao=conta,
            )
    return conta
