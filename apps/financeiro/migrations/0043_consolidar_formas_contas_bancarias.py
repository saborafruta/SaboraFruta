import re
import unicodedata

from django.db import migrations


GENERICAS = {
    "ag", "banco", "boleto", "cartao", "credito", "conta", "debito",
    "fisico", "link", "maquininha", "pagamento", "pix", "vencimento",
}


def _tokens(valor):
    texto = "".join(
        caractere
        for caractere in unicodedata.normalize("NFD", valor or "")
        if unicodedata.category(caractere) != "Mn"
    ).casefold()
    return {
        token for token in re.findall(r"[a-z0-9]+", texto)
        if len(token) >= 3 and token not in GENERICAS
    }


def _conta_por_nome(forma, contas):
    if "maquininha" in _tokens_todos(forma.descricao):
        contas_orenda = [
            conta for conta in contas
            if "orenda" in _tokens_todos(f"{conta.descricao} {conta.banco_nome}")
        ]
        if len(contas_orenda) == 1:
            return contas_orenda[0].pk
    tokens_forma = _tokens(forma.descricao)
    candidatos = []
    for conta in contas:
        comuns = tokens_forma & _tokens(f"{conta.descricao} {conta.banco_nome}")
        if comuns:
            candidatos.append((sum(map(len, comuns)), conta.pk))
    if not candidatos:
        return None
    peso = max(item[0] for item in candidatos)
    melhores = {conta_id for item_peso, conta_id in candidatos if item_peso == peso}
    return melhores.pop() if len(melhores) == 1 else None


def _tokens_todos(valor):
    texto = "".join(
        caractere
        for caractere in unicodedata.normalize("NFD", valor or "")
        if unicodedata.category(caractere) != "Mn"
    ).casefold()
    return set(re.findall(r"[a-z0-9]+", texto))


def consolidar(apps, schema_editor):
    FormaPagamento = apps.get_model("financeiro", "FormaPagamento")
    ContaBancaria = apps.get_model("financeiro", "ContaBancaria")
    ExtratoBancario = apps.get_model("financeiro", "ExtratoBancario")
    ContaReceber = apps.get_model("financeiro", "ContaReceber")
    PagamentoContaPagar = apps.get_model("financeiro", "PagamentoContaPagar")
    ContaPagar = apps.get_model("financeiro", "ContaPagar")
    PagamentoVendaPDV = apps.get_model("pdv", "PagamentoVendaPDV")

    formas = FormaPagamento.objects.filter(ativo=True).select_related("filial")
    for forma in formas.iterator():
        contas = ContaBancaria.objects.filter(ativo=True)
        if forma.filial_id:
            contas = list(contas.filter(filial_id=forma.filial_id))
        else:
            contas = list(contas.filter(filial__empresa_id=forma.empresa_id))

        conta_id = forma.conta_bancaria_padrao_id or _conta_por_nome(forma, contas)
        if not conta_id:
            historico = set()
            historico.update(ExtratoBancario.objects.filter(
                forma_pagamento_id=forma.pk, conta_bancaria__isnull=False,
            ).values_list("conta_bancaria_id", flat=True))
            historico.update(ContaReceber.objects.filter(
                forma_pagamento_id=forma.pk, conta_bancaria__isnull=False,
            ).values_list("conta_bancaria_id", flat=True))
            historico.update(PagamentoVendaPDV.objects.filter(
                forma_pagamento_id=forma.pk, conta_bancaria__isnull=False,
            ).values_list("conta_bancaria_id", flat=True))
            contas_validas = {conta.pk for conta in contas}
            historico &= contas_validas
            if len(historico) == 1:
                conta_id = historico.pop()
        if conta_id and forma.conta_bancaria_padrao_id != conta_id:
            FormaPagamento.objects.filter(pk=forma.pk).update(
                conta_bancaria_padrao_id=conta_id,
            )

    taxas = PagamentoContaPagar.objects.filter(
        conta_bancaria__isnull=True,
        conta_pagar__documento_tipo__startswith="taxa_",
        forma_pagamento__conta_bancaria_padrao__isnull=False,
    ).select_related("forma_pagamento", "conta_pagar")
    for pagamento in taxas.iterator():
        conta_id = pagamento.forma_pagamento.conta_bancaria_padrao_id
        PagamentoContaPagar.objects.filter(pk=pagamento.pk).update(
            conta_bancaria_id=conta_id,
        )
        ContaPagar.objects.filter(pk=pagamento.conta_pagar_id).update(
            conta_bancaria_id=conta_id,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("financeiro", "0042_vincular_formas_as_contas_por_nome"),
        ("pdv", "0014_corrigir_liquidacao_d0_recente"),
    ]
    operations = [migrations.RunPython(consolidar, migrations.RunPython.noop)]
