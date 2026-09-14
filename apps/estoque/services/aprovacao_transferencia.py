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

from apps.core.services.auditoria import registrar_auditoria, snapshot_modelo
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


def _executar_transferencia_simples(
    *, produto, filial_origem, filial_destino, quantidade: Decimal, usuario, documento_numero: str, observacao: str,
):
    """
    Movimento interno simples (sem NF-e/MDF-e) -- usado tanto pela
    aprovação de uma solicitação represada quanto pela criação direta
    quando o valor já está dentro da alçada de quem pede. Quem precisa
    de nota fiscal na transferência usa o formulário completo do ERP;
    esta função é deliberadamente só o movimento + a conferência.
    """
    lote_id = None
    if produto.controla_lote:
        from apps.lotes.services.fefo_service import proximo_lote_fefo

        lote = proximo_lote_fefo(produto.pk, filial_origem.pk)
        if not lote:
            raise DadosInvalidosError(
                "Produto exige lote e não há lote ativo disponível na origem. "
                "Use o formulário completo de transferência para escolher os lotes."
            )
        lote_id = lote.pk

    try:
        MovimentacaoService.transferir_entre_filiais(
            produto_id=produto.pk, filial_origem_id=filial_origem.pk,
            filial_destino_id=filial_destino.pk, quantidade=quantidade,
            usuario_id=usuario.pk, documento_numero=documento_numero, lote_id=lote_id,
            observacao=observacao, permitir_sem_lote=not produto.controla_lote,
        )
    except DomainError as exc:
        if lote_id:
            raise DadosInvalidosError(
                f"{exc} O lote sugerido pelo FEFO não cobre a quantidade toda -- "
                "use o formulário completo de transferência para dividir entre lotes."
            )
        raise
    criar_conferencia_transferencia(
        documento_numero=documento_numero, filial_origem=filial_origem,
        filial_destino=filial_destino, usuario=usuario, observacao=observacao,
    )


def criar_ou_solicitar_transferencia(
    *, produto, filial_origem, filial_destino, quantidade: Decimal, motivo: str, usuario, request=None,
) -> dict:
    """
    Mesma checagem de alçada usada pelo gate HTML (`TransferenciaGateView`)
    e pela API: dentro da alçada de quem pede, executa a transferência
    simples na hora; acima, vira solicitação pendente. Devolve
    {"executada": bool, "solicitacao": ... } pra quem chama decidir o
    que mostrar.
    """
    if filial_origem.pk == filial_destino.pk:
        raise DadosInvalidosError("Escolha uma filial de destino diferente da origem.")
    if quantidade <= 0:
        raise DadosInvalidosError("Quantidade deve ser maior que zero.")

    valor = valor_estimado_transferencia(produto=produto, quantidade=quantidade)
    if usuario_pode_executar_direto(usuario, valor):
        documento_numero = f"EQZ-{timezone.now():%Y%m%d%H%M%S}-{produto.pk}"[:20]
        observacao = motivo.strip() or "Transferência criada via equalização de estoque."
        _executar_transferencia_simples(
            produto=produto, filial_origem=filial_origem, filial_destino=filial_destino,
            quantidade=quantidade, usuario=usuario, documento_numero=documento_numero, observacao=observacao,
        )
        registrar_auditoria(
            request=request, usuario=usuario, filial=filial_origem,
            modulo="estoque", acao="transferir", objeto=produto,
            descricao=f"Transferência {documento_numero}: {produto.descricao} ({filial_origem} → {filial_destino})",
            justificativa=motivo,
            metadados={
                "evento": "transferencia_direta_equalizacao", "documento_numero": documento_numero,
                "quantidade": str(quantidade), "valor_estimado": str(valor),
                "filial_destino": destino_nome(filial_destino),
            },
        )
        return {"executada": True, "documento_numero": documento_numero, "solicitacao": None}

    solicitacao = solicitar_transferencia(
        produto=produto, filial_origem=filial_origem, filial_destino=filial_destino,
        quantidade=quantidade, motivo=motivo, solicitante=usuario, request=request,
    )
    return {"executada": False, "documento_numero": "", "solicitacao": solicitacao}


def destino_nome(filial) -> str:
    return filial.nome_fantasia or filial.razao_social


