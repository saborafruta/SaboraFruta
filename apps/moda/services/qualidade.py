"""
Inspeção de qualidade: checklist, decisão e o que ela faz no fluxo.

A regra que este arquivo protege é uma só: **não se aprova o que tem não
conformidade**. O inspetor decide entre reprovar e mandar para retrabalho —
isso é julgamento e o sistema não tem como fazer por ele —, mas "aprovado"
com um ponto do checklist reprovado é uma contradição, e um sistema que a
aceita transforma o checklist em enfeite.
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.services.exceptions import DomainError
from apps.moda.models import Inspecao, ItemInspecao, OrdemProducao

P = ItemInspecao.Ponto
R = ItemInspecao.Resultado
S = Inspecao.Status

# O checklist da especificação, na ordem em que se confere a peça.
CHECKLIST = [(p.value, (i + 1) * 10) for i, p in enumerate(P)]


class QualidadeService:

    # ── Criação ──────────────────────────────────────────────────────────

    @staticmethod
    @transaction.atomic
    def criar(filial, ordem, usuario=None, quantidade=None) -> Inspecao:
        """
        Abre a inspeção com os nove pontos já criados, todos pendentes.

        A quantidade sai da etapa de Qualidade do fluxo quando existe: é o
        que chegou da produção, e digitá-la de novo é uma chance a mais de
        errar.
        """
        if ordem.status == OrdemProducao.Status.CANCELADA:
            raise DomainError('Ordem cancelada não vai para inspeção.')

        if quantidade is None:
            etapa = next(
                (e for e in ordem.etapas.all() if e.etapa == 'qualidade'), None,
            )
            quantidade = etapa.planejada if etapa else ordem.quantidade

        inspecao = Inspecao.objects.create(
            filial=filial, ordem=ordem, criado_por=usuario,
            quantidade_inspecionada=quantidade,
        )
        ItemInspecao.objects.bulk_create([
            ItemInspecao(inspecao=inspecao, ponto=ponto, ordem_exibicao=ordem_exib)
            for ponto, ordem_exib in CHECKLIST
        ])
        return inspecao

    # ── Checklist ────────────────────────────────────────────────────────

    @staticmethod
    @transaction.atomic
    def avaliar(inspecao: Inspecao, dados: dict) -> int:
        """
        Grava o resultado de cada ponto. Devolve quantos mudaram.

        Ponto ausente do POST fica como está: a tela envia o checklist
        inteiro, mas tratar ausência como "pendente" apagaria a avaliação de
        quem salvou com um campo desabilitado pelo navegador.
        """
        if inspecao.encerrada:
            raise DomainError(
                f'Inspeção {inspecao.get_status_display().lower()} não aceita alteração.'
            )

        alterados = 0
        for item in inspecao.itens.all():
            resultado = (dados.get(f'ponto_{item.pk}') or '').strip()
            observacao = (dados.get(f'obs_{item.pk}') or '').strip()

            mudou = False
            if resultado in R.values and item.resultado != resultado:
                item.resultado = resultado
                mudou = True
            if item.observacao != observacao:
                item.observacao = observacao
                mudou = True

            if mudou:
                item.save(update_fields=['resultado', 'observacao'])
                alterados += 1

        return alterados

    # ── Decisão ──────────────────────────────────────────────────────────

    @classmethod
    @transaction.atomic
    def decidir(cls, inspecao: Inspecao, status: str, dados: dict, usuario) -> Inspecao:
        """Fecha a inspeção com aprovado, reprovado ou retrabalho."""
        if status not in (S.APROVADO, S.REPROVADO, S.RETRABALHO):
            raise DomainError('Decisão inválida.')
        if inspecao.encerrada:
            raise DomainError('Esta inspeção já foi decidida.')

        cls._aplicar_quantidades(inspecao, dados)

        motivo = (dados.get('motivo') or '').strip()

        if status == S.APROVADO and not inspecao.conforme:
            pontos = ', '.join(i.get_ponto_display() for i in inspecao.nao_conformidades)
            raise DomainError(
                f'Não dá para aprovar com não conformidade em: {pontos}. '
                f'Decida entre reprovar e mandar para retrabalho.'
            )

        if status in Inspecao.STATUS_COM_MOTIVO and not motivo:
            raise DomainError(
                'Informe o motivo — é o que permite corrigir a causa depois.'
            )

        if not inspecao.completo:
            raise DomainError(
                'Há pontos do checklist ainda não avaliados. Marque todos, '
                'usando "Não se aplica" no que não couber nesta peça.'
            )

        if not inspecao.fecha:
            raise DomainError(
                f'As quantidades não fecham: {inspecao.quantidade_inspecionada} '
                f'inspecionada(s) contra {inspecao.total_apontado} apontada(s).'
            )

        inspecao.status = status
        inspecao.motivo = motivo
        inspecao.observacao = (dados.get('observacao') or '').strip()
        inspecao.inspetor = (dados.get('inspetor') or inspecao.inspetor).strip()
        inspecao.save()
        return inspecao

    @staticmethod
    def _aplicar_quantidades(inspecao: Inspecao, dados: dict) -> None:
        campos = (
            'quantidade_inspecionada', 'quantidade_aprovada',
            'quantidade_reprovada', 'quantidade_retrabalho',
        )
        for campo in campos:
            if campo not in dados:
                continue
            try:
                valor = int((dados[campo] or '0').strip())
            except (TypeError, ValueError):
                raise DomainError(f'Valor inválido em {campo.replace("_", " ")}.')
            if valor < 0:
                raise DomainError('Quantidade não pode ser negativa.')
            setattr(inspecao, campo, valor)

    # ── Fluxo ────────────────────────────────────────────────────────────

    @staticmethod
    @transaction.atomic
    def aplicar_no_fluxo(inspecao: Inspecao, usuario) -> str:
        """
        Leva o resultado para a etapa de Qualidade da ordem.

        Aprovado vira produção; reprovado vira perda. RETRABALHO NÃO ENTRA em
        nenhum dos dois: a peça voltou para a linha e vai ser inspecionada de
        novo — contá-la agora significaria contar duas vezes quando ela
        passar.
        """
        if not inspecao.encerrada:
            raise DomainError('Decida a inspeção antes de aplicá-la no fluxo.')
        if inspecao.aplicada_no_fluxo:
            raise DomainError('Esta inspeção já foi aplicada no fluxo.')

        etapa = next(
            (e for e in inspecao.ordem.etapas.all() if e.etapa == 'qualidade'), None,
        )
        if etapa is None:
            raise DomainError(
                'A ordem não tem a etapa de Qualidade no fluxo. '
                'Rode criar_etapas_fluxo antes.'
            )

        etapa.quantidade_produzida = inspecao.quantidade_aprovada
        etapa.perda = inspecao.quantidade_reprovada
        etapa.responsavel = inspecao.inspetor or etapa.responsavel
        if not etapa.data_inicio:
            etapa.data_inicio = inspecao.data
        # Só fecha a etapa quando não há peça voltando: com retrabalho
        # pendente a qualidade ainda tem trabalho nesta ordem.
        if inspecao.quantidade_retrabalho:
            etapa.status = etapa.Status.EM_ANDAMENTO
            resultado = 'etapa mantida em andamento — há peças em retrabalho'
        else:
            etapa.status = etapa.Status.CONCLUIDA
            etapa.data_conclusao = etapa.data_conclusao or inspecao.data
            resultado = 'etapa de Qualidade concluída'
        etapa.atualizado_por = usuario
        etapa.save()

        inspecao.aplicada_no_fluxo = True
        inspecao.save(update_fields=['aplicada_no_fluxo'])
        return resultado

    # ── Indicadores ──────────────────────────────────────────────────────

    @staticmethod
    def indicadores(inspecoes) -> dict:
        """
        Números do setor. Só inspeções decididas entram: uma em andamento
        tem quantidades pela metade e puxaria a aprovação para baixo por um
        motivo que não é qualidade.
        """
        fechadas = [i for i in inspecoes if i.encerrada]
        inspecionadas = sum(i.quantidade_inspecionada for i in fechadas)
        aprovadas = sum(i.quantidade_aprovada for i in fechadas)
        reprovadas = sum(i.quantidade_reprovada for i in fechadas)
        retrabalho = sum(i.quantidade_retrabalho for i in fechadas)

        def pct(parte):
            if not inspecionadas:
                return Decimal('0')
            return (Decimal(parte) / inspecionadas * 100).quantize(Decimal('0.1'))

        # Motivos mais frequentes, pelos pontos do checklist reprovados: é o
        # que diz onde atacar, e some numa lista de motivos em texto livre.
        causas: dict[str, int] = {}
        for inspecao in fechadas:
            for item in inspecao.nao_conformidades:
                causas[item.get_ponto_display()] = causas.get(item.get_ponto_display(), 0) + 1

        return {
            'inspecoes': len(fechadas),
            'abertas': len(inspecoes) - len(fechadas),
            'inspecionadas': inspecionadas,
            'aprovadas': aprovadas,
            'reprovadas': reprovadas,
            'retrabalho': retrabalho,
            'percentual_aprovacao': pct(aprovadas),
            'percentual_refugo': pct(reprovadas),
            'percentual_retrabalho': pct(retrabalho),
            'causas': sorted(causas.items(), key=lambda x: x[1], reverse=True),
        }
