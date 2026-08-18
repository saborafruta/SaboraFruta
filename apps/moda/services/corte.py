"""
Controle de corte: gravação da grade e os indicadores do setor.

A regra que este arquivo carrega do pedido para o chão de fábrica é a mesma
de sempre: **a grade e a quantidade nunca divergem**. Lá a divergência
gerava pedido errado; aqui gera lote errado — a costura recebe um número de
peças por tamanho que não é o que foi cortado, e a diferença só aparece na
expedição, com o cliente esperando.
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from apps.core.services.exceptions import DomainError
from apps.moda.models import ItemCorte, RegistroCorte

CEM = Decimal('100')


class CorteService:

    # ── Grade ────────────────────────────────────────────────────────────

    @staticmethod
    @transaction.atomic
    def salvar_grade(corte: RegistroCorte, quantidades: dict) -> int:
        """
        Grava as quantidades por tamanho e sincroniza o total.

        `quantidades` é {tamanho_id: quantidade}. Tamanho zerado é apagado
        em vez de gravado com zero: linha zerada polui a grade da costura e
        não diz nada que a ausência já não diga.
        """
        atuais = {i.tamanho_id: i for i in corte.grade.all()}
        total = 0

        for tamanho_id, quantidade in quantidades.items():
            quantidade = max(0, int(quantidade))
            total += quantidade
            existente = atuais.get(tamanho_id)

            if quantidade == 0:
                if existente:
                    existente.delete()
                continue

            if existente:
                if existente.quantidade != quantidade:
                    existente.quantidade = quantidade
                    existente.save(update_fields=['quantidade'])
            else:
                ItemCorte.objects.create(
                    corte=corte, tamanho_id=tamanho_id, quantidade=quantidade,
                )

        # O total é derivado, nunca digitado à parte: é assim que grade e
        # quantidade não têm como divergir.
        if corte.quantidade != total:
            corte.quantidade = total
            corte.save(update_fields=['quantidade'])

        return total

    # ── Registro ─────────────────────────────────────────────────────────

    @staticmethod
    def validar(corte: RegistroCorte) -> None:
        if corte.ordem.encerrada:
            raise DomainError(
                f'A ordem está {corte.ordem.get_status_display().lower()} — '
                f'não aceita novo corte.'
            )

    @staticmethod
    def alertas(corte: RegistroCorte) -> list[dict]:
        """O que a tela precisa gritar sobre este corte."""
        avisos = []

        if corte.estourou and corte.planejado:
            avisos.append({
                'nivel': 'estouro',
                'texto': (
                    f'Gastou {corte.variacao} m a mais do que o planejado '
                    f'({corte.variacao_percentual}%): {corte.consumo_real} m '
                    f'contra {corte.planejado} m.'
                ),
            })

        if not corte.grade_bate:
            avisos.append({
                'nivel': 'grade',
                'texto': (
                    f'A grade soma {corte.total_da_grade} peça(s) e a quantidade '
                    f'do corte é {corte.quantidade}. Salve a grade para sincronizar.'
                ),
            })

        if corte.quantidade and not corte.consumo_real:
            avisos.append({
                'nivel': 'pendente',
                'texto': 'Consumo real não apontado — o comparativo com o planejado fica sem base.',
            })

        if corte.quantidade and not corte.aproveitamento:
            avisos.append({
                'nivel': 'pendente',
                'texto': 'Aproveitamento do encaixe não informado.',
            })

        return avisos

    # ── Indicadores do setor ─────────────────────────────────────────────

    @classmethod
    def indicadores(cls, cortes) -> dict:
        """
        Os números do corte no conjunto informado.

        O aproveitamento médio é PONDERADO PELO CONSUMO, não pela contagem
        de cortes: um enfesto de 300 m com 70% e outro de 5 m com 95% não
        dão 82,5% de aproveitamento — dão 70,4%. A média simples faria um
        corte pequeno e bem-feito esconder o desperdício do grande.
        """
        cortes = [c for c in cortes if c.status != RegistroCorte.Status.CANCELADO]

        planejado = sum((c.planejado for c in cortes), Decimal('0'))
        real = sum((c.consumo_real or Decimal('0') for c in cortes), Decimal('0'))
        pecas = sum(c.quantidade for c in cortes)

        com_aproveitamento = [c for c in cortes if c.aproveitamento and c.consumo_real]
        base = sum((c.consumo_real for c in com_aproveitamento), Decimal('0'))
        if base:
            ponderado = sum(
                (c.aproveitamento * c.consumo_real for c in com_aproveitamento),
                Decimal('0'),
            ) / base
            aproveitamento = ponderado.quantize(Decimal('0.1'))
        else:
            aproveitamento = Decimal('0')

        variacao = (real - planejado).quantize(Decimal('0.0001'))

        return {
            'cortes': len(cortes),
            'pecas': pecas,
            'planejado': planejado.quantize(Decimal('0.01')),
            'real': real.quantize(Decimal('0.01')),
            'variacao': variacao,
            'variacao_percentual': (
                (variacao / planejado * CEM).quantize(Decimal('0.1'))
                if planejado else Decimal('0')
            ),
            'aproveitamento': aproveitamento,
            'perda': (CEM - aproveitamento).quantize(Decimal('0.1')) if aproveitamento else Decimal('0'),
            'perda_metros': sum(
                (c.perda_metros for c in cortes), Decimal('0'),
            ).quantize(Decimal('0.01')),
            'consumo_por_peca': (
                (real / pecas).quantize(Decimal('0.0001')) if pecas else Decimal('0')
            ),
            'sem_medicao': len(cortes) - len(com_aproveitamento),
        }
