from decimal import Decimal

from django.db import migrations
from django.db.models import Max, Q
from django.utils import timezone


MATRIZ_CNPJ = "06722483000114"
FILIAL_CNPJ = "06722483000115"
MARCADOR = "TESTE-EQUILIBRIO-ITED-20260913"


def _deposito_padrao(Deposito, db, filial_id):
    deposito = (
        Deposito.objects.using(db)
        .filter(filial_id=filial_id, is_padrao=True)
        .first()
    )
    if deposito:
        return deposito
    return Deposito.objects.using(db).create(
        filial_id=filial_id,
        nome="Estoque Geral",
        tipo="geral",
        is_padrao=True,
        permite_venda=True,
        permite_producao=True,
        ativo=True,
    )


def _criar_produto_teste(
    *, Produto, ProdutoFilial, db, matriz, filial, unidade, codigo, descricao,
    preco, estoque_maximo,
):
    produto = (
        Produto.objects.using(db)
        .filter(filial_id=matriz.pk, codigo=codigo)
        .first()
    )
    if not produto:
        produto = Produto.objects.using(db).create(
            filial_id=matriz.pk,
            unidade_medida_id=unidade.pk,
            codigo=codigo,
            descricao=descricao,
            descricao_curta=descricao[:120],
            descricao_pdv=descricao[:80],
            ncm="21069090",
            tipo_produto="unitario",
            preco_custo=Decimal("5.0000"),
            preco_custo_medio=Decimal("5.0000"),
            preco_venda=preco,
            estoque_minimo=Decimal("10.000"),
            estoque_maximo=estoque_maximo,
            estoque_seguranca=Decimal("5.000"),
            lead_time_reposicao_dias=3,
            controla_lote=False,
            controla_validade=False,
            permite_venda_sem_estoque=True,
            ativo=True,
        )
    for unidade_filial in (matriz, filial):
        ProdutoFilial.objects.using(db).update_or_create(
            produto_id=produto.pk,
            filial_id=unidade_filial.pk,
            defaults={"ativo": True},
        )
    return produto


def _entrada_inicial(
    *, Estoque, MovimentacaoEstoque, db, produto, filial, deposito, usuario,
    quantidade, data_movimentacao,
):
    estoque, _ = Estoque.objects.using(db).get_or_create(
        produto_id=produto.pk,
        filial_id=filial.pk,
        deposito_id=deposito.pk,
        defaults={
            "quantidade_atual": Decimal("0"),
            "quantidade_reservada": Decimal("0"),
            "quantidade_disponivel": Decimal("0"),
            "custo_medio": Decimal("5.0000"),
        },
    )
    anterior = estoque.quantidade_atual
    custo_anterior = estoque.custo_medio
    posterior = anterior + quantidade
    estoque.quantidade_atual = posterior
    estoque.quantidade_disponivel = posterior - estoque.quantidade_reservada
    estoque.custo_medio = Decimal("5.0000")
    estoque.ultima_entrada = data_movimentacao
    estoque.save(using=db)
    MovimentacaoEstoque.objects.using(db).create(
        produto_id=produto.pk,
        filial_id=filial.pk,
        deposito_id=deposito.pk,
        tipo_operacao="ajuste_mais",
        documento_tipo="ajuste_manual",
        documento_numero="EQ-TESTE",
        quantidade=quantidade,
        quantidade_anterior=anterior,
        quantidade_posterior=posterior,
        valor_unitario=Decimal("5.0000"),
        valor_total=(quantidade * Decimal("5.00")),
        custo_medio_anterior=custo_anterior,
        custo_medio_posterior=Decimal("5.0000"),
        usuario_id=usuario.pk,
        observacao=f"{MARCADOR}: carga inicial para validar equilíbrio de estoque.",
        data_movimentacao=data_movimentacao,
    )
    return estoque


