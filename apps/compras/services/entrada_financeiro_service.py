"""Geracao explicita de financeiro a partir de entradas de NF."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


from apps.core.tenant_context import tenant_atomic
from apps.compras.models import EntradaNF, EntradaNFParcela
from apps.core.services.exceptions import DadosInvalidosError
from apps.financeiro.constants.enums import StatusContaPagar
from apps.financeiro.models import ContaPagar


DOCUMENTO_TIPO_ENTRADA_NF = 'entrada_nf'


@dataclass
class GeracaoContasPagarResultado:
    criadas: int = 0
    existentes: int = 0
    ignoradas: int = 0


def _observacao_conta(entrada: EntradaNF, parcela: EntradaNFParcela) -> str:
    partes = [
        f'Gerada pela entrada NF {entrada.numero_nf}/{entrada.serie_nf}.',
        f'Parcela origem {parcela.get_origem_display()}.',
    ]
    if not parcela.data_vencimento:
        partes.append('Vencimento não informado na origem; usada a data de emissão da NF.')
    if parcela.observacao:
        partes.append(parcela.observacao)
    return ' '.join(partes)


def validar_geracao_contas_pagar(entrada: EntradaNF) -> list[str]:
    bloqueios = []
    if not entrada.movimenta_financeiro:
        bloqueios.append('Esta entrada está marcada para não gerar financeiro.')
    if entrada.status != EntradaNF.Status.EFETIVADA:
        bloqueios.append('Finalize a entrada antes de gerar contas a pagar.')
    if entrada.fornecedor_pendente:
        bloqueios.append('Resolva o fornecedor antes de gerar contas a pagar.')

    parcelas = list(entrada.parcelas_financeiras.exclude(
        status=EntradaNFParcela.Status.CANCELADA,
    ))
    if not parcelas:
        bloqueios.append('Inclua ao menos uma parcela financeira.')

    total_parcelas = sum((parcela.valor for parcela in parcelas), Decimal('0'))
    total_financeiro = entrada.valor_total_financeiro
    if parcelas and total_parcelas != total_financeiro:
        bloqueios.append('O total das parcelas precisa bater com o valor financeiro considerado.')

    if parcelas and not any(
        parcela.status == EntradaNFParcela.Status.PENDENTE and not parcela.conta_pagar_id
        for parcela in parcelas
    ):
        bloqueios.append('Todas as parcelas ja possuem conta a pagar gerada.')
    return bloqueios


@tenant_atomic
def gerar_contas_pagar_da_entrada(entrada: EntradaNF, usuario) -> GeracaoContasPagarResultado:
    entrada = (
        EntradaNF.objects
        .select_for_update()
        .select_related('filial', 'fornecedor')
        .get(pk=entrada.pk)
    )
    bloqueios = validar_geracao_contas_pagar(entrada)
    if bloqueios:
        raise DadosInvalidosError(' '.join(bloqueios))

    parcelas = list(
        entrada.parcelas_financeiras
        .select_for_update()
        .exclude(status=EntradaNFParcela.Status.CANCELADA)
        .order_by('data_vencimento', 'numero', 'pk')
    )
    total_parcelas = len(parcelas)
    resultado = GeracaoContasPagarResultado()
    rateios = list(entrada.rateios_financeiros.select_related('plano_contas'))
    plano_contas = rateios[0].plano_contas if len(rateios) == 1 else None

    for indice, parcela in enumerate(parcelas, start=1):
        if parcela.status != EntradaNFParcela.Status.PENDENTE or parcela.conta_pagar_id:
            resultado.ignoradas += 1
            continue

        conta, criada = ContaPagar.objects.get_or_create(
            filial=entrada.filial,
            documento_tipo=DOCUMENTO_TIPO_ENTRADA_NF,
            documento_id=entrada.pk,
            parcela=indice,
            defaults={
                'fornecedor': entrada.fornecedor,
                'documento_numero': entrada.numero_nf,
                'nota_fiscal_fornecedor': entrada.numero_nf,
                'total_parcelas': total_parcelas,
                'valor_original': parcela.valor,
                'valor_final': parcela.valor,
                'valor_saldo': parcela.valor,
                'data_emissao': entrada.data_emissao_nf,
                'data_vencimento': parcela.data_vencimento or entrada.data_emissao_nf,
                'data_competencia': entrada.data_emissao_nf,
                'plano_contas': plano_contas,
                'status': StatusContaPagar.ABERTO,
                'observacao': _observacao_conta(entrada, parcela),
                'usuario': usuario,
            },
        )
        if criada:
            resultado.criadas += 1
        else:
            resultado.existentes += 1
            atualizacoes = []
            if not conta.fornecedor_id:
                conta.fornecedor = entrada.fornecedor
                atualizacoes.append('fornecedor')
            if conta.total_parcelas != total_parcelas:
                conta.total_parcelas = total_parcelas
                atualizacoes.append('total_parcelas')
            if atualizacoes:
                atualizacoes.append('updated_at')
                conta.save(update_fields=atualizacoes)

        parcela.conta_pagar_id = conta.pk
        parcela.status = EntradaNFParcela.Status.GERADA
        parcela.save(update_fields=['conta_pagar_id', 'status', 'updated_at'])

    return resultado
