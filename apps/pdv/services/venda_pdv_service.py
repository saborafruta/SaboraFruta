from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone

from apps.core.services.exceptions import DadosInvalidosError, EstoqueInsuficienteError
from apps.estoque.models import MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.financeiro.constants.enums import StatusContaReceber
from apps.financeiro.models import ContaReceber, FormaPagamento
from apps.pdv.models import ItemVendaPDV, PagamentoVendaPDV, VendaPDV
from apps.pdv.services.produto_vendavel_service import ProdutoVendavelService
from apps.produtos.models import Produto
from apps.produtos.models import BrindeProduto, KitProduto
from apps.produtos.services.preco_service import PrecoService


class VendaPDVService:
    """Contrato central entre PDV, promocoes e estoque."""

    MONEY = Decimal("0.01")
    UNIT = Decimal("0.0001")

    @classmethod
    @transaction.atomic
    def finalizar_venda(
        cls,
        *,
        sessao,
        filial,
        usuario,
        itens: list[dict],
        pagamentos: list[dict],
        cliente_id: int | None = None,
        desconto=Decimal("0"),
        acrescimo=Decimal("0"),
        delivery: bool = False,
        endereco_entrega: dict | None = None,
        forcar_estoque_negativo: bool = True,
        credito_valor: Decimal = Decimal("0"),
        data_venda=None,
    ) -> VendaPDV:
        if not sessao:
            raise DadosInvalidosError("Nenhuma sessao de caixa aberta.")
        if not itens:
            raise DadosInvalidosError("Carrinho vazio.")
        credito_valor = cls._decimal(credito_valor, cls.MONEY)
        if not pagamentos and credito_valor <= 0:
            raise DadosInvalidosError("Informe ao menos uma forma de pagamento.")

        desconto = cls._decimal(desconto, cls.MONEY)
        acrescimo = cls._decimal(acrescimo, cls.MONEY)
        numero = cls._proximo_numero_venda(filial)
        # data_venda retroativa (lançamento de venda antiga) quando informada;
        # caso contrário, carimba o momento atual. A validação de permissão
        # (somente administrador) é feita na view.
        data_venda_efetiva = data_venda or timezone.now()
        venda = VendaPDV.objects.create(
            sessao_pdv=sessao,
            filial=filial,
            numero_venda=numero,
            cliente_id=cliente_id or None,
            status="finalizada",
            delivery=delivery,
            endereco_entrega=endereco_entrega or {},
            valor_desconto=desconto,
            valor_acrescimo=acrescimo,
            usuario=usuario,
            data_venda=data_venda_efetiva,
        )

        subtotal = Decimal("0.00")
        proximo_numero_item = 1
        for item_dados in itens:
            try:
                itens_criados = cls._criar_item_e_baixar_estoque(
                    venda=venda,
                    filial=filial,
                    usuario=usuario,
                    item_dados=item_dados,
                    numero_item=proximo_numero_item,
                    forcar_estoque_negativo=forcar_estoque_negativo,
                )
            except EstoqueInsuficienteError:
                if not forcar_estoque_negativo:
                    raise
                # Operador forçou a venda — registra o item sem baixar estoque
                itens_criados = cls._criar_item_e_baixar_estoque(
                    venda=venda,
                    filial=filial,
                    usuario=usuario,
                    item_dados=item_dados,
                    numero_item=proximo_numero_item,
                    forcar_estoque_negativo=True,
                    _skip_estoque=True,
                )
            for item in itens_criados:
                subtotal += item.valor_total
            proximo_numero_item += len(itens_criados)

        valor_total = cls._decimal(subtotal - desconto + acrescimo, cls.MONEY)
        if valor_total < 0:
            raise DadosInvalidosError("Total da venda nao pode ficar negativo.")

        valor_pago, troco_total = cls._registrar_pagamentos(
            venda=venda,
            filial=filial,
            pagamentos=pagamentos,
            valor_total=valor_total,
            credito_valor=credito_valor,
        )

        if credito_valor > 0 and cliente_id:
            cls._aplicar_credito_cliente(filial, cliente_id, credito_valor)

        venda.valor_subtotal = subtotal
        venda.valor_total = valor_total
        venda.valor_pago = valor_pago
        venda.troco = troco_total
        venda.save(update_fields=[
            "valor_subtotal",
            "valor_total",
            "valor_pago",
            "troco",
            "updated_at",
        ])

        sessao.total_vendas = (sessao.total_vendas or Decimal("0")) + valor_total
        sessao.save(update_fields=["total_vendas"])
        return venda

    @classmethod
    def resolver_preco_produto(cls, produto: Produto, filial, quantidade: Decimal) -> dict:
        contrato = ProdutoVendavelService.consultar(
            produto=produto,
            filial=filial,
            quantidade=quantidade,
        )
        preco = cls._decimal(contrato["preco_aplicado"], cls.UNIT)
        return {
            "preco": preco,
            "tipo": contrato.get("preco_origem_tipo", "normal") or "normal",
            "origem": contrato.get("preco_origem", "Preco de venda") or "Preco de venda",
            "detalhe": contrato.get("preco_origem_detalhe", "") or "",
            "contrato": contrato,
        }

    @classmethod
    def _criar_item_e_baixar_estoque(
        cls,
        *,
        venda: VendaPDV,
        filial,
        usuario,
        item_dados: dict,
        numero_item: int,
        forcar_estoque_negativo: bool = True,
        _skip_estoque: bool = False,
    ) -> list[ItemVendaPDV]:
        tipo_venda = (item_dados.get("tipo_venda") or "unitario").strip() or "unitario"
        if tipo_venda == "kit" or item_dados.get("kit_id"):
            return cls._criar_itens_kit_e_baixar_estoque(
                venda=venda,
                filial=filial,
                usuario=usuario,
                item_dados=item_dados,
                numero_item=numero_item,
                forcar_estoque_negativo=forcar_estoque_negativo,
            )

        produto_id = int(item_dados["produto_id"])
        quantidade = cls._decimal(item_dados.get("quantidade", "0"), Decimal("0.001"))
        if quantidade <= 0:
            raise DadosInvalidosError("Quantidade deve ser positiva.")

        try:
            produto = (
                Produto.objects
                .for_filial(filial)
                .select_related("unidade_medida")
                .get(pk=produto_id, ativo=True)
            )
        except Produto.DoesNotExist:
            raise DadosInvalidosError("Produto nao encontrado ou nao vinculado a filial ativa.")

        contrato = ProdutoVendavelService.validar_venda(
            produto=produto,
            filial=filial,
            quantidade=quantidade,
        )
        preco_info = cls.resolver_preco_produto(produto, filial, quantidade)
        valor_unitario = preco_info["preco"]
        preco_origem_tipo = preco_info["tipo"]
        preco_origem_detalhe = preco_info["detalhe"] or preco_info["origem"]

        preco_manual = item_dados.get("preco_manual")
        if preco_manual not in (None, ""):
            valor_manual = cls._decimal(preco_manual, cls.UNIT)
            if valor_manual <= 0:
                raise DadosInvalidosError("Preco manual deve ser maior que zero.")
            valor_unitario = valor_manual
            preco_origem_tipo = "manual"
            preco_origem_detalhe = (
                f"Preco alterado manualmente pelo operador "
                f"(tabela: R$ {produto.preco_venda})."
            )

        valor_total_item = cls._decimal(quantidade * valor_unitario, cls.MONEY)
        unidade = produto.unidade_medida.sigla if produto.unidade_medida_id else "UN"
        custo_snapshot = contrato["custo_atual"]

        item = ItemVendaPDV.objects.create(
            venda_pdv=venda,
            produto=produto,
            numero_item=numero_item,
            tipo_venda=tipo_venda,
            quantidade=quantidade,
            unidade_medida=unidade,
            valor_unitario=valor_unitario,
            valor_unitario_tabela=produto.preco_venda,
            custo_unitario_snapshot=custo_snapshot,
            preco_origem=preco_origem_tipo,
            preco_origem_detalhe=preco_origem_detalhe,
            valor_total=valor_total_item,
        )

        if produto.tipo_produto == Produto.TipoProduto.SERVICO:
            return [item]

        movimentacoes = cls._baixar_produto_pdv(
            produto=produto,
            filial=filial,
            quantidade=quantidade,
            usuario=usuario,
            venda=venda,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.SAIDA,
            forcar_estoque_negativo=forcar_estoque_negativo,
        )

        item.estoque_baixado = True
        item.movimentacoes_estoque_ids = [mov.pk for mov in movimentacoes]
        item.save(update_fields=[
            "estoque_baixado",
            "movimentacoes_estoque_ids",
        ])
        itens = [item]
        itens.extend(cls._criar_brindes_automaticos(
            venda=venda,
            filial=filial,
            usuario=usuario,
            produto_gatilho=produto,
            quantidade_gatilho=quantidade,
            numero_item_inicial=numero_item,
        ))
        return itens

    @classmethod
    def _criar_itens_kit_e_baixar_estoque(
        cls,
        *,
        venda: VendaPDV,
        filial,
        usuario,
        item_dados: dict,
        numero_item: int,
        forcar_estoque_negativo: bool = True,
    ) -> list[ItemVendaPDV]:
        quantidade_kit = cls._decimal(item_dados.get("quantidade", "1"), Decimal("0.001"))
        if quantidade_kit <= 0:
            raise DadosInvalidosError("Quantidade do kit deve ser positiva.")
        kit_id = int(item_dados["kit_id"])
        try:
            kit = (
                KitProduto.objects.for_filial(filial)
                .prefetch_related("itens__produto__unidade_medida")
                .get(pk=kit_id, ativo=True)
            )
        except KitProduto.DoesNotExist:
            raise DadosInvalidosError("Kit nao encontrado ou nao vinculado a filial ativa.")
        componentes = list(kit.itens.all())
        if not componentes:
            raise DadosInvalidosError("Kit sem itens nao pode ser vendido.")

        itens = []
        subtotal_sem_desconto = Decimal("0.00")
        precos_componentes = []
        for comp in componentes:
            qtd_componente = cls._decimal(comp.quantidade * quantidade_kit, Decimal("0.001"))
            contrato = ProdutoVendavelService.validar_venda(
                produto=comp.produto,
                filial=filial,
                quantidade=qtd_componente,
            )
            preco_unitario = contrato["preco_aplicado"] if kit.permite_preco_promocional else comp.produto.preco_venda
            preco_unitario = cls._decimal(preco_unitario, cls.UNIT)
            total = cls._decimal(qtd_componente * preco_unitario, cls.MONEY)
            subtotal_sem_desconto += total
            precos_componentes.append((comp, qtd_componente, contrato, preco_unitario, total))

        total_kit = cls._aplicar_desconto_kit(subtotal_sem_desconto, kit.tipo_desconto, kit.valor_desconto)
        fator = (total_kit / subtotal_sem_desconto) if subtotal_sem_desconto > 0 else Decimal("0")
        for offset, (comp, qtd_componente, contrato, preco_unitario, total) in enumerate(precos_componentes):
            valor_total_item = cls._decimal(total * fator, cls.MONEY)
            valor_unitario = cls._decimal(
                (valor_total_item / qtd_componente) if qtd_componente > 0 else Decimal("0"),
                cls.UNIT,
            )
            item = ItemVendaPDV.objects.create(
                venda_pdv=venda,
                produto=comp.produto,
                numero_item=numero_item + offset,
                tipo_venda="kit",
                quantidade=qtd_componente,
                unidade_medida=comp.produto.unidade_medida.sigla if comp.produto.unidade_medida_id else "UN",
                valor_unitario=valor_unitario,
                valor_unitario_tabela=preco_unitario,
                custo_unitario_snapshot=contrato["custo_atual"],
                preco_origem="kit",
                preco_origem_detalhe=f'Kit "{kit.nome}"',
                valor_total=valor_total_item,
            )
            if comp.produto.tipo_produto != Produto.TipoProduto.SERVICO:
                movimentacoes = cls._baixar_produto_pdv(
                    produto=comp.produto,
                    filial=filial,
                    quantidade=qtd_componente,
                    usuario=usuario,
                    venda=venda,
                    tipo_operacao=MovimentacaoEstoque.TipoOperacao.SAIDA,
                    forcar_estoque_negativo=forcar_estoque_negativo,
                )
                item.estoque_baixado = True
                item.movimentacoes_estoque_ids = [mov.pk for mov in movimentacoes]
                item.save(update_fields=["estoque_baixado", "movimentacoes_estoque_ids"])
            itens.append(item)
        return itens

    @classmethod
    def _criar_brindes_automaticos(
        cls,
        *,
        venda: VendaPDV,
        filial,
        usuario,
        produto_gatilho: Produto,
        quantidade_gatilho: Decimal,
        numero_item_inicial: int,
    ) -> list[ItemVendaPDV]:
        brindes = (
            BrindeProduto.objects.for_filial(filial)
            .filter(ativo=True, produto_gatilho=produto_gatilho, quantidade_gatilho__lte=quantidade_gatilho)
            .prefetch_related("itens__produto__unidade_medida")
        )
        itens = []
        proximo_numero = numero_item_inicial + 1
        for brinde in brindes:
            multiplicador = int(quantidade_gatilho // brinde.quantidade_gatilho) if brinde.quantidade_gatilho else 0
            if multiplicador <= 0:
                continue
            for comp in brinde.itens.all():
                qtd = cls._decimal(comp.quantidade * multiplicador, Decimal("0.001"))
                contrato = ProdutoVendavelService.validar_venda(
                    produto=comp.produto,
                    filial=filial,
                    quantidade=qtd,
                )
                item = ItemVendaPDV.objects.create(
                    venda_pdv=venda,
                    produto=comp.produto,
                    numero_item=proximo_numero,
                    tipo_venda="brinde",
                    quantidade=qtd,
                    unidade_medida=comp.produto.unidade_medida.sigla if comp.produto.unidade_medida_id else "UN",
                    valor_unitario=Decimal("0.0000"),
                    valor_unitario_tabela=comp.produto.preco_venda,
                    custo_unitario_snapshot=contrato["custo_atual"],
                    preco_origem="brinde",
                    preco_origem_detalhe=f'Brinde "{brinde.nome}" gerado por {produto_gatilho.descricao}.',
                    valor_total=Decimal("0.00"),
                )
                movimentacoes = cls._baixar_produto_pdv(
                    produto=comp.produto,
                    filial=filial,
                    quantidade=qtd,
                    usuario=usuario,
                    venda=venda,
                    tipo_operacao=MovimentacaoEstoque.TipoOperacao.BRINDE,
                )
                item.estoque_baixado = True
                item.movimentacoes_estoque_ids = [mov.pk for mov in movimentacoes]
                item.save(update_fields=["estoque_baixado", "movimentacoes_estoque_ids"])
                itens.append(item)
                proximo_numero += 1
        return itens

    @classmethod
    def _baixar_produto_pdv(
        cls,
        *,
        produto: Produto,
        filial,
        quantidade: Decimal,
        usuario,
        venda: VendaPDV,
        tipo_operacao: str,
        forcar_estoque_negativo: bool = True,
    ):
        return MovimentacaoService.registrar_saida_fefo(
            produto_id=produto.pk,
            filial_id=filial.pk,
            quantidade=quantidade,
            usuario_id=usuario.pk,
            tipo_operacao=tipo_operacao,
            documento_tipo=MovimentacaoEstoque.DocumentoTipo.NFCE,
            documento_id=venda.pk,
            documento_numero=str(venda.numero_venda),
            forcar_estoque_negativo=forcar_estoque_negativo,
        )

    @classmethod
    def _aplicar_desconto_kit(cls, subtotal: Decimal, tipo: str, valor: Decimal) -> Decimal:
        total = PrecoService.aplicar_regra_desconto(subtotal, tipo, valor or Decimal("0"))
        return cls._decimal(total, cls.MONEY)

    @staticmethod
    def _aplicar_credito_cliente(filial, cliente_id, credito_valor: Decimal):
        """Debita credito_valor dos créditos disponíveis do cliente (FIFO)."""
        from apps.financeiro.models.credito_cliente import CreditoCliente
        restante = credito_valor
        creditos = CreditoCliente.objects.filter(
            filial=filial,
            cliente_id=cliente_id,
            status=CreditoCliente.Status.DISPONIVEL,
        ).order_by('created_at')
        for credito in creditos:
            if restante <= 0:
                break
            saldo = credito.valor_saldo
            if saldo <= 0:
                continue
            debitar = min(saldo, restante)
            credito.valor_utilizado += debitar
            restante -= debitar
            if credito.valor_saldo <= 0:
                credito.status = CreditoCliente.Status.UTILIZADO
            credito.save(update_fields=['valor_utilizado', 'status', 'updated_at'])

    @classmethod
    def _registrar_pagamentos(
        cls,
        *,
        venda: VendaPDV,
        filial,
        pagamentos: list[dict],
        valor_total: Decimal,
        credito_valor: Decimal = Decimal("0"),
    ) -> tuple[Decimal, Decimal]:
        valor_pago = credito_valor  # crédito conta como pré-pago
        troco_total = Decimal("0.00")
        for pgto in pagamentos:
            forma_id = int(pgto["forma_id"])
            valor_pgto = cls._decimal(pgto.get("valor", "0"), cls.MONEY)
            if valor_pgto <= 0:
                raise DadosInvalidosError("Valor do pagamento deve ser positivo.")
            try:
                forma = FormaPagamento.objects.get(
                    pk=forma_id,
                    empresa=filial.empresa,
                    ativo=True,
                )
            except FormaPagamento.DoesNotExist:
                raise DadosInvalidosError("Forma de pagamento nao encontrada.")

            troco = max(Decimal("0.00"), valor_pgto - (valor_total - valor_pago))
            PagamentoVendaPDV.objects.create(
                venda_pdv=venda,
                forma_pagamento=forma,
                valor=valor_pgto,
                troco=troco,
            )

            # Boleto e Vale sao recebimentos a prazo: geram conta a receber.
            if (forma.tipo or "").strip().lower() in ("boleto", "vale"):
                prazo_dias = pgto.get("prazo_dias")
                cls._gerar_conta_receber(
                    venda=venda,
                    filial=filial,
                    forma=forma,
                    valor=valor_pgto - troco,
                    prazo_dias=int(prazo_dias) if prazo_dias not in (None, "") else None,
                )

            valor_pago += valor_pgto
            troco_total += troco

        if valor_pago < valor_total:
            raise DadosInvalidosError("Valor pago menor que o total da venda.")
        return valor_pago, troco_total

    @classmethod
    def _gerar_conta_receber(cls, *, venda: VendaPDV, filial, forma, valor: Decimal, prazo_dias: int | None = None) -> None:
        """Cria uma conta a receber em aberto para pagamentos a prazo (boleto/vale)."""
        valor = cls._decimal(valor, cls.MONEY)
        if valor <= 0:
            return
        if not venda.cliente_id:
            raise DadosInvalidosError(
                f"Vendas no {forma.get_tipo_display()} exigem um cliente selecionado "
                "para gerar a conta a receber. Selecione o cliente antes de finalizar."
            )

        dias = prazo_dias if prazo_dias is not None else int(forma.prazo_liquidacao_dias or 0)
        if dias < 0:
            raise DadosInvalidosError("O prazo informado nao pode ser negativo.")

        # Emissão baseada na data da venda (respeita lançamento retroativo).
        hoje = timezone.localtime(venda.data_venda).date() if venda.data_venda else timezone.localdate()
        vencimento = hoje + timedelta(days=dias)
        ContaReceber.objects.create(
            filial=filial,
            cliente_id=venda.cliente_id,
            documento_tipo="venda_pdv",
            documento_id=venda.pk,
            documento_numero=str(venda.numero_venda),
            valor_original=valor,
            valor_final=valor,
            valor_saldo=valor,
            data_emissao=hoje,
            data_vencimento=vencimento,
            forma_pagamento=forma,
            status=StatusContaReceber.ABERTO,
            observacao=f"Gerada automaticamente da venda #{venda.numero_venda} ({forma.descricao}).",
            usuario=venda.usuario,
        )

    @staticmethod
    def _proximo_numero_venda(filial) -> int:
        ultimo_num = (
            VendaPDV.objects.filter(filial=filial)
            .order_by("-numero_venda")
            .values_list("numero_venda", flat=True)
            .first()
        )
        return (ultimo_num or 0) + 1

    @staticmethod
    def _decimal(valor, quantizador: Decimal) -> Decimal:
        return Decimal(str(valor or "0")).quantize(quantizador, rounding=ROUND_HALF_UP)
