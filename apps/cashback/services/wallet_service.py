"""
Carteira virtual de cashback — crédito, débito, estorno e expiração.

O ledger (`MovimentoCashback`) é sempre a fonte da verdade; os saldos em
`CarteiraCashback` são colunas de cache atualizadas na mesma transação
de cada lançamento. Toda operação que muda saldo roda dentro de
`transaction.atomic()` com `select_for_update()` na carteira, e
recalcula o saldo disponível por agregação do ledger antes de validar
qualquer débito — nunca confia só no valor em cache para decisões
críticas. Isso evita condição de corrida (duas requisições poderiam
debitar o mesmo saldo "ao mesmo tempo") e saldo negativo indevido.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.core.middleware.audit import get_client_ip
from apps.core.services.exceptions import DadosInvalidosError

from .regra_resolver import obter_configuracao_cashback, resolver_percentual


def _ip_e_dispositivo(request):
    if not request:
        return None, ""
    return get_client_ip(request), (request.META.get("HTTP_USER_AGENT", "") or "")[:255]


class CashbackWalletService:
    """Operações da carteira de cashback. Todos os métodos são atômicos."""

    # ------------------------------------------------------------ leitura
    @staticmethod
    def obter_ou_criar_carteira(empresa, cliente):
        from apps.cashback.models import CarteiraCashback

        carteira, _ = CarteiraCashback.objects.get_or_create(empresa=empresa, cliente=cliente)
        return carteira

    @staticmethod
    def _recalcular_saldo_disponivel(carteira) -> Decimal:
        """Soma tudo do ledger — fonte da verdade, ignora o cache."""
        from apps.cashback.models import MovimentoCashback

        agregados = MovimentoCashback.objects.filter(carteira=carteira).aggregate(
            creditos=Sum("valor", filter=Q(tipo__in=MovimentoCashback.TIPOS_CREDITO)),
            debitos=Sum("valor", filter=Q(tipo__in=MovimentoCashback.TIPOS_DEBITO)),
        )
        creditos = agregados["creditos"] or Decimal("0")
        debitos = agregados["debitos"] or Decimal("0")
        return creditos - debitos

    # ------------------------------------------------------------- crédito
    @classmethod
    @transaction.atomic
    def creditar(cls, *, venda, usuario=None, request=None) -> list:
        """
        Credita cashback pela venda finalizada, agrupando os itens por
        percentual resolvido. Regra §5: só roda se o cliente tiver
        CPF/CNPJ. Idempotente por venda (chave_idempotencia única) —
        chamadas repetidas para a mesma venda não duplicam crédito.
        """
        from apps.cashback.models import MovimentoCashback

        cliente = venda.cliente
        if not cliente or not (cliente.cpf_cnpj or "").strip():
            return []

        chave_base = f"venda:{venda.pk}:credito"
        if MovimentoCashback.objects.filter(chave_idempotencia=chave_base).exists():
            return []

        filial = venda.filial
        empresa = filial.empresa
        carteira = cls.obter_ou_criar_carteira(empresa, cliente)
        carteira = type(carteira).objects.select_for_update().get(pk=carteira.pk)

        config = obter_configuracao_cashback(filial)
        valor_minimo_padrao = config.valor_minimo_gerar if config else Decimal("0")
        dias_validade = config.dias_validade if config else 90

        itens = list(venda.itens.select_related("produto", "produto__categoria").all())
        if not itens:
            return []

        grupos: dict[tuple[Decimal, str], dict] = {}
        for item in itens:
            if not item.produto_id:
                continue
            resultado = resolver_percentual(produto=item.produto, filial=filial)
            if not resultado.gera_cashback:
                continue
            chave_grupo = (resultado.percentual, resultado.origem)
            grupo = grupos.setdefault(chave_grupo, {
                "subtotal": Decimal("0"),
                "valor_minimo": resultado.valor_minimo_gerar,
                "item_ref": item,
            })
            grupo["subtotal"] += item.valor_total

        if not grupos:
            return []

        ip_origem, dispositivo = _ip_e_dispositivo(request)
        hoje = timezone.localdate()
        movimentos = []

        for idx, ((percentual, origem), dados) in enumerate(grupos.items()):
            minimo = dados["valor_minimo"] if dados["valor_minimo"] is not None else valor_minimo_padrao
            if dados["subtotal"] < minimo:
                continue
            valor_cashback = (dados["subtotal"] * percentual / Decimal("100")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP,
            )
            if valor_cashback <= 0:
                continue
            mov = MovimentoCashback.objects.create(
                carteira=carteira,
                cliente=cliente,
                empresa=empresa,
                filial=filial,
                venda=venda,
                item_venda=dados["item_ref"],
                usuario=usuario,
                tipo=MovimentoCashback.Tipo.CREDITO_VENDA,
                valor=valor_cashback,
                data_validade=hoje + timedelta(days=dias_validade),
                observacao=f"Cashback {percentual}% ({origem}) sobre R$ {dados['subtotal']}.",
                origem=MovimentoCashback.Origem.PDV,
                ip_origem=ip_origem,
                dispositivo=dispositivo,
                chave_idempotencia=chave_base if idx == 0 else f"{chave_base}:{idx}",
            )
            movimentos.append(mov)
            carteira.saldo_total_gerado += valor_cashback
            carteira.saldo_disponivel += valor_cashback

        if movimentos:
            carteira.save(update_fields=["saldo_total_gerado", "saldo_disponivel", "updated_at"])
        return movimentos

    # -------------------------------------------------------------- débito
    @classmethod
    @transaction.atomic
    def debitar(cls, *, cliente, empresa, valor: Decimal, venda, usuario=None, request=None):
        """
        Debita `valor` da carteira do cliente para pagar (parte de) uma
        venda. Valida saldo disponível, valor mínimo de uso e limite
        máximo percentual da venda — sempre sob lock e saldo recalculado
        na hora, para não permitir corrida entre dois débitos simultâneos.
        """
        from apps.cashback.models import CarteiraCashback, MovimentoCashback

        if valor <= 0:
            raise DadosInvalidosError("Valor de cashback deve ser positivo.")

        try:
            carteira = CarteiraCashback.objects.select_for_update().get(empresa=empresa, cliente=cliente)
        except CarteiraCashback.DoesNotExist:
            raise DadosInvalidosError("Cliente não possui carteira de cashback.")

        cls.expirar_creditos(carteira=carteira)
        saldo_disponivel = cls._recalcular_saldo_disponivel(carteira)

        config = obter_configuracao_cashback(venda.filial) if venda else None
        if config and venda and venda.valor_total < config.valor_minimo_usar:
            raise DadosInvalidosError(
                f"O valor mínimo da compra para usar cashback é R$ {config.valor_minimo_usar}."
            )
        if config and venda:
            limite = (venda.valor_total * config.percentual_maximo_uso_venda / Decimal("100")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP,
            )
            if valor > limite:
                raise DadosInvalidosError(
                    f"O cashback pode cobrir no máximo {config.percentual_maximo_uso_venda}% "
                    f"da venda (R$ {limite})."
                )
        if valor > saldo_disponivel:
            raise DadosInvalidosError(f"Saldo de cashback insuficiente. Disponível: R$ {saldo_disponivel}.")

        ip_origem, dispositivo = _ip_e_dispositivo(request)
        mov = MovimentoCashback.objects.create(
            carteira=carteira,
            cliente=cliente,
            empresa=empresa,
            filial=venda.filial if venda else None,
            venda=venda,
            usuario=usuario,
            tipo=MovimentoCashback.Tipo.DEBITO_UTILIZACAO,
            valor=valor,
            observacao=(
                f"Uso de cashback na venda #{venda.numero_venda}." if venda else "Uso de cashback."
            ),
            origem=MovimentoCashback.Origem.PDV,
            ip_origem=ip_origem,
            dispositivo=dispositivo,
        )
        carteira.saldo_utilizado += valor
        carteira.saldo_disponivel = saldo_disponivel - valor
        carteira.save(update_fields=["saldo_utilizado", "saldo_disponivel", "updated_at"])
        return mov

    # ------------------------------------------------------- saldo/consulta
    @classmethod
    def saldo_disponivel(cls, *, empresa, cliente) -> Decimal:
        from apps.cashback.models import CarteiraCashback

        carteira = CarteiraCashback.objects.filter(empresa=empresa, cliente=cliente).first()
        if not carteira:
            return Decimal("0")
        with transaction.atomic():
            carteira = CarteiraCashback.objects.select_for_update().get(pk=carteira.pk)
            cls.expirar_creditos(carteira=carteira)
            return cls._recalcular_saldo_disponivel(carteira)

    # -------------------------------------------------------------- ajuste
    @classmethod
    @transaction.atomic
    def ajustar_manual(cls, *, empresa, cliente, valor: Decimal, usuario, observacao: str, request=None):
        """
        Ajuste manual (positivo) de cashback — ex.: cortesia, correção de
        atendimento. Para remover saldo manualmente, use `estornar_venda`
        ou registre um `CANCELAMENTO` diretamente via admin, com
        justificativa.
        """
        if valor <= 0:
            raise DadosInvalidosError("Ajuste manual deve ser um valor positivo.")

        from apps.cashback.models import MovimentoCashback

        carteira = cls.obter_ou_criar_carteira(empresa, cliente)
        carteira = type(carteira).objects.select_for_update().get(pk=carteira.pk)
        ip_origem, dispositivo = _ip_e_dispositivo(request)
        mov = MovimentoCashback.objects.create(
            carteira=carteira, cliente=cliente, empresa=empresa, usuario=usuario,
            tipo=MovimentoCashback.Tipo.AJUSTE_MANUAL, valor=valor, observacao=observacao,
            origem=MovimentoCashback.Origem.MANUAL,
            ip_origem=ip_origem, dispositivo=dispositivo,
        )
        carteira.saldo_total_gerado += valor
        carteira.saldo_disponivel += valor
        carteira.save(update_fields=["saldo_total_gerado", "saldo_disponivel", "updated_at"])
        return mov

    # ------------------------------------------------------------ estorno
    @classmethod
    @transaction.atomic
    def estornar_venda(cls, venda, usuario=None, motivo: str = "") -> None:
        """
        Reverte os efeitos de cashback de uma venda cancelada/editada.
        Idempotente: se já foi revertida antes, não faz nada de novo.

        (a) Se a venda gerou crédito: cancela o que ainda estiver
            disponível; se já tiver sido gasto/expirado pelo cliente,
            aplica o `modo_estorno_usado` da configuração (saldo negativo
            ou conta a receber).
        (b) Se a venda usou cashback como pagamento: devolve à carteira.
        """
        from apps.cashback.models import CarteiraCashback, ConfiguracaoCashback, MovimentoCashback

        creditos_da_venda = list(
            MovimentoCashback.objects.select_for_update()
            .filter(venda=venda, tipo=MovimentoCashback.Tipo.CREDITO_VENDA)
        )
        ja_cancelado = MovimentoCashback.objects.filter(
            venda=venda, tipo=MovimentoCashback.Tipo.CANCELAMENTO,
        ).exists()

        if creditos_da_venda and not ja_cancelado:
            total_creditado = sum((m.valor for m in creditos_da_venda), Decimal("0"))
            carteira = CarteiraCashback.objects.select_for_update().get(pk=creditos_da_venda[0].carteira_id)
            saldo_disponivel = cls._recalcular_saldo_disponivel(carteira)
            a_cancelar = min(total_creditado, max(saldo_disponivel, Decimal("0")))
            faltante = total_creditado - a_cancelar

            if a_cancelar > 0:
                MovimentoCashback.objects.create(
                    carteira=carteira, cliente=carteira.cliente, empresa=carteira.empresa,
                    venda=venda, usuario=usuario, tipo=MovimentoCashback.Tipo.CANCELAMENTO,
                    valor=a_cancelar,
                    observacao=motivo or f"Cancelamento do cashback gerado pela venda #{venda.numero_venda}.",
                    origem=MovimentoCashback.Origem.SISTEMA,
                )
                carteira.saldo_cancelado += a_cancelar
                carteira.saldo_disponivel = saldo_disponivel - a_cancelar
                carteira.save(update_fields=["saldo_cancelado", "saldo_disponivel", "updated_at"])

            if faltante > 0:
                config = obter_configuracao_cashback(venda.filial)
                modo = (
                    config.modo_estorno_usado if config
                    else ConfiguracaoCashback.ModoEstornoUsado.CONTA_A_RECEBER
                )
                if modo == ConfiguracaoCashback.ModoEstornoUsado.NEGATIVO:
                    saldo_atual = cls._recalcular_saldo_disponivel(carteira)
                    MovimentoCashback.objects.create(
                        carteira=carteira, cliente=carteira.cliente, empresa=carteira.empresa,
                        venda=venda, usuario=usuario, tipo=MovimentoCashback.Tipo.CANCELAMENTO,
                        valor=faltante,
                        observacao=(
                            (motivo + " " if motivo else "")
                            + f"Cashback já utilizado pelo cliente — saldo negativo de R$ {faltante}."
                        ),
                        origem=MovimentoCashback.Origem.SISTEMA,
                    )
                    carteira.saldo_cancelado += faltante
                    carteira.saldo_disponivel = saldo_atual - faltante
                    carteira.save(update_fields=["saldo_cancelado", "saldo_disponivel", "updated_at"])
                else:
                    cls._gerar_conta_receber_cashback(carteira, venda, faltante, usuario)

        debitos_da_venda = list(
            MovimentoCashback.objects.select_for_update()
            .filter(venda=venda, tipo=MovimentoCashback.Tipo.DEBITO_UTILIZACAO)
        )
        ja_estornado = MovimentoCashback.objects.filter(
            venda=venda, tipo=MovimentoCashback.Tipo.ESTORNO,
        ).exists()
        if debitos_da_venda and not ja_estornado:
            total_debitado = sum((m.valor for m in debitos_da_venda), Decimal("0"))
            carteira = CarteiraCashback.objects.select_for_update().get(pk=debitos_da_venda[0].carteira_id)
            saldo_disponivel = cls._recalcular_saldo_disponivel(carteira)
            if total_debitado > 0:
                MovimentoCashback.objects.create(
                    carteira=carteira, cliente=carteira.cliente, empresa=carteira.empresa,
                    venda=venda, usuario=usuario, tipo=MovimentoCashback.Tipo.ESTORNO,
                    valor=total_debitado,
                    observacao=motivo or f"Estorno do cashback usado na venda #{venda.numero_venda}.",
                    origem=MovimentoCashback.Origem.SISTEMA,
                )
                carteira.saldo_disponivel = saldo_disponivel + total_debitado
                carteira.save(update_fields=["saldo_disponivel", "updated_at"])

    @staticmethod
    def _gerar_conta_receber_cashback(carteira, venda, valor: Decimal, usuario) -> None:
        from apps.financeiro.constants.enums import StatusContaReceber
        from apps.financeiro.models import ContaReceber

        hoje = timezone.localdate()
        ContaReceber.objects.create(
            filial=venda.filial,
            cliente_id=carteira.cliente_id,
            documento_tipo="cashback_estorno",
            documento_id=venda.pk,
            documento_numero=str(venda.numero_venda),
            valor_original=valor,
            valor_final=valor,
            valor_saldo=valor,
            data_emissao=hoje,
            data_vencimento=hoje,
            status=StatusContaReceber.ABERTO,
            observacao=(
                f"Cashback já utilizado pelo cliente na venda #{venda.numero_venda}, "
                "cancelada/editada — valor a recuperar."
            ),
            usuario=usuario,
        )

    # ----------------------------------------------------------- expiração
    @classmethod
    @transaction.atomic
    def expirar_creditos(cls, *, carteira=None, hoje=None) -> int:
        """
        Expira o saldo ainda disponível de créditos vencidos.

        Se `carteira` for informada, escopo local — chamado antes de
        qualquer leitura/débito de saldo (expiração "preguiçosa", sempre
        correta mesmo sem um worker Celery rodando). Sem `carteira`,
        varre todas as carteiras com crédito vencido (uso em lote).

        Simplificação assumida: não há rastreio FIFO por lote de crédito
        individual — quando uma leva de créditos vence, comparamos o
        total vencido com o total já expirado anteriormente e expiramos
        a diferença, nunca mais do que o saldo disponível atual (o que já
        foi gasto ou expirado antes não é expirado de novo).
        """
        from apps.cashback.models import CarteiraCashback, MovimentoCashback

        hoje = hoje or timezone.localdate()

        if carteira is not None:
            carteiras = [carteira]
        else:
            carteiras = list(
                CarteiraCashback.objects.filter(
                    movimentos__tipo=MovimentoCashback.Tipo.CREDITO_VENDA,
                    movimentos__data_validade__lt=hoje,
                ).distinct()
            )

        total_processadas = 0
        for cart in carteiras:
            cart = CarteiraCashback.objects.select_for_update().get(pk=cart.pk)
            saldo_disponivel = cls._recalcular_saldo_disponivel(cart)
            if saldo_disponivel <= 0:
                continue

            vencidos = MovimentoCashback.objects.filter(
                carteira=cart, tipo=MovimentoCashback.Tipo.CREDITO_VENDA, data_validade__lt=hoje,
            ).aggregate(total=Sum("valor"))["total"] or Decimal("0")
            ja_expirado = MovimentoCashback.objects.filter(
                carteira=cart, tipo=MovimentoCashback.Tipo.EXPIRACAO,
            ).aggregate(total=Sum("valor"))["total"] or Decimal("0")
            a_expirar = min(vencidos - ja_expirado, saldo_disponivel)
            if a_expirar <= 0:
                continue

            MovimentoCashback.objects.create(
                carteira=cart, cliente=cart.cliente, empresa=cart.empresa,
                tipo=MovimentoCashback.Tipo.EXPIRACAO, valor=a_expirar,
                observacao=f"Expiração de créditos vencidos até {hoje.isoformat()}.",
                origem=MovimentoCashback.Origem.SISTEMA,
            )
            cart.saldo_expirado += a_expirar
            cart.saldo_disponivel = saldo_disponivel - a_expirar
            cart.save(update_fields=["saldo_expirado", "saldo_disponivel", "updated_at"])
            total_processadas += 1

        return total_processadas
