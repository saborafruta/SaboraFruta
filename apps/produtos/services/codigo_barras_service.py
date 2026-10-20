"""Geracao e verificacao de codigos de barras internos de produtos."""
import secrets

from django.db.models import Q

from apps.produtos.models import (
    Produto,
    ProdutoCodigoBarras,
    ProdutoFornecedorEquivalencia,
)


PREFIXO_EAN_INTERNO = '290'
MAX_TENTATIVAS_GERACAO = 100


def calcular_digito_verificador_ean13(base: str) -> str:
    """Calcula o digito verificador de uma base EAN-13 com 12 digitos."""
    if len(base) != 12 or not base.isdigit():
        raise ValueError('A base do EAN-13 deve conter 12 digitos.')
    soma = sum(
        int(digito) * (1 if indice % 2 == 0 else 3)
        for indice, digito in enumerate(base)
    )
    return str((10 - (soma % 10)) % 10)


def ean13_valido(codigo: str) -> bool:
    codigo = str(codigo or '').strip()
    return (
        len(codigo) == 13
        and codigo.isdigit()
        and codigo[-1] == calcular_digito_verificador_ean13(codigo[:12])
    )


def _produtos_da_empresa(empresa=None):
    queryset = Produto.objects.all()
    if empresa is not None:
        queryset = queryset.filter(
            Q(filial__empresa=empresa)
            | Q(filiais_vinculo__filial__empresa=empresa)
        ).distinct()
    return queryset


def codigo_barras_em_uso(codigo: str, *, empresa=None, produto_id=None) -> bool:
    """Confere todos os locais em que um produto pode guardar um codigo."""
    codigo = str(codigo or '').strip()
    if not codigo:
        return False

    produtos = _produtos_da_empresa(empresa)
    if produto_id:
        produtos = produtos.exclude(pk=produto_id)

    if produtos.filter(codigo_barras=codigo).exists():
        return True

    if ProdutoCodigoBarras.objects.filter(
        produto__in=produtos,
        ean=codigo,
    ).exists():
        return True

    if ProdutoFornecedorEquivalencia.objects.filter(
        produto__in=produtos,
        ean_utilizado=codigo,
    ).exists():
        return True

    # Funciona igualmente em PostgreSQL e SQLite. Alguns bancos nao suportam
    # o lookup JSON `contains`, entao a verificacao final e feita em Python.
    return any(
        codigo in {str(item).strip() for item in (extras or [])}
        for extras in produtos.values_list('codigos_barras_extras', flat=True)
        if isinstance(extras, list)
    )


def codigo_barras_conflita_apresentacao(codigo, *, produto=None, empresa=None) -> bool:
    """Impede que o código principal seja trocado pelo EAN de uma embalagem."""
    if empresa is None and produto is not None:
        empresa = getattr(getattr(produto, 'filial', None), 'empresa', None)
    registros = ProdutoCodigoBarras.objects.filter(
        produto__in=_produtos_da_empresa(empresa),
        ean=str(codigo or '').strip(),
        ativo=True,
        apresentacao__isnull=False,
    )
    if produto and codigo == str(produto.codigo_barras or '').strip():
        # Manter o mesmo código da apresentação base é válido; trocar para o
        # código de um fardo/caixa não é.
        registros = registros.exclude(
            produto=produto,
            apresentacao__fator_conversao=1,
        )
    return registros.exists()


def gerar_codigo_barras_unico(*, empresa=None) -> str:
    """Gera um EAN-13 interno valido e ainda nao usado pela empresa."""
    for _ in range(MAX_TENTATIVAS_GERACAO):
        corpo = f'{PREFIXO_EAN_INTERNO}{secrets.randbelow(1_000_000_000):09d}'
        codigo = f'{corpo}{calcular_digito_verificador_ean13(corpo)}'
        if not codigo_barras_em_uso(codigo, empresa=empresa):
            return codigo
    raise RuntimeError('Nao foi possivel gerar um codigo de barras unico. Tente novamente.')


def codigo_barras_apresentacao_em_uso(
    codigo, *, produto, registro_id=None, permitir_codigo_produto=False,
) -> bool:
    """Valida um EAN de apresentação sem rejeitar o EAN legado do próprio produto."""
    empresa = getattr(getattr(produto, 'filial', None), 'empresa', None)
    produtos = _produtos_da_empresa(empresa)
    registros = ProdutoCodigoBarras.objects.filter(
        produto__in=produtos,
        ean=codigo,
        ativo=True,
    )
    if registro_id:
        registros = registros.exclude(pk=registro_id)
    reutiliza_codigo_principal = (
        permitir_codigo_produto
        and codigo
        and codigo == str(produto.codigo_barras or '').strip()
    )
    if reutiliza_codigo_principal:
        # A apresentação base pode assumir o registro canônico antigo do
        # próprio produto; códigos ligados a outra apresentação continuam
        # sendo conflito.
        registros = registros.exclude(
            produto=produto,
            apresentacao__isnull=True,
        )
    if registros.exists():
        return True
    if produtos.exclude(pk=produto.pk).filter(codigo_barras=codigo).exists():
        return True
    if not reutiliza_codigo_principal and produto.codigo_barras == codigo:
        return True
    if any(
        codigo in {str(item).strip() for item in (extras or [])}
        for extras in produtos.values_list('codigos_barras_extras', flat=True)
        if isinstance(extras, list)
    ):
        return True
    return ProdutoFornecedorEquivalencia.objects.filter(
        produto__in=produtos,
        ean_utilizado=codigo,
    ).exists()


def salvar_codigo_barras_apresentacao(apresentacao, codigo):
    """Mantém um código principal ativo para a apresentação informada."""
    codigo = str(codigo or '').strip()
    ativos = apresentacao.codigos_barras.filter(ativo=True).order_by('pk')
    registro = ativos.filter(ean=codigo).first()
    ativos.exclude(ean=codigo).update(ativo=False)

    if registro is None:
        registro = ProdutoCodigoBarras.objects.filter(
            produto=apresentacao.produto,
            ean=codigo,
        ).order_by('pk').first()
    if registro is None:
        registro = ProdutoCodigoBarras(produto=apresentacao.produto, ean=codigo)

    registro.apresentacao = apresentacao
    registro.tipo = (
        ProdutoCodigoBarras.Tipo.UNIDADE
        if apresentacao.fator_conversao == 1
        else ProdutoCodigoBarras.Tipo.CAIXA
    )
    registro.quantidade_conversao = apresentacao.fator_conversao
    registro.ativo = True
    registro.save()
    return registro
