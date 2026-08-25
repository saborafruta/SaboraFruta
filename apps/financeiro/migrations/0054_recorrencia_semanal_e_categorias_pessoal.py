from datetime import date

from django.db import migrations, models
from django.db.models import Max


CATEGORIAS_PESSOAL = [
    ("3320100017", "Diária"),
    ("3320100018", "Hora extra"),
]


def _maximos(PlanoContabil, empresa):
    return PlanoContabil.objects.filter(empresa=empresa).aggregate(
        codigo=Max("codigo_referencia"),
        ordem=Max("ordem"),
    )


def _conta_contabil(PlanoContabil, empresa, classificacao, descricao):
    conta = PlanoContabil.objects.filter(
        empresa=empresa,
        classificacao=classificacao,
    ).first()
    if conta:
        conta.descricao = descricao
        conta.tipo_conta = "A"
        conta.ativo = True
        conta.save(update_fields=["descricao", "tipo_conta", "ativo", "updated_at"])
        return conta

    pai = PlanoContabil.objects.filter(empresa=empresa, classificacao="33201").first()
    if not pai:
        pai = PlanoContabil.objects.filter(
            empresa=empresa,
            classificacao__in=["332", "33", "3"],
        ).order_by("-nivel", "ordem").first()
    maximos = _maximos(PlanoContabil, empresa)
    return PlanoContabil.objects.create(
        empresa=empresa,
        conta_pai=pai,
        codigo_referencia=(maximos["codigo"] or 0) + 1,
        classificacao=classificacao,
        tipo_conta="A",
        descricao=descricao,
        codigo_dre=getattr(pai, "codigo_dre", "") or "",
        data_inicio=date(2026, 8, 25),
        nivel=(pai.nivel + 1) if pai else 1,
        ordem=(maximos["ordem"] or 0) + 1,
        origem="Complemento operacional - despesas com pessoal",
        ativo=True,
    )


def criar_categorias_pessoal(apps, schema_editor):
    Empresa = apps.get_model("core", "Empresa")
    PlanoContas = apps.get_model("financeiro", "PlanoContas")
    PlanoContabil = apps.get_model("financeiro", "PlanoContabil")

    for empresa in Empresa.objects.all().iterator():
        grupo, _ = PlanoContas.objects.update_or_create(
            empresa=empresa,
            codigo="332",
            defaults={
                "descricao": "Despesas Administrativas",
                "tipo": "D",
                "nivel": 1,
                "aceita_lancamento": False,
                "despesa_pessoal": False,
                "ativo": True,
            },
        )
        subgrupo, _ = PlanoContas.objects.update_or_create(
            empresa=empresa,
            codigo="33201",
            defaults={
                "descricao": "Despesas com Pessoal",
                "tipo": "D",
                "nivel": 2,
                "conta_pai": grupo,
                "aceita_lancamento": False,
                "despesa_pessoal": True,
                "ativo": True,
            },
        )
        for codigo, descricao in CATEGORIAS_PESSOAL:
            conta = _conta_contabil(PlanoContabil, empresa, codigo, descricao)
            PlanoContas.objects.update_or_create(
                empresa=empresa,
                codigo=codigo,
                defaults={
                    "descricao": descricao,
                    "tipo": "D",
                    "nivel": 3,
                    "conta_pai": subgrupo,
                    "conta_contabil": conta,
                    "aceita_lancamento": True,
                    "despesa_pessoal": True,
                    "ativo": True,
                },
            )


class Migration(migrations.Migration):
    dependencies = [
        ("financeiro", "0053_limpar_configuracoes_eureka_de_outras_empresas"),
    ]

    operations = [
        migrations.AddField(
            model_name="contapagar",
            name="dias_semana_recorrencia",
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.RunPython(criar_categorias_pessoal, migrations.RunPython.noop),
    ]
