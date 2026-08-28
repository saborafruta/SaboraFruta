"""
O ciclo da entrega de uma bonificação, do estoque à mão de quem recebe.

A PERGUNTA QUE ESTE SERVIÇO FAZ

"A cortesia chegou?" Mercadoria vendida cobra a si mesma — se não chegar, o
cliente liga. A bonificação não: ninguém pagou, ninguém reclama, e é
justamente ela que some no caminho sem que nada acuse. Enquanto a entrega
não é marcada, ela fica PENDENTE e visível.

MARCAR ENTREGUE EXIGE QUEM RECEBEU

Um "entregue: sim" sem nome não prova nada, e é o registro que a auditoria
vai ler. A quantidade também é pedida, e pode ser MENOR que a prometida: o
cliente aceita 15 das 20, e assumir que chegou tudo esconderia justamente a
diferença que precisa voltar.

O COMPROVANTE É ANEXO, NÃO CAMPO

Foto na porta, canhoto assinado, comprovante — é isso que responde "quem
recebeu as 20 caixas?" seis meses depois. O serviço aceita vários por
entrega, porque a doca fotografa mais de uma coisa; e não os exige, porque
exigir foto pararia a rota num lugar sem sinal. O que falta aparece.

ELE NÃO MEXE EM ESTOQUE

Marcar RETORNADA diz que a mercadoria voltou fisicamente. Devolvê-la ao
saldo é o caminho que já existe — cancelar a entrega da rua, ou registrar o
retorno da viagem. Fazer a devolução aqui TAMBÉM significaria a mesma caixa
voltando duas vezes, que é como um controle de bonificação começa a inventar
estoque.
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.services.exceptions import DadosInvalidosError
from apps.logistica.models import (
    ComprovanteBonificacao, EntregaBonificacao, ItemCarga, VendaViagem,
)

ZERO = Decimal('0')
S = EntregaBonificacao.Status


class EntregaBonificacaoService:

    # ── Onde ela mora ────────────────────────────────────────────────────

    @staticmethod
    def para_item(item: ItemCarga) -> EntregaBonificacao:
        """
        O acompanhamento de uma linha de bonificação da carga.

        NASCE NA PRIMEIRA OLHADA, e não no fechamento da carga: assim uma
        bonificação registrada antes desta funcionalidade também passa a ser
        acompanhada, sem migração que invente estado.
        """
        entrega, _ = EntregaBonificacao.objects.get_or_create(item_carga=item)
        return entrega

    @staticmethod
    def para_entrega_da_rua(entrega_rua: VendaViagem) -> EntregaBonificacao:
        """
        O acompanhamento de uma bonificação entregue na rota.

        Ela nasce ENTREGUE quando registrada: o vendedor estava com o cliente
        na frente. Marcá-la como pendente seria descrever errado o que
        aconteceu — mas ela continua podendo receber comprovante, que é o que
        faltava.
        """
        entrega, criada = EntregaBonificacao.objects.get_or_create(
            entrega_rua=entrega_rua,
            defaults={
                'status': S.ENTREGUE,
                'entregue_em': entrega_rua.data,
                'entregue_por': entrega_rua.vendedor,
                'destinatario_nome': entrega_rua.cliente_nome,
                'destinatario_documento': entrega_rua.cliente_documento,
                'quantidade_entregue': sum(
                    (i.quantidade or ZERO for i in entrega_rua.itens.all()), ZERO,
                ),
            },
        )
        return entrega

    # ── O ciclo ──────────────────────────────────────────────────────────

    @classmethod
    @transaction.atomic
    def mover(cls, entrega: EntregaBonificacao, destino: str,
              dados: dict | None = None, usuario=None) -> EntregaBonificacao:
        """
        Muda o status, respeitando o caminho.

        AS TRANSIÇÕES SÃO EXPLÍCITAS: sem elas alguém marca "entregue" numa
        bonificação que voltou, e o controle deixa de significar coisa
        alguma. Recusada tem um destino só — a mercadoria está no caminhão e
        precisa voltar.
        """
        dados = dados or {}
        if destino not in EntregaBonificacao.Status.values:
            raise DadosInvalidosError('Situação desconhecida.')
        if not entrega.pode_ir_para(destino):
            raise DadosInvalidosError(
                f'Uma bonificação {entrega.get_status_display().lower()} não '
                f'pode ir para "{dict(EntregaBonificacao.Status.choices)[destino]}".'
            )

        if destino == S.ENTREGUE:
            return cls.entregar(entrega, dados, usuario)

        entrega.status = destino
        observacao = (dados.get('observacao') or '').strip()
        if observacao:
            entrega.observacao = (
                f'{entrega.observacao}\n{observacao}'.strip()
            )
        entrega.save(update_fields=['status', 'observacao', 'updated_at'])
        return entrega

    @classmethod
    @transaction.atomic
    def entregar(cls, entrega: EntregaBonificacao, dados: dict,
                 usuario=None) -> EntregaBonificacao:
        """
        Marca a chegada: quem recebeu, quanto, quando e por quem.

        EXIGE O NOME DE QUEM RECEBEU. É o registro que a auditoria vai ler, e
        "entregue: sim" não prova nada.
        """
        if not entrega.pode_ir_para(S.ENTREGUE):
            raise DadosInvalidosError(
                f'Uma bonificação {entrega.get_status_display().lower()} não '
                'pode ser marcada como entregue.'
            )

        nome = (dados.get('destinatario_nome') or '').strip()
        if not nome:
            raise DadosInvalidosError(
                'Informe quem recebeu — sem nome, "entregue" não prova nada.'
            )

        quantidade = dados.get('quantidade_entregue')
        if quantidade in (None, ''):
            raise DadosInvalidosError('Informe a quantidade entregue.')
        quantidade = Decimal(str(quantidade))
        if quantidade <= ZERO:
            raise DadosInvalidosError('A quantidade precisa ser maior que zero.')

        prometida = cls.quantidade_prevista(entrega)
        if prometida is not None and quantidade > prometida:
            raise DadosInvalidosError(
                f'A bonificação é de {prometida} e a entrega é de {quantidade}. '
                'Não se entrega mais do que saiu.'
            )

        entrega.status = S.ENTREGUE
        entrega.entregue_em = dados.get('entregue_em') or timezone.now()
        entrega.entregue_por = usuario
        entrega.quantidade_entregue = quantidade
        entrega.destinatario_nome = nome
        entrega.destinatario_documento = (
            dados.get('destinatario_documento') or ''
        ).strip()
        observacao = (dados.get('observacao') or '').strip()
        if observacao:
            entrega.observacao = f'{entrega.observacao}\n{observacao}'.strip()
        entrega.save()
        return entrega

    @staticmethod
    def quantidade_prevista(entrega: EntregaBonificacao) -> Decimal | None:
        """Quanto a bonificação prometia — `None` quando a origem sumiu."""
        if entrega.item_carga_id:
            return entrega.item_carga.quantidade
        if entrega.entrega_rua_id:
            return sum(
                (i.quantidade or ZERO for i in entrega.entrega_rua.itens.all()),
                ZERO,
            )
        return None

    # ── A prova ──────────────────────────────────────────────────────────

    @staticmethod
    def anexar(entrega: EntregaBonificacao, tipo: str, arquivo,
               descricao: str = '', usuario=None) -> ComprovanteBonificacao:
        """
        Guarda um comprovante — foto, assinatura, canhoto ou outro documento.

        VÁRIOS POR ENTREGA: a doca fotografa a caixa, o canhoto e às vezes a
        porta. Um campo único obrigaria a escolher qual prova guardar.
        """
        if tipo not in ComprovanteBonificacao.Tipo.values:
            raise DadosInvalidosError('Tipo de comprovante desconhecido.')
        if not arquivo:
            raise DadosInvalidosError('Escolha o arquivo do comprovante.')

        return ComprovanteBonificacao.objects.create(
            entrega=entrega, tipo=tipo, arquivo=arquivo,
            descricao=(descricao or '').strip(), enviado_por=usuario,
        )

    # ── Leitura ──────────────────────────────────────────────────────────

    @classmethod
    def pendencias(cls, entrega: EntregaBonificacao) -> list[str]:
        """
        O que falta nesta entrega — em texto, não em trava.

        A ROTA NÃO PARA POR FALTA DE FOTO: exigir comprovante pararia a
        entrega num lugar sem sinal. O que falta aparece, e alguém resolve
        depois.
        """
        faltando = []
        if entrega.status in EntregaBonificacao.ABERTAS:
            faltando.append(
                f'Bonificação {entrega.get_status_display().lower()} — ainda '
                'não há registro de que chegou.'
            )
        if entrega.entregue and not entrega.tem_prova:
            faltando.append(
                'Entrega sem comprovante — foto, canhoto ou assinatura é o que '
                'responde "quem recebeu?" depois.'
            )
        prevista = cls.quantidade_prevista(entrega)
        entregue = entrega.quantidade_entregue
        if entrega.entregue and prevista and entregue and entregue < prevista:
            faltando.append(
                f'Entregue {entregue} de {prevista} — a diferença precisa '
                'voltar ou ser baixada.'
            )
        return faltando
