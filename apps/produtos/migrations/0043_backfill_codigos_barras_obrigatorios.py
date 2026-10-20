import hashlib

from django.conf import settings
from django.db import migrations


def _digito_verificador(base):
    soma = sum(
        int(digito) * (1 if indice % 2 == 0 else 3)
        for indice, digito in enumerate(base)
    )
    return str((10 - (soma % 10)) % 10)


def _codigo_interno(identidade, usados):
    for tentativa in range(10_000):
        semente = f'{identidade}:{tentativa}'.encode()
        numero = int.from_bytes(hashlib.sha256(semente).digest()[:8], 'big') % 1_000_000_000
        base = f'290{numero:09d}'
        codigo = f'{base}{_digito_verificador(base)}'
        if codigo not in usados:
            usados.add(codigo)
            return codigo
    raise RuntimeError('Nao foi possivel gerar um codigo de barras interno unico.')


def preencher_codigos_barras(apps, schema_editor):
    alias = schema_editor.connection.alias
    # No app central, o banco default e o diretório gerencial e contém apenas
    # um retrato histórico das tabelas operacionais. Os tenants são migrados
    # separadamente pelo comando migrate_tenant_databases.
    if alias == 'default' and getattr(settings, 'TENANT_DATABASE_ROUTING_ENABLED', False):
        return

    Produto = apps.get_model('produtos', 'Produto')
    ProdutoApresentacao = apps.get_model('produtos', 'ProdutoApresentacao')
    ProdutoCodigoBarras = apps.get_model('produtos', 'ProdutoCodigoBarras')
    ProdutoFornecedorEquivalencia = apps.get_model(
        'produtos', 'ProdutoFornecedorEquivalencia',
    )

    usados = {
        str(codigo).strip()
        for codigo in Produto.objects.using(alias).values_list('codigo_barras', flat=True)
        if str(codigo or '').strip()
    }
    for extras in Produto.objects.using(alias).values_list('codigos_barras_extras', flat=True):
        if isinstance(extras, list):
            usados.update(str(codigo).strip() for codigo in extras if str(codigo or '').strip())
    usados.update(
        str(codigo).strip()
        for codigo in ProdutoCodigoBarras.objects.using(alias).values_list('ean', flat=True)
        if str(codigo or '').strip()
    )
    usados.update(
        str(codigo).strip()
        for codigo in ProdutoFornecedorEquivalencia.objects.using(alias)
        .values_list('ean_utilizado', flat=True)
        if str(codigo or '').strip()
    )

    produtos = list(
        Produto.objects.using(alias)
        .select_related('filial__empresa')
        .order_by('pk')
    )
    por_id = {produto.pk: produto for produto in produtos}
    for produto in produtos:
        if str(produto.codigo_barras or '').strip():
            continue
        empresa = produto.filial.empresa
        identidade = f'produto:{empresa.cnpj or empresa.pk}:{produto.pk}'
        produto.codigo_barras = _codigo_interno(identidade, usados)
        produto.save(using=alias, update_fields=['codigo_barras'])

    apresentacoes = (
        ProdutoApresentacao.objects.using(alias)
        .filter(ativo=True)
        .order_by('pk')
    )
    for apresentacao in apresentacoes.iterator():
        if ProdutoCodigoBarras.objects.using(alias).filter(
            apresentacao_id=apresentacao.pk,
            ativo=True,
        ).exists():
            continue

        produto = por_id[apresentacao.produto_id]
        codigo = None
        if apresentacao.fator_conversao == 1:
            candidato = str(produto.codigo_barras or '').strip()
            conflito = ProdutoCodigoBarras.objects.using(alias).filter(
                ean=candidato,
                ativo=True,
                apresentacao__isnull=False,
            ).exists()
            if candidato and not conflito:
                codigo = candidato

        if codigo is None:
            empresa = produto.filial.empresa
            identidade = f'apresentacao:{empresa.cnpj or empresa.pk}:{apresentacao.pk}'
            codigo = _codigo_interno(identidade, usados)

        registro = (
            ProdutoCodigoBarras.objects.using(alias)
            .filter(produto_id=produto.pk, ean=codigo)
            .order_by('pk')
            .first()
        )
        if registro is None:
            registro = ProdutoCodigoBarras(produto_id=produto.pk, ean=codigo)
        registro.apresentacao_id = apresentacao.pk
        registro.tipo = 'unidade' if apresentacao.fator_conversao == 1 else 'caixa'
        registro.quantidade_conversao = apresentacao.fator_conversao
        registro.ativo = True
        registro.save(using=alias)


class Migration(migrations.Migration):

    dependencies = [
        ('produtos', '0042_produto_catalogo'),
    ]

    operations = [
        migrations.RunPython(preencher_codigos_barras, migrations.RunPython.noop),
    ]
