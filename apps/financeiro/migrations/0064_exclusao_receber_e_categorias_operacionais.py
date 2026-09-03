from django.db import migrations, models
import django.db.models.deletion


def cadastrar_categorias_operacionais(apps, schema_editor):
    Empresa = apps.get_model('core', 'Empresa')
    PlanoContabil = apps.get_model('financeiro', 'PlanoContabil')
    PlanoContas = apps.get_model('financeiro', 'PlanoContas')
    empresa = Empresa.objects.filter(cnpj='50649395000126').first()
    if not empresa:
        return

    def categoria(codigo, descricao, nivel, pai=None, conta=None):
        item, _ = PlanoContas.objects.update_or_create(
            empresa=empresa, codigo=codigo,
            defaults={
                'descricao': descricao, 'tipo': 'D', 'nivel': nivel,
                'conta_pai': pai, 'conta_contabil': conta if nivel == 3 else None,
                'aceita_lancamento': nivel == 3, 'ativo': True,
            },
        )
        return item

    gerais = PlanoContas.objects.filter(empresa=empresa, codigo='332').first()
    if not gerais:
        gerais = categoria('332', 'Despesas Administrativas', 1)
    transporte = categoria('33206', 'Transporte e Deslocamentos', 2, gerais)
    conta_uber, _ = PlanoContabil.objects.get_or_create(
        empresa=empresa, classificacao='3320400023',
        defaults={
            'conta_pai': PlanoContabil.objects.filter(empresa=empresa, classificacao='33204').first(),
            'codigo_referencia': (PlanoContabil.objects.filter(empresa=empresa).order_by('-codigo_referencia').values_list('codigo_referencia', flat=True).first() or 0) + 1,
            'tipo_conta': 'A', 'descricao': 'APLICATIVOS DE TRANSPORTE (UBER)',
            'data_inicio': '2026-09-03', 'nivel': 5,
            'ordem': (PlanoContabil.objects.filter(empresa=empresa).order_by('-ordem').values_list('ordem', flat=True).first() or 0) + 1,
            'origem': 'Complemento operacional - iTed', 'ativo': True,
        },
    )
    conta_taxi, _ = PlanoContabil.objects.get_or_create(
        empresa=empresa, classificacao='3320400024',
        defaults={
            'conta_pai': PlanoContabil.objects.filter(empresa=empresa, classificacao='33204').first(),
            'codigo_referencia': (PlanoContabil.objects.filter(empresa=empresa).order_by('-codigo_referencia').values_list('codigo_referencia', flat=True).first() or 0) + 1,
            'tipo_conta': 'A', 'descricao': 'TÁXI', 'data_inicio': '2026-09-03', 'nivel': 5,
            'ordem': (PlanoContabil.objects.filter(empresa=empresa).order_by('-ordem').values_list('ordem', flat=True).first() or 0) + 1,
            'origem': 'Complemento operacional - iTed', 'ativo': True,
        },
    )
    categoria('3320600001', 'Uber e aplicativos de transporte', 3, transporte, conta_uber)
    categoria('3320600002', 'Táxi', 3, transporte, conta_taxi)

    investimento = categoria('398', 'Investimentos em Produção', 1)
    maquinas = categoria('39801', 'Máquinas e Ferramentas', 2, investimento)
    imobilizado = PlanoContabil.objects.filter(
        empresa=empresa, classificacao='1240300001', tipo_conta='A',
    ).first()
    if imobilizado:
        categoria('3980100001', 'Compra de máquinas e equipamentos para produção', 3, maquinas, imobilizado)
        categoria('3980100002', 'Compra de ferramentas para produção', 3, maquinas, imobilizado)


class Migration(migrations.Migration):
    dependencies = [('financeiro', '0063_pagamentocontapagar_tarifa_bancaria')]
    operations = [
        migrations.AddField(model_name='contareceber', name='excluido_em', field=models.DateTimeField(blank=True, db_index=True, null=True)),
        migrations.AddField(model_name='contareceber', name='motivo_exclusao', field=models.CharField(blank=True, max_length=300)),
        migrations.AddField(model_name='contareceber', name='excluido_por', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='contas_receber_excluidas', to='core.usuario')),
        migrations.RunPython(cadastrar_categorias_operacionais, migrations.RunPython.noop),
    ]
