"""
Aprovação de transferência por alçada de valor (Fase 20/30).

Uma transferência dentro da alçada do perfil de quem pede executa na
hora, exatamente como sempre executou (`MovimentacaoService.transferir_entre_filiais`
continua sendo chamado direto -- nada muda pra quem já está dentro do
limite). Acima da alçada, fica como `SolicitacaoTransferencia` PENDENTE
até alguém com alçada suficiente aprovar ou rejeitar; só a aprovação
executa a movimentação real.
"""
from __future__ import annotations

from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from apps.core.services.exceptions import DadosInvalidosError, DomainError
from apps.core.tenant_context import tenant_atomic
from apps.estoque.models import Estoque, SolicitacaoTransferencia
from apps.estoque.services.conferencia_transferencia import criar_conferencia_transferencia
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.estoque.services.posicoes_estoque import custo_unitario


def valor_estimado_transferencia(*, produto, quantidade: Decimal) -> Decimal:
    return (quantidade * custo_unitario(produto)).quantize(Decimal("0.01"))


def _perfil(usuario):
    return getattr(usuario, "_perfil_ativo", None) or usuario.perfil


def usuario_pode_executar_direto(usuario, valor: Decimal) -> bool:
    """
    Admin (ou superuser) sempre executa direto -- é quem administra as
    próprias alçadas, não faria sentido travar essa pessoa. Perfil sem
    alçada configurada (`alcada_transferencia is None`) também executa
    direto: introduzir esta feature não pode travar, de surpresa, todo
    mundo que já usava transferência sem nenhuma alçada definida.
    """
    perfil = _perfil(usuario)
    if usuario.is_superuser or perfil.is_admin:
        return True
    if perfil.alcada_transferencia is None:
        return True
    return valor <= perfil.alcada_transferencia


def solicitar_transferencia(*, produto, filial_origem, filial_destino, quantidade: Decimal, motivo: str, solicitante) -> SolicitacaoTransferencia:
    if filial_origem.pk == filial_destino.pk:
        raise DadosInvalidosError("Escolha uma filial de destino diferente da origem.")
    if quantidade <= 0:
        raise DadosInvalidosError("Quantidade deve ser maior que zero.")
    valor = valor_estimado_transferencia(produto=produto, quantidade=quantidade)
    return SolicitacaoTransferencia.objects.create(
        produto=produto, filial_origem=filial_origem, filial_destino=filial_destino,
        quantidade=quantidade, valor_estimado=valor, motivo=motivo.strip(), solicitante=solicitante,
    )


@tenant_atomic
def aprovar_solicitacao(*, solicitacao_id, aprovador, observacao: str = "") -> SolicitacaoTransferencia:
    solicitacao = SolicitacaoTransferencia.objects.select_for_update().select_related(
        "produto", "filial_origem", "filial_destino", "solicitante",
    ).get(pk=solicitacao_id)
    if solicitacao.status != SolicitacaoTransferencia.Status.PENDENTE:
        raise DadosInvalidosError("Esta solicitação já foi decidida.")
    # Segregação de funções (regra 30): quem pede não pode ser quem aprova.
    if aprovador.pk == solicitacao.solicitante_id:
        raise DadosInvalidosError("Quem solicitou a transferência não pode aprová-la.")
    if not usuario_pode_executar_direto(aprovador, solicitacao.valor_estimado):
        raise DadosInvalidosError("Sua alçada não é suficiente para aprovar este valor.")

    saldo_disponivel = Estoque.objects.filter(
        produto=solicitacao.produto, filial=solicitacao.filial_origem,
    ).aggregate(total=Sum("quantidade_disponivel"))["total"] or Decimal("0")
    if solicitacao.quantidade > saldo_disponivel:
        raise DadosInvalidosError(
            f"Estoque disponível na origem ({saldo_disponivel}) é menor que a quantidade solicitada."
        )

    # Regra 30: nunca ignorar lote quando o produto exige rastreabilidade.
    # Resolve o lote pelo critério FEFO automaticamente -- quem aprova não
    # escolhe lote manualmente aqui, mas a transferência real continua
    # rastreável como qualquer outra.
    lote_id = None
    if solicitacao.produto.controla_lote:
        from apps.lotes.services.fefo_service import proximo_lote_fefo

        lote = proximo_lote_fefo(solicitacao.produto_id, solicitacao.filial_origem_id)
        if not lote:
            raise DadosInvalidosError(
                "Produto exige lote e não há lote ativo disponível na origem. "
                "Use o formulário completo de transferência para escolher os lotes."
            )
        lote_id = lote.pk

    documento_numero = f"APR-{solicitacao.pk}"[:20]
    try:
        MovimentacaoService.transferir_entre_filiais(
            produto_id=solicitacao.produto_id, filial_origem_id=solicitacao.filial_origem_id,
            filial_destino_id=solicitacao.filial_destino_id, quantidade=solicitacao.quantidade,
            usuario_id=aprovador.pk, documento_numero=documento_numero, lote_id=lote_id,
            observacao=f"Transferência aprovada por {aprovador.nome}: {observacao}".strip(),
            permitir_sem_lote=not solicitacao.produto.controla_lote,
        )
    except DomainError as exc:
        if lote_id:
            raise DadosInvalidosError(
                f"{exc} O lote sugerido pelo FEFO não cobre a quantidade toda -- "
                "use o formulário completo de transferência para dividir entre lotes."
            )
        raise
    criar_conferencia_transferencia(
        documento_numero=documento_numero, filial_origem=solicitacao.filial_origem,
        filial_destino=solicitacao.filial_destino, usuario=aprovador,
        observacao=f"Gerada a partir da solicitação #{solicitacao.pk}",
    )

    solicitacao.status = SolicitacaoTransferencia.Status.APROVADA
    solicitacao.aprovador = aprovador
    solicitacao.decidida_em = timezone.now()
    solicitacao.observacao_decisao = observacao
    solicitacao.documento_numero = documento_numero
    solicitacao.save()
    return solicitacao


def rejeitar_solicitacao(*, solicitacao_id, aprovador, observacao: str) -> SolicitacaoTransferencia:
    solicitacao = SolicitacaoTransferencia.objects.select_related("solicitante").get(pk=solicitacao_id)
    if solicitacao.status != SolicitacaoTransferencia.Status.PENDENTE:
        raise DadosInvalidosError("Esta solicitação já foi decidida.")
    if aprovador.pk == solicitacao.solicitante_id:
        raise DadosInvalidosError("Quem solicitou a transferência não pode decidir sobre ela.")
    if not observacao.strip():
        raise DadosInvalidosError("Explique o motivo da rejeição.")

    solicitacao.status = SolicitacaoTransferencia.Status.REJEITADA
    solicitacao.aprovador = aprovador
    solicitacao.decidida_em = timezone.now()
    solicitacao.observacao_decisao = observacao.strip()
    solicitacao.save()
    return solicitacao
