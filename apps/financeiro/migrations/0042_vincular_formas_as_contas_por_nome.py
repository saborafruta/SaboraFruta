import unicodedata

from django.db import migrations


def _normalizar(valor):
    return "".join(
        caractere
        for caractere in unicodedata.normalize("NFD", valor or "")
        if unicodedata.category(caractere) != "Mn"
    ).casefold()


def vincular(apps, schema_editor):
    FormaPagamento = apps.get_model("financeiro", "FormaPagamento")
    ContaBancaria = apps.get_model("financeiro", "ContaBancaria")

    formas = FormaPagamento.objects.filter(
        ativo=True, conta_bancaria_padrao__isnull=True,
    ).select_related("filial")
    for forma in formas.iterator():
        descricao_forma = _normalizar(forma.descricao)
        candidatos = []
        contas = ContaBancaria.objects.filter(ativo=True)
        if forma.filial_id:
            contas = contas.filter(filial_id=forma.filial_id)
        else:
            contas = contas.filter(filial__empresa_id=forma.empresa_id)
        for conta in contas:
            nomes = {
                _normalizar(conta.descricao),
                _normalizar(conta.banco_nome),
            } - {""}
            correspondencias = [nome for nome in nomes if nome in descricao_forma]
            if correspondencias:
                candidatos.append((max(map(len, correspondencias)), conta.pk))
        if candidatos:
            maior_correspondencia = max(peso for peso, _ in candidatos)
            melhores = {conta_id for peso, conta_id in candidatos if peso == maior_correspondencia}
            if len(melhores) != 1:
                continue
            conta_id = melhores.pop()
            FormaPagamento.objects.filter(pk=forma.pk).update(
                conta_bancaria_padrao_id=conta_id,
            )


class Migration(migrations.Migration):
    dependencies = [("financeiro", "0041_vincular_conta_taxas_transacao")]
    operations = [migrations.RunPython(vincular, migrations.RunPython.noop)]