def _criar_vendas(
    *, VendaPDV, ItemVendaPDV, PagamentoVendaPDV, MovimentacaoEstoque,
    db, produto, filial, deposito, usuario, estoque, forma_pagamento,
    quantidade, preco, prefixo,
):
    proximo_numero = (
        VendaPDV.objects.using(db)
        .filter(filial_id=filial.pk)
        .aggregate(maximo=Max("numero_venda"))["maximo"]
        or 0
    ) + 1
    agora = timezone.now()
    for indice, dias_atras in enumerate((23, 19, 15, 11, 7, 3), start=1):
        chave = f"{MARCADOR}-{prefixo}-{indice}"
        if VendaPDV.objects.using(db).filter(idempotency_key=chave).exists():
            continue
        data_venda = agora - timezone.timedelta(days=dias_atras)
        total = (quantidade * preco).quantize(Decimal("0.01"))
        venda = VendaPDV.objects.using(db).create(
            filial_id=filial.pk,
            numero_venda=proximo_numero,
            status="finalizada",
            origem="pdv",
            valor_subtotal=total,
            valor_total=total,
            valor_pago=total,
            troco=Decimal("0.00"),
            usuario_id=usuario.pk,
            data_venda=data_venda,
            observacao=f"{MARCADOR}: venda retroativa para validar equilíbrio de estoque.",
            idempotency_key=chave,
        )
        proximo_numero += 1
        anterior = estoque.quantidade_atual
        posterior = anterior - quantidade
        movimentacao = MovimentacaoEstoque.objects.using(db).create(
            produto_id=produto.pk,
            filial_id=filial.pk,
            deposito_id=deposito.pk,
            tipo_operacao="saida",
            documento_id=venda.pk,
            documento_numero=str(venda.numero_venda),
            quantidade=quantidade,
            quantidade_anterior=anterior,
            quantidade_posterior=posterior,
            valor_unitario=Decimal("5.0000"),
            valor_total=(quantidade * Decimal("5.00")),
            custo_medio_anterior=Decimal("5.0000"),
            custo_medio_posterior=Decimal("5.0000"),
            usuario_id=usuario.pk,
            observacao=f"{MARCADOR}: baixa da venda retroativa de teste.",
            data_movimentacao=data_venda,
        )
        ItemVendaPDV.objects.using(db).create(
            venda_pdv_id=venda.pk,
            produto_id=produto.pk,
            numero_item=1,
            tipo_venda="unitario",
            quantidade=quantidade,
            unidade_medida="UN",
            valor_unitario=preco,
            custo_unitario_snapshot=Decimal("5.0000"),
            preco_origem="Dados de teste",
            preco_origem_detalhe=MARCADOR,
            valor_total=total,
            estoque_baixado=True,
            movimentacoes_estoque_ids=[movimentacao.pk],
            observacao=MARCADOR,
        )
        if forma_pagamento:
            PagamentoVendaPDV.objects.using(db).create(
                venda_pdv_id=venda.pk,
                forma_pagamento_id=forma_pagamento.pk,
                valor=total,
                valor_liquido=total,
                numero_parcelas=1,
                status="aprovado",
                taxa_calculada_em=data_venda,
                data_liquidacao_prevista=data_venda.date(),
            )
        estoque.quantidade_atual = posterior
        estoque.quantidade_disponivel = posterior - estoque.quantidade_reservada
        estoque.ultima_saida = data_venda
        estoque.save(using=db)


