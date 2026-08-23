from datetime import date

import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Max


CNPJ_EUREKA = "50649395000126"


CONTAS_CONTABEIS_RECEITA = [
    ("3110100001", "VENDAS", "31101"),
    ("3110100002", "CONTAS A RECEBER", "31101"),
    ("3110100003", "EMPRESTIMOS RECEBIDOS", "31101"),
    ("3110100004", "OUTRAS ENTRADAS", "31101"),
]


def _maximos(PlanoContabil, empresa):
    return PlanoContabil.objects.filter(empresa=empresa).aggregate(
        codigo=Max("codigo_referencia"),
        ordem=Max("ordem"),
    )


def _conta_contabil(apps, empresa, classificacao, descricao, pai_classificacao=None, tipo="A"):
    PlanoContabil = apps.get_model("financeiro", "PlanoContabil")
    conta = PlanoContabil.objects.filter(empresa=empresa, classificacao=classificacao).first()
    if conta:
        if tipo == "A" and conta.tipo_conta != "A":
            conta.tipo_conta = "A"
            conta.save(update_fields=["tipo_conta", "updated_at"])
        return conta

    pai = None
    if pai_classificacao:
        pai = PlanoContabil.objects.filter(empresa=empresa, classificacao=pai_classificacao).first()
    maximos = _maximos(PlanoContabil, empresa)
    return PlanoContabil.objects.create(
        empresa=empresa,
        conta_pai=pai,
        codigo_referencia=(maximos["codigo"] or 0) + 1,
        classificacao=classificacao,
        tipo_conta=tipo,
        descricao=descricao,
        codigo_dre=getattr(pai, "codigo_dre", "") or "",
        data_inicio=date(2026, 8, 23),
        nivel=(pai.nivel + 1) if pai else 1,
        ordem=(maximos["ordem"] or 0) + 1,
        origem="Complemento operacional - iTed",
        ativo=True,
    )


def _categoria(PlanoContas, empresa, codigo, descricao, nivel, pai=None, conta=None):
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


def configurar_receitas(apps, schema_editor):
    Empresa = apps.get_model("core", "Empresa")
    PlanoContas = apps.get_model("financeiro", "PlanoContas")
    empresa = Empresa.objects.filter(cnpj=CNPJ_EUREKA).first()
    if not empresa:
        return

    _conta_contabil(apps, empresa, "3", "RECEITAS", None, "S")
    _conta_contabil(apps, empresa, "311", "RECEITA BRUTA OPERACIONAL", "3", "S")
    _conta_contabil(apps, empresa, "31101", "RECEITAS OPERACIONAIS", "311", "S")

    contas = {
        classificacao: _conta_contabil(apps, empresa, classificacao, descricao, pai)
        for classificacao, descricao, pai in CONTAS_CONTABEIS_RECEITA
    }

    grupo = _categoria(PlanoContas, empresa, "310", "Receitas Operacionais", 1)
    subgrupo = _categoria(PlanoContas, empresa, "31001", "Entradas do caixa", 2, pai=grupo)
    categorias = [
        ("3100100001", "Vendas", "3110100001"),
        ("3100100002", "Conta a receber", "3110100002"),
        ("3100100003", "Emprestimos recebidos", "3110100003"),
        ("3100100004", "Outras entradas", "3110100004"),
    ]
    for codigo, descricao, classificacao in categorias:
        _categoria(PlanoContas, empresa, codigo, descricao, 3, pai=subgrupo, conta=contas[classificacao])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_initial"),
        ("financeiro", "0035_extratobancario_plano_contas"),
    ]

    operations = [
        migrations.CreateModel(
            name="MetaDespesaPessoal",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("tipo_meta", models.CharField(choices=[
                    ("valor_fixo", "Valor fixo"),
                    ("percentual_mes_anterior", "% do faturamento do mes anterior"),
                    ("percentual_media_meses", "% da media de faturamento"),
                ], default="valor_fixo", max_length=30)),
                ("valor_fixo", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("percentual", models.DecimalField(decimal_places=4, default=0, max_digits=7)),
                ("meses_media", models.PositiveSmallIntegerField(default=3)),
                ("ativo", models.BooleanField(default=True)),
                ("filial", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="meta_despesa_pessoal", to="core.filial")),
            ],
            options={
                "verbose_name": "Meta de despesa pessoal",
                "verbose_name_plural": "Metas de despesas pessoais",
                "db_table": "metas_despesa_pessoal",
            },
        ),
        migrations.RunPython(configurar_receitas, migrations.RunPython.noop),
    ]
