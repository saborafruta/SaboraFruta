from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from django.utils import timezone

from apps.estoque.models import Estoque, LoteProduto
from apps.produtos.models import BrindeProduto, KitProduto, Produto, PromocaoQuantidade
from apps.produtos.services.preco_service import PrecoService
from apps.produtos.services.prontidao_comercial_service import avaliar_produto_para_venda, custo_referencia


class ProdutoVendavelService:
    """Contrato unico de consulta de produto vendavel para vendas e PDV."""

    MONEY = Decimal("0.01")
    UNIT = Decimal("0.0001")

    @classmethod
    def consultar(
        cls,
        *,
        produto: Produto,
        filial,
        quantidade=Decimal("1"),
        validar_promocoes: bool = True,
        incluir_promocoes_aplicaveis: bool = True,
        cliente=None,
        tabela_preco=None,
    ) -> dict:
        quantidade = cls._decimal(quantidade, Decimal("0.001"))
        estoque = Estoque.objects.filter(produto=produto, filial=filial).first()
        saldo_disponivel = estoque.quantidade_disponivel if estoque else Decimal("0")
        custo_atual = cls._decimal(custo_referencia(produto), cls.UNIT)
        preco_info = PrecoService.preco_cliente_detalhado(
            produto,
            quantidade,
            cliente=cliente,
            tabela=tabela_preco,
            filial=filial,
            validar_promocoes=validar_promocoes,
        )
        preco_aplicado = cls._decimal(preco_info.get("preco"), cls.UNIT)
        margem_percentual = cls._margem(preco_aplicado, custo_atual)
        avaliacao = avaliar_produto_para_venda(produto, filial=filial)
        bloqueios = [
            item for item in avaliacao["pendencias"]
            if item.get("severidade") == "bloqueio" and item.get("codigo") != "promocao_margem_negativa"
        ]
        alertas = [
            item for item in avaliacao["pendencias"]
            if item.get("severidade") != "bloqueio"
        ]
        alertas.extend(
            dict(item, severidade="aviso") for item in avaliacao["pendencias"]
            if item.get("codigo") == "promocao_margem_negativa"
        )
        if preco_aplicado <= 0:
            bloqueios.append({
                "codigo": "preco_aplicado_invalido",
                "label": "Preco aplicado invalido para venda.",
                "status": avaliacao["status"],
                "severidade": "bloqueio",
            })
        if custo_atual <= 0 and produto.tipo_produto != Produto.TipoProduto.SERVICO:
            alertas.append({
                "codigo": "custo_atual_invalido",
                "label": "Custo nao informado — margem e CMV nao serao calculados.",
                "status": avaliacao["status"],
                "severidade": "aviso",
            })
        if custo_atual > 0 and preco_aplicado < custo_atual:
            alertas.append({
                "codigo": "margem_negativa",
                "label": "Preco aplicado abaixo do custo atual.",
                "status": avaliacao["status"],
                "severidade": "aviso",
            })

        lote_obrigatorio = bool(produto.controla_lote or produto.controla_validade)
        return {
            "produto_id": produto.pk,
            "descricao": produto.descricao_pdv or produto.descricao,
            "saldo_disponivel": saldo_disponivel,
            "custo_atual": custo_atual,
            "preco_base": produto.preco_venda or Decimal("0"),
            "preco_aplicado": preco_aplicado,
            "preco_origem": preco_info.get("origem", "Preco de venda"),
            "preco_origem_tipo": preco_info.get("tipo", "normal"),
            "preco_origem_detalhe": preco_info.get("detalhe", ""),
            "margem_percentual": margem_percentual,
            "status_comercial": avaliacao["status"],
            "status_comercial_label": avaliacao["label"],
            "lote_obrigatorio": lote_obrigatorio,
            "tem_lote_disponivel": cls._tem_lote_disponivel(produto, filial) if lote_obrigatorio else True,
            "promocoes_aplicaveis": (
                cls.promocoes_aplicaveis(produto, filial, quantidade)
                if incluir_promocoes_aplicaveis else []
            ),
            "bloqueios": bloqueios,
            "alertas": alertas,
            "pode_vender": not bloqueios,
            "permite_venda_sem_estoque": produto.permite_venda_sem_estoque,
            "controla_lote": produto.controla_lote,
            "controla_validade": produto.controla_validade,
        }

    @classmethod
    def validar_venda(
        cls,
        *,
        produto: Produto,
        filial,
        quantidade=Decimal("1"),
        cliente=None,
        tabela_preco=None,
    ) -> dict:
        contrato = cls.consultar(
            produto=produto,
            filial=filial,
            quantidade=quantidade,
            cliente=cliente,
            tabela_preco=tabela_preco,
        )
        if contrato["bloqueios"]:
            labels = "; ".join(item["label"] for item in contrato["bloqueios"])
            from apps.core.services.exceptions import DadosInvalidosError
            raise DadosInvalidosError(f'Produto "{produto.descricao}" nao pode ser vendido: {labels}')
        return contrato

    @classmethod
    def promocoes_aplicaveis(cls, produto: Produto, filial, quantidade) -> list[dict]:
        promocoes = []
        detalhado = PrecoService.melhor_preco_produto_detalhado(
            produto,
            filial=filial,
            quantidade=quantidade,
            data=timezone.localdate(),
        )
        if detalhado.get("tipo") != "normal":
            promocoes.append({
                "tipo": detalhado.get("tipo"),
                "nome": detalhado.get("origem"),
                "preco": cls._decimal(detalhado.get("preco"), cls.UNIT),
                "detalhe": detalhado.get("detalhe", ""),
            })
        for promo in PromocaoQuantidade.objects.for_filial(filial).filter(produto=produto, ativo=True):
            if PrecoService.combo_quantidade_vigente(promo):
                promocoes.append({"tipo": "combo", "id": promo.pk, "nome": promo.nome})
        for kit in KitProduto.objects.for_filial(filial).filter(ativo=True, itens__produto=produto).distinct():
            promocoes.append({"tipo": "kit", "id": kit.pk, "nome": kit.nome})
        for brinde in BrindeProduto.objects.for_filial(filial).filter(
            ativo=True,
            produto_gatilho=produto,
            quantidade_gatilho__lte=quantidade,
        ):
            promocoes.append({"tipo": "brinde", "id": brinde.pk, "nome": brinde.nome})
        return promocoes

    @staticmethod
    def _tem_lote_disponivel(produto: Produto, filial) -> bool:
        hoje = timezone.localdate()
        return LoteProduto.objects.filter(
            produto=produto,
            filial=filial,
            status=LoteProduto.Status.ATIVO,
            quantidade_atual__gt=0,
        ).filter(data_validade__isnull=True).exists() or LoteProduto.objects.filter(
            produto=produto,
            filial=filial,
            status=LoteProduto.Status.ATIVO,
            quantidade_atual__gt=0,
            data_validade__gte=hoje,
        ).exists()

    @staticmethod
    def _margem(preco: Decimal, custo: Decimal) -> Decimal:
        if preco <= 0:
            return Decimal("0.00")
        return ((preco - custo) / preco * Decimal("100")).quantize(Decimal("0.01"))

    @staticmethod
    def _decimal(valor, quantizador: Decimal) -> Decimal:
        return Decimal(str(valor or "0")).quantize(quantizador, rounding=ROUND_HALF_UP)