def _semear_no_banco(apps, schema_editor):
    db = schema_editor.connection.alias
    Filial = apps.get_model("core", "Filial")
    Usuario = apps.get_model("core", "Usuario")
    UnidadeMedida = apps.get_model("produtos", "UnidadeMedida")
    UnidadeMedidaFilial = apps.get_model("produtos", "UnidadeMedidaFilial")
    Produto = apps.get_model("produtos", "Produto")
    ProdutoFilial = apps.get_model("produtos", "ProdutoFilial")
    Deposito = apps.get_model("estoque", "Deposito")
    Estoque = apps.get_model("estoque", "Estoque")
    MovimentacaoEstoque = apps.get_model("estoque", "MovimentacaoEstoque")
    FormaPagamento = apps.get_model("financeiro", "FormaPagamento")
    VendaPDV = apps.get_model("pdv", "VendaPDV")
    ItemVendaPDV = apps.get_model("pdv", "ItemVendaPDV")
    PagamentoVendaPDV = apps.get_model("pdv", "PagamentoVendaPDV")

    filiais = list(
        Filial.objects.using(db)
        .filter(cnpj__in=(MATRIZ_CNPJ, FILIAL_CNPJ), ativo=True)
        .select_related("empresa")
    )
    if len(filiais) != 2 or len({filial.empresa_id for filial in filiais}) != 1:
        return
    por_cnpj = {filial.cnpj: filial for filial in filiais}
    matriz = por_cnpj[MATRIZ_CNPJ]
    filial = por_cnpj[FILIAL_CNPJ]
    if VendaPDV.objects.using(db).filter(
        idempotency_key__startswith=MARCADOR,
    ).count() >= 12:
        return

    usuario = (
        Usuario.objects.using(db)
        .filter(ativo=True, empresa_id=matriz.empresa_id)
        .order_by("pk")
        .first()
    )
    if not usuario:
        raise RuntimeError("Empresa iTED localizada sem usuário ativo no banco operacional.")

    unidade = (
        UnidadeMedida.objects.using(db)
        .filter(empresa_id=matriz.empresa_id, sigla__iexact="UN", ativo=True)
        .first()
    )
    if not unidade:
        unidade = UnidadeMedida.objects.using(db).create(
            empresa_id=matriz.empresa_id,
            sigla="UN",
            descricao="Unidade",
            tipo="unidade",
            ativo=True,
        )
    for unidade_filial in (matriz, filial):
        UnidadeMedidaFilial.objects.using(db).update_or_create(
            unidade_id=unidade.pk,
            filial_id=unidade_filial.pk,
            defaults={"ativo": True},
        )

    forma_pagamento = (
        FormaPagamento.objects.using(db)
        .filter(empresa_id=matriz.empresa_id, ativo=True, tipo="dinheiro")
        .filter(Q(filial_id__isnull=True) | Q(filial_id__in=(matriz.pk, filial.pk)))
        .order_by("filial_id", "pk")
        .first()
    )
    deposito_matriz = _deposito_padrao(Deposito, db, matriz.pk)
    deposito_filial = _deposito_padrao(Deposito, db, filial.pk)
    data_inicial = timezone.now() - timezone.timedelta(days=29)

    produto_matriz = _criar_produto_teste(
        Produto=Produto, ProdutoFilial=ProdutoFilial, db=db,
        matriz=matriz, filial=filial, unidade=unidade,
        codigo="EQ-TESTE-MATRIZ",
        descricao="TESTE EQUILÍBRIO — GIRO NA MATRIZ",
        preco=Decimal("12.5000"), estoque_maximo=Decimal("90.000"),
    )
    estoque_matriz_a = _entrada_inicial(
        Estoque=Estoque, MovimentacaoEstoque=MovimentacaoEstoque, db=db,
        produto=produto_matriz, filial=matriz, deposito=deposito_matriz,
        usuario=usuario, quantidade=Decimal("64.000"),
        data_movimentacao=data_inicial,
    )
    _entrada_inicial(
        Estoque=Estoque, MovimentacaoEstoque=MovimentacaoEstoque, db=db,
        produto=produto_matriz, filial=filial, deposito=deposito_filial,
        usuario=usuario, quantidade=Decimal("500.000"),
        data_movimentacao=data_inicial,
    )
    _criar_vendas(
        VendaPDV=VendaPDV, ItemVendaPDV=ItemVendaPDV,
        PagamentoVendaPDV=PagamentoVendaPDV,
        MovimentacaoEstoque=MovimentacaoEstoque, db=db,
        produto=produto_matriz, filial=matriz, deposito=deposito_matriz,
        usuario=usuario, estoque=estoque_matriz_a,
        forma_pagamento=forma_pagamento, quantidade=Decimal("10.000"),
        preco=Decimal("12.5000"), prefixo="MATRIZ",
    )

    produto_filial = _criar_produto_teste(
        Produto=Produto, ProdutoFilial=ProdutoFilial, db=db,
        matriz=matriz, filial=filial, unidade=unidade,
        codigo="EQ-TESTE-FILIAL",
        descricao="TESTE EQUILÍBRIO — GIRO NA FILIAL",
        preco=Decimal("18.0000"), estoque_maximo=Decimal("80.000"),
    )
    _entrada_inicial(
        Estoque=Estoque, MovimentacaoEstoque=MovimentacaoEstoque, db=db,
        produto=produto_filial, filial=matriz, deposito=deposito_matriz,
        usuario=usuario, quantidade=Decimal("500.000"),
        data_movimentacao=data_inicial,
    )
    estoque_filial_b = _entrada_inicial(
        Estoque=Estoque, MovimentacaoEstoque=MovimentacaoEstoque, db=db,
        produto=produto_filial, filial=filial, deposito=deposito_filial,
        usuario=usuario, quantidade=Decimal("51.000"),
        data_movimentacao=data_inicial,
    )
    _criar_vendas(
        VendaPDV=VendaPDV, ItemVendaPDV=ItemVendaPDV,
        PagamentoVendaPDV=PagamentoVendaPDV,
        MovimentacaoEstoque=MovimentacaoEstoque, db=db,
        produto=produto_filial, filial=filial, deposito=deposito_filial,
        usuario=usuario, estoque=estoque_filial_b,
        forma_pagamento=forma_pagamento, quantidade=Decimal("8.000"),
        preco=Decimal("18.0000"), prefixo="FILIAL",
    )


def semear_equilibrio_ited(apps, schema_editor):
    # O banco default é o diretório gerencial. Dados operacionais pertencem
    # exclusivamente ao tenant e nunca devem ser semeados no diretório.
    if schema_editor.connection.alias == "default":
        return
    _semear_no_banco(apps, schema_editor)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0070_parametrossistema_checkout_busca_nome_senha"),
        ("estoque", "0022_movimentacao_apresentacao"),
        ("financeiro", "0067_reaplicar_data_entradas_manuais"),
        ("produtos", "0031_produto_tempo_preparo_minutos"),
        ("pdv", "0021_venda_pdv_viagem"),
    ]

    operations = [
        migrations.RunPython(semear_equilibrio_ited, migrations.RunPython.noop),
    ]
