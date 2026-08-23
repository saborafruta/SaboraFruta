from django.db import migrations


def organizar_receitas(apps, schema_editor):
    Empresa = apps.get_model("core", "Empresa")
    PlanoContas = apps.get_model("financeiro", "PlanoContas")
    empresa = Empresa.objects.filter(cnpj="50649395000126").first()
    if not empresa:
        return

    def salvar(codigo, descricao, nivel, pai=None, conta_contabil=None):
        item, _ = PlanoContas.objects.update_or_create(
            empresa=empresa,
            codigo=codigo,
            defaults={
                "descricao": descricao,
                "tipo": "R",
                "nivel": nivel,
                "conta_pai": pai,
                "conta_contabil": conta_contabil if nivel == 3 else None,
                "aceita_lancamento": nivel == 3,
                "ativo": True,
                "despesa_pessoal": False,
            },
        )
        return item

    operacionais = salvar("310", "Receitas operacionais", 1)
    financeiras = salvar("311", "Receitas financeiras", 1)
    ajustes = salvar("312", "Outras receitas e ajustes", 1)
    vendas = salvar("31001", "Vendas", 2, operacionais)
    receber = salvar("31002", "Recebimentos a prazo", 2, operacionais)
    emprestimos = salvar("31101", "Capital e empréstimos", 2, financeiras)
    estornos = salvar("31201", "Estornos e ajustes", 2, ajustes)

    for codigo, pai in {
        "3100100001": vendas,
        "3100100002": receber,
        "3100100003": emprestimos,
        "3100100004": estornos,
    }.items():
        PlanoContas.objects.filter(empresa=empresa, codigo=codigo).update(conta_pai=pai)


class Migration(migrations.Migration):
    dependencies = [("financeiro", "0036_receitas_e_meta_despesa_pessoal")]

    operations = [migrations.RunPython(organizar_receitas, migrations.RunPython.noop)]
