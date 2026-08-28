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

QUANDO ELA NÃO CHEGA, O MOTIVO É OBRIGATÓRIO

Sem motivo, "não entregue" é um número em relatório. Com ele, é um problema
com dono: "cliente ausente" é roteiro errado, "produto danificado" é
carregamento errado — e os dois se resolvem em lugares diferentes.

Enquanto o retorno não é tratado, a mercadoria fica identificada como
BONIFICAÇÃO NÃO ENTREGUE: ela saiu do estoque, não virou entrega e ainda
não voltou. É o único estado em que a caixa está fora de qualquer saldo.

O RETORNO DEVOLVE DE VERDADE, E CADA UMA PARA ONDE SAIU

  · a bonificação da CARGA saiu do estoque da filial quando o caminhão
    fechou, e volta para lá, com movimento próprio no razão;

  · a da RUA consumiu o saldo em poder da viagem (o estoque saiu na
    remessa), e volta para o saldo.

Devolver no lugar errado é pior do que não devolver: somaria estoque que
nunca saiu de lá, e o inventário passaria a acusar sobra sem explicação. E
marcar RETORNADA sem devolver — que era o que este serviço fazia antes — deixa
o status dizendo que voltou enquanto a mercadoria segue fora de qualquer
saldo.
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
        if destino == S.RETORNADA:
            return cls.tratar_retorno(entrega, dados, usuario)

        campos = ['status', 'observacao', 'updated_at']
        motivo = (dados.get('motivo_nao_entrega') or '').strip()
        if destino in EntregaBonificacao.EXIGEM_MOTIVO:
            # SEM MOTIVO, "NAO ENTREGUE" E' UM NUMERO EM RELATORIO. Com ele,
            # e' um problema com dono: cliente ausente e' roteiro errado;
            # produto danificado e' carregamento errado -- e os dois se
            # resolvem em lugares diferentes.
            if not motivo:
                raise DadosInvalidosError(
                    'Diga por que a bonificação não foi entregue — é o motivo '
                    'que transforma a devolução em problema com dono.'
                )
            if motivo not in EntregaBonificacao.MotivoNaoEntrega.values:
                raise DadosInvalidosError('Motivo de não entrega desconhecido.')
            entrega.motivo_nao_entrega = motivo
            entrega.nao_entregue_em = timezone.now()
            campos += ['motivo_nao_entrega', 'nao_entregue_em']

        entrega.status = destino
        observacao = (dados.get('observacao') or '').strip()
        if observacao:
            entrega.observacao = (
                f'{entrega.observacao}\n{observacao}'.strip()
            )
        entrega.save(update_fields=campos)
        return entrega

    @classmethod
    @transaction.atomic
    def tratar_retorno(cls, entrega: EntregaBonificacao,
                       dados: dict | None = None,
                       usuario=None) -> EntregaBonificacao:
        """
        Devolve de fato a mercadoria que não foi entregue.

        AQUI A MERCADORIA VOLTA — e cada origem volta para onde saiu:

          · a bonificação da CARGA saiu do estoque da filial quando o
            caminhão fechou, e volta para lá, com movimento próprio no razão
            dizendo que é retorno de cortesia não entregue;

          · a da RUA consumiu o saldo em poder da viagem (o estoque saiu na
            remessa), e volta para o saldo — que é o que o cancelamento da
            entrega já faz.

        DEVOLVER NO LUGAR ERRADO É PIOR DO QUE NÃO DEVOLVER: somaria estoque
        que nunca saiu de lá, e o inventário passaria a acusar sobra sem
        explicação.

        MARCAR RETORNADA SEM DEVOLVER TAMBÉM NÃO SERVE: era o que faltava
        antes — o status dizia que voltou e a mercadoria continuava fora de
        qualquer saldo.
        """
        dados = dados or {}
        if entrega.status != S.RETORNO_PENDENTE:
            raise DadosInvalidosError(
                'Só se trata o retorno de uma bonificação com retorno '
                'pendente — antes disso ela ainda está com o cliente ou no '
                'caminhão.'
            )

        motivo = entrega.get_motivo_nao_entrega_display() or 'não entregue'
        if entrega.item_carga_id:
            cls._devolver_ao_estoque(entrega, motivo, usuario)
        else:
            cls._devolver_ao_saldo(entrega, motivo)

        entrega.status = S.RETORNADA
        entrega.retorno_tratado_em = timezone.now()
        observacao = (dados.get('observacao') or '').strip()
        if observacao:
            entrega.observacao = f'{entrega.observacao}\n{observacao}'.strip()
        entrega.save(update_fields=[
            'status', 'retorno_tratado_em', 'observacao', 'updated_at',
        ])
        return entrega

    @staticmethod
    def _devolver_ao_estoque(entrega, motivo: str, usuario=None) -> None:
        """A cortesia da carga volta para a prateleira de onde saiu."""
        from apps.estoque.models import MovimentacaoEstoque
        from apps.estoque.services.movimentacao_service import MovimentacaoService

        item = entrega.item_carga
        viagem = item.viagem
        MovimentacaoService.registrar_movimentacao(
            produto_id=item.produto_id,
            filial_id=viagem.filial_id,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.DEVOLUCAO_CLIENTE,
            quantidade=item.quantidade,
            usuario_id=getattr(usuario, 'pk', None) or viagem.responsavel_id,
            lote_id=item.lote_id,
            valor_unitario=item.valor_unitario or None,
            documento_tipo='viagem',
            documento_id=viagem.pk,
            documento_numero=str(viagem.numero),
            cliente_id=item.cliente_id,
            observacao=(
                f'Retorno de Bonificação não entregue ({motivo}) — '
                f'viagem #{viagem.numero:06d}'
            ),
            permitir_sem_lote=True,
        )

    @staticmethod
    def _devolver_ao_saldo(entrega, motivo: str) -> None:
        """
        A cortesia da rua volta para o saldo em poder da viagem.

        O estoque da filial não é tocado: aquela mercadoria saiu na remessa,
        e quem responde por ela até o fim da viagem é o saldo.
        """
        from apps.logistica.services.venda_viagem import VendaViagemService

        VendaViagemService.cancelar(
            entrega.entrega_rua, motivo=f'Bonificação não entregue: {motivo}',
        )

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
        if entrega.nao_entregue:
            # A CAIXA ESTA' NO CAMINHAO, fora do estoque e sem dono. Enquanto
            # o retorno nao e' tratado, ela precisa continuar identificada.
            faltando.append(
                f'{entrega.rotulo_nao_entregue} — a mercadoria saiu, não foi '
                'entregue e ainda não voltou.'
            )
        elif entrega.status in EntregaBonificacao.ABERTAS:
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
