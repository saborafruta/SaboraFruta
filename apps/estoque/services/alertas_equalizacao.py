"""
Alertas automáticos de equalização (dentro do ERP, via sino de notificações).

DETECTADOS, NÃO GRAVADOS -- mesmo espírito do `apps.moda.services.alertas`:
a lista é recalculada a cada sincronização a partir do estado atual
(`posicoes_estoque.calcular_posicoes`), nunca de um registro que
envelhece. `sincronizar` cria o que apareceu e DESLIGA o que deixou de
valer (produto que saiu da ruptura, lote que já foi transferido) --
um alerta que só acumula vira ruído que ninguém mais olha.

Usa o tipo genérico `Notificacao.Tipo.ALERTA_SISTEMA` (em vez de criar 6
tipos novos no model compartilhado `core.Notificacao`) -- a distinção
visual pedida (🔴🟠🟡🔵⚫🟣) já fica no título, e `referencia_tipo` isola
esses alertas de qualquer outro alerta do sistema na hora de desligar
os obsoletos.

Preparado para expansão futura de canal (WhatsApp/e-mail/push): quem
quiser adicionar um canal novo escuta o mesmo `detectar()` e decide
como entregar -- a notificação in-app não precisa mudar.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.urls import reverse

from apps.core.models import Filial, Notificacao
from apps.estoque.services.posicoes_estoque import calcular_posicoes

REFERENCIA_TIPO = "equalizacao_alerta"

ZERO = Decimal("0")


@dataclass(frozen=True)
class AlertaEqualizacao:
    referencia: str
    titulo: str
    mensagem: str
    filial_id: int


def detectar(*, empresa, dias_analise: int = 30, dias_cobertura: int = 14) -> list[AlertaEqualizacao]:
    posicoes = calcular_posicoes(empresa=empresa, dias_analise=dias_analise, dias_cobertura=dias_cobertura)
    alertas: list[AlertaEqualizacao] = []
    for item in posicoes:
        produto = item["produto"]
        filial = item["filial"]
        chave = f"{produto.pk}:{filial.pk}"
        nome_filial = filial.nome_fantasia or filial.razao_social

        if item["classe"] == "ruptura":
            alertas.append(AlertaEqualizacao(
                referencia=f"ruptura:{chave}",
                titulo=f"🔴 Ruptura: {produto.descricao} ({nome_filial})",
                mensagem=f"{produto.descricao} está com estoque zerado em {nome_filial}.",
                filial_id=filial.pk,
            ))
        elif item["classe"] == "critico":
            alertas.append(AlertaEqualizacao(
                referencia=f"risco_ruptura:{chave}",
                titulo=f"🟠 Risco de ruptura: {produto.descricao} ({nome_filial})",
                mensagem=(
                    f"{produto.descricao} tem apenas {item['cobertura']} dia(s) de cobertura em {nome_filial}."
                ),
                filial_id=filial.pk,
            ))
        elif item["deficit"] > ZERO:
            alertas.append(AlertaEqualizacao(
                referencia=f"abaixo_minimo:{chave}",
                titulo=f"🟡 Abaixo do mínimo: {produto.descricao} ({nome_filial})",
                mensagem=f"{produto.descricao} está abaixo da meta de estoque em {nome_filial}.",
                filial_id=filial.pk,
            ))

        if item["classe"] == "excesso":
            alertas.append(AlertaEqualizacao(
                referencia=f"excesso:{chave}",
                titulo=f"🔵 Excesso: {produto.descricao} ({nome_filial})",
                mensagem=f"{produto.descricao} está com excesso de estoque em {nome_filial}.",
                filial_id=filial.pk,
            ))

        if item["parado"] and item["saldo"] > ZERO:
            alertas.append(AlertaEqualizacao(
                referencia=f"parado:{chave}",
                titulo=f"⚫ Produto parado: {produto.descricao} ({nome_filial})",
                mensagem=f"{produto.descricao} não vendeu no período em {nome_filial}, com {item['saldo']} unidades em estoque.",
                filial_id=filial.pk,
            ))

        if item["lote_dias_vencer"] is not None and item["lote_dias_vencer"] <= (produto.dias_aviso_vencimento or 0):
            alertas.append(AlertaEqualizacao(
                referencia=f"vencimento:{chave}",
                titulo=f"🟣 Próximo do vencimento: {produto.descricao} ({nome_filial})",
                mensagem=f"{produto.descricao} tem lote vencendo em {item['lote_dias_vencer']} dia(s) em {nome_filial}.",
                filial_id=filial.pk,
            ))

    return alertas


def sincronizar(*, empresa, dias_analise: int = 30, dias_cobertura: int = 14) -> dict:
    alertas = detectar(empresa=empresa, dias_analise=dias_analise, dias_cobertura=dias_cobertura)
    url = reverse("estoque:dashboard-equalizacao")

    criados = atualizados = 0
    for alerta in alertas:
        _obj, novo = Notificacao.objects.update_or_create(
            filial_id=alerta.filial_id, tipo=Notificacao.Tipo.ALERTA_SISTEMA,
            referencia_tipo=REFERENCIA_TIPO, referencia_id=alerta.referencia,
            defaults={"titulo": alerta.titulo[:140], "mensagem": alerta.mensagem[:500], "url": url, "ativa": True},
        )
        criados += 1 if novo else 0
        atualizados += 0 if novo else 1

    # `referencia` já inclui produto+filial, então é globalmente única --
    # desliga tudo que estava ativo pra' esta empresa e não apareceu nesta
    # varredura, mesmo numa filial que não tem alerta nenhum agora (a
    # unica forma de nao perder essa filial no desligamento).
    filial_ids_empresa = list(Filial.objects.filter(empresa=empresa).values_list("pk", flat=True))
    vistos = {alerta.referencia for alerta in alertas}
    desligados = (
        Notificacao.objects.filter(filial_id__in=filial_ids_empresa, referencia_tipo=REFERENCIA_TIPO, ativa=True)
        .exclude(referencia_id__in=vistos)
        .update(ativa=False)
    )

    return {"detectados": len(alertas), "criados": criados, "atualizados": atualizados, "desligados": desligados}
