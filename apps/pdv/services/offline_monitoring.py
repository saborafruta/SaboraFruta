"""Alertas e auditoria do estado local do PDV, sem copiar conteúdo de vendas."""
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Notificacao, NotificacaoLeitura
from apps.pdv.models import EventoInstalacaoPDVOffline, InstalacaoPDVOffline


def _atualizar_notificacao(*, alias, filial, referencia_tipo, referencia_id, ativa, titulo, mensagem):
    queryset = Notificacao.objects.using(alias)
    existente = queryset.filter(
        filial_id=filial.pk,
        tipo=Notificacao.Tipo.ALERTA_SISTEMA,
        referencia_tipo=referencia_tipo,
        referencia_id=referencia_id,
    ).first()
    if not ativa:
        if existente and existente.ativa:
            queryset.filter(pk=existente.pk).update(ativa=False)
        return

    estava_ativa = bool(existente and existente.ativa)
    notificacao, _ = queryset.update_or_create(
        filial_id=filial.pk,
        tipo=Notificacao.Tipo.ALERTA_SISTEMA,
        referencia_tipo=referencia_tipo,
        referencia_id=referencia_id,
        defaults={
            "titulo": titulo[:140],
            "mensagem": mensagem[:500],
            "url": reverse("pdv:home"),
            "ativa": True,
        },
    )
    if not estava_ativa:
        NotificacaoLeitura.objects.using(alias).filter(notificacao_id=notificacao.pk).delete()


def atualizar_alertas_pdv(*, alias, filial, instalacao):
    """Mantém alertas condicionais da fila e do catálogo no tenant da filial."""
    nome = instalacao.nome_dispositivo or "PDV"
    pendentes = instalacao.fila_pendente_quantidade
    erros = instalacao.fila_erro_quantidade
    detalhe_erro = instalacao.ultimo_erro_sincronizacao.strip()
    _atualizar_notificacao(
        alias=alias,
        filial=filial,
        referencia_tipo="pdv_offline_fila",
        referencia_id=instalacao.installation_id,
        ativa=pendentes > 0,
        titulo=(f"PDV com {erros} venda(s) em erro" if erros else f"PDV com {pendentes} venda(s) pendente(s)"),
        mensagem=(
            f"{nome}: {pendentes} venda(s) aguardando sincronização."
            + (f" Último erro: {detalhe_erro}" if detalhe_erro else "")
        ),
    )

    catalogo_vencido = (
        instalacao.status == InstalacaoPDVOffline.Status.ATIVA
        and (
            not instalacao.catalogo_atualizado_em
            or instalacao.catalogo_atualizado_em < timezone.now() - timezone.timedelta(hours=12)
        )
    )
    _atualizar_notificacao(
        alias=alias,
        filial=filial,
        referencia_tipo="pdv_offline_catalogo",
        referencia_id=instalacao.installation_id,
        ativa=catalogo_vencido,
        titulo="Catálogo offline do PDV está vencido",
        mensagem=f"{nome}: conecte o caixa para renovar produtos, preços e regras antes de operar offline.",
    )


def registrar_auditoria_fila(*, instalacao, fila_anterior, fila_atual, backup_anterior=None):
    """Registra transições por local_id; o payload comercial nunca é persistido."""
    anteriores = {item.get("local_id"): item for item in fila_anterior or [] if item.get("local_id")}
    atuais = {item.get("local_id"): item for item in fila_atual or [] if item.get("local_id")}
    eventos = []

    for local_id, item in atuais.items():
        anterior = anteriores.get(local_id)
        metadados = {
            "local_id": local_id,
            "status": item.get("status", "pendente"),
            "tentativas": item.get("attempts", 0),
            "criado_em_local": item.get("created_at"),
        }
        if anterior is None:
            eventos.append(EventoInstalacaoPDVOffline(
                instalacao=instalacao,
                tipo="venda_enfileirada",
                detalhe="Venda local detectada na fila offline.",
                metadados=metadados,
            ))
            continue
        if item.get("attempts", 0) > anterior.get("attempts", 0):
            eventos.append(EventoInstalacaoPDVOffline(
                instalacao=instalacao,
                tipo="tentativa_sincronizacao",
                detalhe=item.get("last_error") or "Nova tentativa de sincronização registrada.",
                metadados=metadados,
            ))
        if item.get("status") == "erro" and (
            anterior.get("status") != "erro" or anterior.get("last_error") != item.get("last_error")
        ):
            eventos.append(EventoInstalacaoPDVOffline(
                instalacao=instalacao,
                tipo="erro_sincronizacao",
                detalhe=item.get("last_error") or "Servidor recusou a venda local.",
                metadados=metadados,
            ))

    for local_id, item in anteriores.items():
        if local_id not in atuais:
            eventos.append(EventoInstalacaoPDVOffline(
                instalacao=instalacao,
                tipo="venda_reconciliada",
                detalhe="Venda removida da fila após confirmação idempotente do servidor.",
                metadados={
                    "local_id": local_id,
                    "tentativas": item.get("attempts", 0),
                    "status_anterior": item.get("status", "pendente"),
                },
            ))

    if instalacao.ultimo_backup_em and instalacao.ultimo_backup_em != backup_anterior:
        eventos.append(EventoInstalacaoPDVOffline(
            instalacao=instalacao,
            tipo="backup_exportado",
            detalhe="Backup emergencial local exportado.",
            metadados={"exportado_em": instalacao.ultimo_backup_em.isoformat()},
        ))

    if eventos:
        EventoInstalacaoPDVOffline.objects.using("default").bulk_create(eventos)
