from django.db import migrations, models
import django.db.models.deletion


CATEGORIAS = [
    ("3320100001", "Salarios e Ordenados"),
    ("3320100002", "Pro-labore"),
    ("3320100003", "Premios e Gratificacoes"),
    ("3320100004", "13o Salario"),
    ("3320100005", "Ferias"),
    ("3320100006", "INSS"),
    ("3320100007", "FGTS"),
    ("3320100008", "Indenizacoes e Aviso Previo"),
    ("3320100009", "Assistencia Medica e Social"),
    ("3320100010", "Vale Transporte"),
    ("3320100011", "PIS sobre Folha"),
    ("3320100012", "Alimentacao de Funcionarios"),
    ("3320100013", "Bolsa-auxilio"),
    ("3320100014", "Viagens e Estadias"),
    ("3320100016", "Rescisoes"),
]


def criar_categorias_pessoal(apps, schema_editor):
    Empresa = apps.get_model("core", "Empresa")
    PlanoContas = apps.get_model("financeiro", "PlanoContas")
    PlanoContabil = apps.get_model("financeiro", "PlanoContabil")

    for empresa in Empresa.objects.all().iterator():
        grupo = PlanoContas.objects.filter(empresa=empresa, codigo="332").order_by("pk").first()
        if not grupo:
            grupo = PlanoContas.objects.create(
                empresa=empresa, codigo="332", descricao="Despesas Administrativas",
                tipo="D", nivel=1, aceita_lancamento=False,
            )
        grupo.descricao = "Despesas Administrativas"
        grupo.tipo = "D"
        grupo.nivel = 1
        grupo.aceita_lancamento = False
        grupo.ativo = True
        grupo.save()

        subgrupo = PlanoContas.objects.filter(empresa=empresa, codigo="33201").order_by("pk").first()
        if not subgrupo:
            subgrupo = PlanoContas.objects.create(
                empresa=empresa, codigo="33201", descricao="Despesas com Pessoal",
                tipo="D", nivel=2, aceita_lancamento=False, conta_pai=grupo,
            )
        subgrupo.descricao = "Despesas com Pessoal"
        subgrupo.tipo = "D"
        subgrupo.nivel = 2
        subgrupo.aceita_lancamento = False
        subgrupo.ativo = True
        subgrupo.conta_pai = grupo
        subgrupo.save()

        for classificacao, descricao in CATEGORIAS:
            conta = PlanoContabil.objects.filter(
                empresa=empresa, classificacao=classificacao, tipo_conta="A", ativo=True,
            ).first()
            if not conta:
                continue
            categoria = PlanoContas.objects.filter(
                empresa=empresa, codigo=classificacao,
            ).order_by("pk").first()
            if not categoria:
                categoria = PlanoContas.objects.create(
                    empresa=empresa, codigo=classificacao, descricao=descricao, tipo="D",
                    nivel=3, aceita_lancamento=True, conta_pai=subgrupo, conta_contabil=conta,
                )
            categoria.descricao = descricao
            categoria.tipo = "D"
            categoria.nivel = 3
            categoria.aceita_lancamento = True
            categoria.ativo = True
            categoria.conta_pai = subgrupo
            categoria.conta_contabil = conta
            categoria.save()


class Migration(migrations.Migration):
    dependencies = [
        ("cadastros", "0015_funcionario"),
        ("financeiro", "0017_categorias_financeiras_conta_contabil"),
    ]

    operations = [
        migrations.AddField(
            model_name="contapagar", name="funcionario",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="contas_pagar", to="cadastros.funcionario"),
        ),
        migrations.AddField(
            model_name="contapagar", name="tipo_lancamento",
            field=models.CharField(choices=[("fornecedor", "Fornecedor ou outro"), ("funcionario", "Pagamento ao funcionario"), ("encargo", "Encargo ou beneficio")], default="fornecedor", max_length=12),
        ),
        migrations.AddIndex(model_name="contapagar", index=models.Index(fields=["filial", "funcionario"], name="contas_paga_filial__6852e1_idx")),
        migrations.RunPython(criar_categorias_pessoal, migrations.RunPython.noop),
    ]