def solicitar_transferencia(*, produto, filial_origem, filial_destino, quantidade: Decimal, motivo: str, solicitante, request=None) -> SolicitacaoTransferencia:
    if filial_origem.pk == filial_destino.pk:
        raise DadosInvalidosError("Escolha uma filial de destino diferente da origem.")
    if quantidade <= 0:
        raise DadosInvalidosError("Quantidade deve ser maior que zero.")
    valor = valor_estimado_transferencia(produto=produto, quantidade=quantidade)
    solicitacao = SolicitacaoTransferencia.objects.create(
        produto=produto, filial_origem=filial_origem, filial_destino=filial_destino,
        quantidade=quantidade, valor_estimado=valor, motivo=motivo.strip(), solicitante=solicitante,
    )
    registrar_auditoria(
        request=request, usuario=solicitante, filial=filial_origem,
        modulo="estoque", acao="criar", objeto=solicitacao,
        descricao=f"Solicitação de transferência #{solicitacao.pk}: {produto.descricao} ({filial_origem} → {filial_destino})",
        justificativa=motivo,
        depois=snapshot_modelo(solicitacao),
        metadados={
            "evento": "solicitacao_transferencia_criada", "quantidade": str(quantidade), "valor_estimado": str(valor),
        },
    )
    return solicitacao


@tenant_atomic
def aprovar_solicitacao(*, solicitacao_id, aprovador, observacao: str = "", request=None) -> SolicitacaoTransferencia:
    solicitacao = SolicitacaoTransferencia.objects.select_for_update().select_related(
        "produto", "filial_origem", "filial_destino", "solicitante",
    ).get(pk=solicitacao_id)
    antes = snapshot_modelo(solicitacao)
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

    documento_numero = f"APR-{solicitacao.pk}"[:20]
    _executar_transferencia_simples(
        produto=solicitacao.produto, filial_origem=solicitacao.filial_origem,
        filial_destino=solicitacao.filial_destino, quantidade=solicitacao.quantidade,
        usuario=aprovador, documento_numero=documento_numero,
        observacao=f"Transferência aprovada por {aprovador.nome}: {observacao}".strip(),
    )

    solicitacao.status = SolicitacaoTransferencia.Status.APROVADA
    solicitacao.aprovador = aprovador
    solicitacao.decidida_em = timezone.now()
    solicitacao.observacao_decisao = observacao
    solicitacao.documento_numero = documento_numero
    solicitacao.save()
    registrar_auditoria(
        request=request, usuario=aprovador, filial=solicitacao.filial_origem,
        modulo="estoque", acao="aprovar", objeto=solicitacao,
        descricao=f"Solicitação de transferência #{solicitacao.pk} aprovada -- transferência {documento_numero}",
        justificativa=observacao,
        antes=antes, depois=snapshot_modelo(solicitacao),
        metadados={"evento": "solicitacao_transferencia_aprovada", "documento_numero": documento_numero},
    )
    return solicitacao


def rejeitar_solicitacao(*, solicitacao_id, aprovador, observacao: str, request=None) -> SolicitacaoTransferencia:
    solicitacao = SolicitacaoTransferencia.objects.select_related("solicitante").get(pk=solicitacao_id)
    if solicitacao.status != SolicitacaoTransferencia.Status.PENDENTE:
        raise DadosInvalidosError("Esta solicitação já foi decidida.")
    if aprovador.pk == solicitacao.solicitante_id:
        raise DadosInvalidosError("Quem solicitou a transferência não pode decidir sobre ela.")
    if not observacao.strip():
        raise DadosInvalidosError("Explique o motivo da rejeição.")

    antes = snapshot_modelo(solicitacao)
    solicitacao.status = SolicitacaoTransferencia.Status.REJEITADA
    solicitacao.aprovador = aprovador
    solicitacao.decidida_em = timezone.now()
    solicitacao.observacao_decisao = observacao.strip()
    solicitacao.save()
    registrar_auditoria(
        request=request, usuario=aprovador, filial=solicitacao.filial_origem,
        modulo="estoque", acao="cancelar", objeto=solicitacao,
        descricao=f"Solicitação de transferência #{solicitacao.pk} rejeitada",
        justificativa=observacao,
        antes=antes, depois=snapshot_modelo(solicitacao),
        metadados={"evento": "solicitacao_transferencia_rejeitada"},
    )
    return solicitacao
