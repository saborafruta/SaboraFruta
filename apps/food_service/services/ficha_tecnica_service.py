from __future__ import annotations

from decimal import Decimal

from apps.estoque.models import MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.producao.models import FichaTecnica


class FichaTecnicaService:
    """
    Ponte entre a Ficha Técnica que já existe em `apps.producao` (BOM usado
    pelas ordens de produção) e a venda do Food Service.

    Quando um prato com ficha ativa é vendido, consome os INGREDIENTES do
    estoque pelo mesmo mecanismo que uma ordem de produção usa
    (`MovimentacaoService.registrar_saida_fefo`) -- em vez de dar baixa no
    prato em si. Para isso funcionar corretamente, o prato deve estar
    cadastrado com `tipo_produto=SERVICO`: `VendaPDVService` já pula a baixa
    de estoque do próprio item nesse caso (pratos preparados não têm saldo
    de prateleira, só os ingredientes têm).

    Prato sem ficha técnica cadastrada = sem efeito aqui (continua sendo
    vendido normalmente, sem consumo automático de ingrediente).
    """

    @staticmethod
    def ficha_ativa(produto):
        return (
            FichaTecnica.objects
            .filter(produto_acabado=produto, status=FichaTecnica.Status.ATIVA)
            .order_by('-versao')
            .prefetch_related('itens__materia_prima')
            .first()
        )

    @classmethod
    def consumir_ingredientes(
        cls, *, produto, quantidade_vendida, filial, usuario, documento_id, documento_numero,
    ) -> list[MovimentacaoEstoque]:
        ficha = cls.ficha_ativa(produto)
        if not ficha:
            return []

        proporcao = Decimal(str(quantidade_vendida)) / ficha.quantidade_produzida
        movimentacoes = []
        for item in ficha.itens.select_related('materia_prima'):
            quantidade = item.quantidade_com_perda() * proporcao
            if quantidade <= 0:
                continue
            movimentacoes.extend(
                MovimentacaoService.registrar_saida_fefo(
                    produto_id=item.materia_prima_id,
                    filial_id=filial.pk,
                    quantidade=quantidade,
                    usuario_id=usuario.pk,
                    tipo_operacao=MovimentacaoEstoque.TipoOperacao.PRODUCAO_SAIDA,
                    documento_tipo=MovimentacaoEstoque.DocumentoTipo.COMANDA,
                    documento_id=documento_id,
                    documento_numero=documento_numero,
                    # Nunca bloquear o fechamento da comanda por falta de
                    # ingrediente -- mesmo comportamento que o resto do PDV
                    # já tem (venda com estoque negativo autorizada).
                    forcar_estoque_negativo=True,
                )
            )
        return movimentacoes

    @classmethod
    def custo_estimado(cls, produto, quantidade) -> Decimal | None:
        """CMV do prato pra essa quantidade, ou None se não há ficha técnica cadastrada."""
        ficha = cls.ficha_ativa(produto)
        if not ficha:
            return None
        proporcao = Decimal(str(quantidade)) / ficha.quantidade_produzida
        return ficha.custo_total_execucao() * proporcao
