from datetime import date

from django.db import migrations
from django.db.models import Max


def criar_insumos(apps, schema_editor):
    Empresa = apps.get_model('core', 'Empresa')
    PlanoContabil = apps.get_model('financeiro', 'PlanoContabil')
    PlanoContas = apps.get_model('financeiro', 'PlanoContas')

    for empresa in Empresa.objects.all():
        conta_contabil = PlanoContabil.objects.filter(
            empresa=empresa,
            descricao__iexact='Insumos',
            tipo_conta='A',
        ).first()
        if not conta_contabil:
            pai_contabil = PlanoContabil.objects.filter(
                empresa=empresa,
                classificacao='32401',
            ).first()
            max_ref = PlanoContabil.objects.filter(empresa=empresa).aggregate(v=Max('codigo_referencia'))['v'] or 0
            max_ordem = PlanoContabil.objects.filter(empresa=empresa).aggregate(v=Max('ordem'))['v'] or 0
            classificacao = '3240100005'
            while PlanoContabil.objects.filter(empresa=empresa, classificacao=classificacao).exists():
                classificacao = str(int(classificacao) + 1)
            conta_contabil = PlanoContabil.objects.create(
                empresa=empresa,
                conta_pai=pai_contabil,
                codigo_referencia=max_ref + 1,
                classificacao=classificacao,
                tipo_conta='A',
                descricao='Insumos',
                data_inicio=date(2026, 1, 1),
                nivel=(pai_contabil.nivel + 1) if pai_contabil else 5,
                ordem=max_ordem + 1,
                origem='Cadastro automatico do sistema',
                ativo=True,
            )

        grupo = (
            PlanoContas.objects.filter(empresa=empresa, codigo='324').first()
            or PlanoContas.objects.filter(
                empresa=empresa,
                descricao__iexact='Custos das Mercadorias Vendidas',
                nivel=1,
            ).first()
        )
        if not grupo:
            grupo = PlanoContas.objects.create(
                empresa=empresa,
                codigo='324',
                descricao='Custos das Mercadorias Vendidas',
                nivel=1,
                tipo='D',
                aceita_lancamento=False,
                ativo=True,
            )

        subgrupo = (
            PlanoContas.objects.filter(empresa=empresa, codigo='32401').first()
            or PlanoContas.objects.filter(
                empresa=empresa,
                conta_pai=grupo,
                descricao__iexact='Mercadorias e Insumos',
                nivel=2,
            ).first()
        )
        if not subgrupo:
            subgrupo = PlanoContas.objects.create(
                empresa=empresa,
                conta_pai=grupo,
                codigo='32401',
                descricao='Mercadorias e Insumos',
                nivel=2,
                tipo='D',
                aceita_lancamento=False,
                ativo=True,
            )

        categoria = PlanoContas.objects.filter(
            empresa=empresa,
            descricao__iexact='Insumos',
            aceita_lancamento=True,
        ).first()
        if not categoria:
            categoria = PlanoContas.objects.create(
                empresa=empresa,
                conta_pai=subgrupo,
                codigo=conta_contabil.classificacao,
                descricao='Insumos',
                nivel=3,
                tipo='D',
                aceita_lancamento=True,
                conta_contabil=conta_contabil,
                ativo=True,
            )
        elif not categoria.conta_contabil_id:
            categoria.conta_contabil = conta_contabil
            categoria.save(update_fields=['conta_contabil'])


class Migration(migrations.Migration):

    dependencies = [
        ('financeiro', '0047_contapagar_regra_vencimento_mensal'),
    ]

    operations = [
        migrations.RunPython(criar_insumos, migrations.RunPython.noop),
    ]
