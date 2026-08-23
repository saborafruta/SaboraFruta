from datetime import date

from django.db import migrations
from django.db.models import Max


CNPJ_EUREKA = "50649395000126"


def completar_receitas(apps, schema_editor):
    Empresa = apps.get_model("core", "Empresa")
    PlanoContabil = apps.get_model("financeiro", "PlanoContabil")
    PlanoContas = apps.get_model("financeiro", "PlanoContas")
    empresa = Empresa.objects.filter(cnpj=CNPJ_EUREKA).first()
    if not empresa:
        return

    pai_contabil = PlanoContabil.objects.filter(
        empresa=empresa, classificacao="31101",
    ).first()

    def conta_contabil(classificacao, descricao):
        conta = PlanoContabil.objects.filter(
            empresa=empresa, classificacao=classificacao,
        ).first()
        if conta:
            return conta
        maximos = PlanoContabil.objects.filter(empresa=empresa).aggregate(
            codigo=Max("codigo_referencia"), ordem=Max("ordem"),
        )
        return PlanoContabil.objects.create(
            empresa=empresa,
            conta_pai=pai_contabil,
            codigo_referencia=(maximos["codigo"] or 0) + 1,
            classificacao=classificacao,
            tipo_conta="A",
            descricao=descricao,
            codigo_dre=getattr(pai_contabil, "codigo_dre", "") or "",
            data_inicio=date(2026, 8, 23),
            nivel=(pai_contabil.nivel + 1) if pai_contabil else 1,
            ordem=(maximos["ordem"] or 0) + 1,
            origem="Complemento operacional - iTed",
            ativo=True,
        )

    def categoria(codigo, descricao, nivel, pai=None, conta=None):
        item, _ = PlanoContas.objects.update_or_create(
            empresa=empresa,
            codigo=codigo,
            defaults={
                "descricao": descricao,
                "tipo": "R",
                "nivel": nivel,
                "conta_pai": pai,
                "conta_contabil": conta if nivel == 3 else None,
                "aceita_lancamento": nivel == 3,
                "despesa_pessoal": False,
                "ativo": True,
            },
        )
        return item

    operacionais = categoria("310", "Receitas operacionais", 1)
    financeiras = categoria("311", "Receitas financeiras", 1)
    outras = categoria("312", "Outras receitas e ajustes", 1)

    vendas = categoria("31001", "Vendas", 2, operacionais)
    recebimentos = categoria("31002", "Recebimentos a prazo", 2, operacionais)
    capital = categoria("31101", "Capital e emprestimos", 2, financeiras)
    rendimentos = categoria("31102", "Rendimentos financeiros", 2, financeiras)
    ajustes = categoria("31201", "Estornos e reembolsos", 2, outras)
    eventuais = categoria("31202", "Receitas eventuais", 2, outras)

    contas = {
        "vendas": conta_contabil("3110100001", "VENDAS DE PRODUTOS"),
        "receber": conta_contabil("3110100002", "CONTAS A RECEBER"),
        "emprestimos": conta_contabil("3110100003", "EMPRESTIMOS RECEBIDOS"),
        "outras": conta_contabil("3110100004", "OUTRAS ENTRADAS"),
        "servicos": conta_contabil("3110100005", "VENDAS DE SERVICOS"),
        "antecipacoes": conta_contabil("3110100006", "ANTECIPACOES DE CLIENTES"),
        "aportes": conta_contabil("3110100007", "APORTES DE SOCIOS"),
        "rendimentos": conta_contabil("3110100008", "RENDIMENTOS FINANCEIROS"),
        "juros": conta_contabil("3110100009", "JUROS RECEBIDOS"),
        "indenizacoes": conta_contabil("3110100010", "INDENIZACOES RECEBIDAS"),
        "ativos": conta_contabil("3110100011", "VENDA DE ATIVOS"),
    }

    folhas = [
        ("3100100001", "Vendas de produtos", vendas, contas["vendas"]),
        ("3100100005", "Vendas de servicos", vendas, contas["servicos"]),
        ("3100100002", "Contas a receber", recebimentos, contas["receber"]),
        ("3100200002", "Antecipacoes de clientes", recebimentos, contas["antecipacoes"]),
        ("3100100003", "Emprestimos recebidos", capital, contas["emprestimos"]),
        ("3110100002", "Aportes de socios", capital, contas["aportes"]),
        ("3110200001", "Rendimentos de aplicacoes", rendimentos, contas["rendimentos"]),
        ("3110200002", "Juros recebidos", rendimentos, contas["juros"]),
        ("3100100004", "Estornos e reembolsos", ajustes, contas["outras"]),
        ("3120100002", "Indenizacoes recebidas", ajustes, contas["indenizacoes"]),
        ("3120200001", "Venda de ativos", eventuais, contas["ativos"]),
        ("3120200002", "Outras receitas", eventuais, contas["outras"]),
    ]
    for codigo, descricao, pai, conta in folhas:
        categoria(codigo, descricao, 3, pai, conta)


class Migration(migrations.Migration):
    dependencies = [("financeiro", "0037_organiza_receitas_em_grupos")]

    operations = [
        migrations.RunPython(completar_receitas, migrations.RunPython.noop),
    ]
