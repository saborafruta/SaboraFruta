from datetime import date

from django.db import migrations, models
from django.db.models import Max


CNPJ_EUREKA = "50649395000126"


def _conta_analitica(apps, empresa, classificacao, descricao, pai_classificacao):
    PlanoContabil = apps.get_model("financeiro", "PlanoContabil")
    conta = PlanoContabil.objects.filter(
        empresa=empresa, classificacao=classificacao,
    ).first()
    if conta:
        return conta
    pai = PlanoContabil.objects.filter(
        empresa=empresa, classificacao=pai_classificacao,
    ).first()
    if not pai:
        return None
    maximos = PlanoContabil.objects.filter(empresa=empresa).aggregate(
        codigo=Max("codigo_referencia"), ordem=Max("ordem"),
    )
    return PlanoContabil.objects.create(
        empresa=empresa,
        conta_pai=pai,
        codigo_referencia=(maximos["codigo"] or 0) + 1,
        classificacao=classificacao,
        tipo_conta="A",
        descricao=descricao,
        codigo_dre=pai.codigo_dre,
        data_inicio=date(2026, 8, 22),
        nivel=(pai.nivel or 4) + 1,
        ordem=(maximos["ordem"] or 0) + 1,
        origem="Complemento operacional - iTed",
        ativo=True,
    )


def _categoria(PlanoContas, empresa, codigo, descricao, nivel, pai=None, conta=None, pessoal=False):
    item, _ = PlanoContas.objects.update_or_create(
        empresa=empresa,
        codigo=codigo,
        defaults={
            "descricao": descricao,
            "tipo": "D",
            "nivel": nivel,
            "conta_pai": pai,
            "conta_contabil": conta if nivel == 3 else None,
            "aceita_lancamento": nivel == 3,
            "despesa_pessoal": pessoal,
            "ativo": True,
        },
    )
    return item


def configurar_categorias(apps, schema_editor):
    Empresa = apps.get_model("core", "Empresa")
    PlanoContas = apps.get_model("financeiro", "PlanoContas")
    empresa = Empresa.objects.filter(cnpj=CNPJ_EUREKA).first()
    if not empresa:
        return

    conta_socios = _conta_analitica(
        apps, empresa, "1220400001", "EMPRÉSTIMO SÓCIO A", "12204",
    )
    grupo_pessoal = _categoria(
        PlanoContas, empresa, "390", "Despesas Pessoais e Sócios", 1, pessoal=True,
    )
    subgrupo_pessoal = _categoria(
        PlanoContas, empresa, "39001", "Gastos Pessoais", 2,
        pai=grupo_pessoal, pessoal=True,
    )
    for codigo, descricao in (
        ("3900100001", "Compras Pessoais"),
        ("3900100002", "Alimentação Pessoal"),
        ("3900100003", "Outras Despesas Pessoais"),
    ):
        _categoria(
            PlanoContas, empresa, codigo, descricao, 3,
            pai=subgrupo_pessoal, conta=conta_socios, pessoal=True,
        )

    conta_software = _conta_analitica(
        apps, empresa, "3320400023", "SISTEMAS, SOFTWARES E ASSINATURAS", "33204",
    )
    grupo_admin = PlanoContas.objects.filter(
        empresa=empresa, nivel=1, descricao__iexact="Despesas Administrativas",
    ).first() or _categoria(
        PlanoContas, empresa, "332", "Despesas Administrativas", 1,
    )
    subgrupo_tecnologia = _categoria(
        PlanoContas, empresa, "33206", "Tecnologia e Sistemas", 2, pai=grupo_admin,
    )
    _categoria(
        PlanoContas, empresa, "3320600001", "Sistemas, Softwares e Assinaturas", 3,
        pai=subgrupo_tecnologia, conta=conta_software,
    )


class Migration(migrations.Migration):
    dependencies = [("financeiro", "0032_configurar_pix_orenda_link_eureka")]
    operations = [
        migrations.AddField(
            model_name="contapagar",
            name="descricao_despesa",
            field=models.CharField(blank=True, max_length=180),
        ),
        migrations.AddField(
            model_name="planocontas",
            name="despesa_pessoal",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(configurar_categorias, migrations.RunPython.noop),
    ]
