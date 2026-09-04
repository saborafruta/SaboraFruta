"""
Expedição: avanço do processo, conferência e leitura de código.

O AVANÇO É LINEAR E VALIDADO. Cada etapa exige o resultado da anterior:
não se separa o que não foi conferido, não se despacha o que não foi
embalado. Deixar pular etapas transformaria a régua num enfeite, e o
documento chegaria ao cliente com volume faltando sem ninguém saber onde.

A LEITURA DE CÓDIGO aceita quatro coisas: o token da expedição, o token de
um volume, o número da OP e o número da expedição. Aceitar os quatro porque
o que está na mão de quem confere varia — a etiqueta do volume, a folha da
OP ou a tela do computador — e obrigar a saber qual deles o campo espera é
o tipo de detalhe que faz o conferente desistir e anotar no papel.
"""
from __future__ import annotations

import base64
import binascii

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from apps.core.services.exceptions import DomainError
from apps.moda.models import (
    Expedicao, ItemConferencia, OrdemProducao, Volume,
)

S = Expedicao.Status

# Campo de data carimbado ao ENTRAR em cada etapa.
CARIMBO = {
    S.CONFERENCIA: 'data_conferencia',
    S.SEPARACAO: 'data_separacao',
    S.EMBALAGEM: 'data_embalagem',
    S.DESPACHO: 'data_despacho',
    S.ENTREGA: 'data_entrega',
}


class ExpedicaoService:

    # ── Criação ──────────────────────────────────────────────────────────

    @staticmethod
    @transaction.atomic
    def criar(filial, ordem, usuario=None, forcar=False, observacao='') -> Expedicao:
        """
        Abre a expedição de uma ordem cuja produção terminou.

        "Terminou" quer dizer que a etapa de Qualidade do fluxo foi
        encerrada. Abrir antes disso daria um documento de expedição para
        peça que ainda está na costura.
        """
        if ordem.status == OrdemProducao.Status.CANCELADA:
            raise DomainError('Ordem cancelada não vai para expedição.')

        aberta = Expedicao.objects.filter(ordem=ordem).exclude(
            status__in=(S.ENTREGA, S.CANCELADA),
        ).first()
        if aberta:
            raise DomainError(
                f'Esta ordem já tem a expedição #{aberta.numero:04d} em andamento.'
            )

        etapas = list(ordem.etapas.all())
        qualidade = next((e for e in etapas if e.etapa == 'qualidade'), None)
        # `forcar` é o desvio explícito da tela do pedido, onde alguém já viu
        # a lista do que está ignorando e disse que sim. O padrão continua
        # travado: quem chamar sem pensar não abre expedição de peça que
        # ainda está na costura.
        if not forcar and qualidade is not None and not qualidade.encerrada:
            raise DomainError(
                'A produção ainda não passou pela Qualidade — a expedição '
                'começa depois dela.'
            )

        return Expedicao.objects.create(
            filial=filial, ordem=ordem, criado_por=usuario,
            observacao=observacao,
        )

    # ── Conferência ──────────────────────────────────────────────────────

    @staticmethod
    @transaction.atomic
    def conferir(expedicao: Expedicao, quantidades: dict, dados: dict) -> int:
        """
        Grava a conferência por tamanho. Devolve o total conferido.

        Tamanho zerado é apagado em vez de gravado com zero: linha zerada
        não diz nada que a ausência já não diga, e polui a comparação com a
        grade.
        """
        if expedicao.status not in (S.PRODUCAO_CONCLUIDA, S.CONFERENCIA):
            raise DomainError(
                'A conferência já foi encerrada — o documento passou desta etapa.'
            )

        esperadas = {
            celula.tamanho_id: celula
            for celula in expedicao.grade_esperada
            if celula.quantidade
        }
        for tamanho_id, quantidade in quantidades.items():
            quantidade = max(0, int(quantidade))
            celula = esperadas.get(tamanho_id)
            if celula is None:
                raise DomainError('Foi informado um tamanho que não pertence a esta ordem.')
            if quantidade > celula.quantidade:
                raise DomainError(
                    f'{celula.tamanho.sigla}: a quantidade conferida não pode '
                    f'ultrapassar as {celula.quantidade} peça(s) pedidas.'
                )

        atuais = {i.tamanho_id: i for i in expedicao.conferencia.all()}
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
                ItemConferencia.objects.create(
                    expedicao=expedicao, tamanho_id=tamanho_id, quantidade=quantidade,
                )

        conferido_por = (dados.get('conferido_por') or '').strip()
        if conferido_por:
            expedicao.conferido_por = conferido_por
        if expedicao.status == S.PRODUCAO_CONCLUIDA:
            expedicao.status = S.CONFERENCIA
            expedicao.data_conferencia = timezone.now()
        expedicao.save()
        return total

    # ── Assinatura do recebimento ────────────────────────────────────────

    # Um traço de dedo em tela de celular não passa de uns poucos KB; o teto
    # existe para o caso de alguém mandar uma FOTO no lugar do traço, que
    # entraria no banco pela porta do formulário.
    LIMITE_ASSINATURA = 400 * 1024
    PREFIXO = 'data:image/png;base64,'

    @staticmethod
    @transaction.atomic
    def assinar(expedicao: Expedicao, nome: str, traco: str, documento: str = ''):
        """
        Grava quem recebeu e o traço da assinatura.

        NÃO AVANÇA O STATUS. Assinar a conferência é dizer "conferi isto
        junto com vocês"; despachar e entregar continuam sendo atos da
        expedição, com suas datas. Emendar as duas coisas faria uma
        assinatura colhida na bancada marcar como entregue uma caixa que
        ainda não saiu.

        O TRAÇO CHEGA COMO data URL, do `<canvas>`. Decodificar aqui, e não
        na view, mantém o formato aceito num lugar só -- a tela pública de
        entrega vai querer o mesmo, e duas decodificações divergiriam no
        limite de tamanho.
        """
        nome = (nome or '').strip()
        if not nome:
            raise DomainError('Informe o nome de quem está recebendo.')

        if expedicao.cancelada:
            raise DomainError('Expedição cancelada não recebe assinatura.')

        expedicao.recebido_por = nome[:120]
        expedicao.assinado_documento = (documento or '').strip()[:30]

        imagem = ExpedicaoService._decodificar(traco)
        if imagem is not None:
            # `save=False`: o arquivo entra no storage e o registro é gravado
            # UMA vez, junto com o nome -- senão um erro no meio deixaria
            # assinatura sem nome ou nome sem assinatura.
            expedicao.assinatura.save(
                f'assinatura-{expedicao.codigo}.png', ContentFile(imagem), save=False,
            )
            expedicao.assinado_em = timezone.now()

        expedicao.save(update_fields=[
            'recebido_por', 'assinado_documento', 'assinatura', 'assinado_em',
        ])
        return expedicao

    @classmethod
    def _decodificar(cls, traco: str) -> bytes | None:
        """A data URL do canvas vira bytes. Vazio é vazio, lixo é recusado."""
        traco = (traco or '').strip()
        if not traco:
            return None
        if not traco.startswith(cls.PREFIXO):
            raise DomainError('Assinatura em formato não reconhecido.')
        try:
            imagem = base64.b64decode(traco[len(cls.PREFIXO):], validate=True)
        except (binascii.Error, ValueError):
            raise DomainError('Não foi possível ler a assinatura. Tente assinar de novo.')
        if not imagem:
            return None
        if len(imagem) > cls.LIMITE_ASSINATURA:
            raise DomainError('A assinatura ficou grande demais para ser guardada.')
        return imagem

    # ── Volumes ──────────────────────────────────────────────────────────

    @staticmethod
    @transaction.atomic
    def criar_volume(expedicao: Expedicao, dados: dict) -> Volume:
        if expedicao.cancelada:
            raise DomainError('Expedição cancelada não recebe volumes.')
        if expedicao.status in (S.DESPACHO, S.ENTREGA):
            raise DomainError(
                'O documento já foi despachado — volume novo agora não sairia '
                'junto com a carga.'
            )

        try:
            quantidade = int((dados.get('quantidade') or '0').strip())
        except (TypeError, ValueError):
            raise DomainError('Quantidade inválida.')
        if quantidade <= 0:
            raise DomainError('Volume sem peças não é volume.')

        peso = (dados.get('peso_kg') or '').strip()
        return Volume.objects.create(
            expedicao=expedicao,
            quantidade=quantidade,
            peso_kg=peso.replace(',', '.') or None,
            observacao=(dados.get('observacao') or '').strip(),
        )

    @staticmethod
    def remover_volume(volume: Volume) -> None:
        if volume.expedicao.status in (S.DESPACHO, S.ENTREGA):
            raise DomainError(
                'Volume de carga já despachada não se apaga — ele saiu no '
                'caminhão, e o histórico precisa dizer isso.'
            )
        volume.delete()

    # ── Avanço ───────────────────────────────────────────────────────────

    @classmethod
    @transaction.atomic
    def avancar(cls, expedicao: Expedicao, usuario, dados: dict | None = None) -> str:
        """Leva o documento para a próxima etapa, validando a atual."""
        dados = dados or {}

        if expedicao.cancelada:
            raise DomainError('Expedição cancelada não avança.')
        if expedicao.entregue:
            raise DomainError('Esta expedição já foi entregue.')

        proxima = expedicao.ETAPAS[expedicao.posicao + 1]
        cls._validar_saida(expedicao, proxima)

        if proxima == S.DESPACHO:
            expedicao.transportadora = (dados.get('transportadora') or expedicao.transportadora).strip()
            expedicao.rastreio = (dados.get('rastreio') or expedicao.rastreio).strip()
        if proxima == S.ENTREGA:
            expedicao.recebido_por = (dados.get('recebido_por') or '').strip()
            if not expedicao.recebido_por:
                raise DomainError('Informe quem recebeu — é a prova da entrega.')

        expedicao.status = proxima
        campo = CARIMBO.get(proxima)
        if campo and not getattr(expedicao, campo):
            setattr(expedicao, campo, timezone.now())
        expedicao.save()
        return proxima

    @staticmethod
    def _validar_saida(expedicao: Expedicao, proxima: str) -> None:
        """O que cada etapa exige para deixar o documento seguir."""
        if proxima == S.SEPARACAO:
            if not expedicao.quantidade_conferida:
                raise DomainError('Confira as peças antes de separar.')
            if not expedicao.conferencia_fecha:
                falta = expedicao.divergencia_conferencia
                raise DomainError(
                    f'A conferência não fecha: {"faltam" if falta > 0 else "sobram"} '
                    f'{abs(falta)} peça(s) em relação à ordem. Corrija antes de seguir.'
                )

        if proxima == S.DESPACHO:
            if not expedicao.total_volumes:
                raise DomainError('Não há volume embalado — nada a despachar.')
            if not expedicao.volumes_fecham:
                raise DomainError(
                    f'Os volumes somam {expedicao.pecas_nos_volumes} peça(s) e a '
                    f'conferência deu {expedicao.quantidade_conferida}. '
                    f'Acerte os volumes antes de despachar.'
                )

    @staticmethod
    @transaction.atomic
    def cancelar(expedicao: Expedicao, motivo: str) -> None:
        if expedicao.entregue:
            raise DomainError('Expedição já entregue não é cancelada.')
        expedicao.status = S.CANCELADA
        sufixo = f'[Cancelada em {timezone.localdate():%d/%m/%Y}] {motivo}'.strip()
        expedicao.observacao = f'{expedicao.observacao}\n{sufixo}'.strip()
        expedicao.save(update_fields=['status', 'observacao'])

    # ── Leitura de código ────────────────────────────────────────────────

    @staticmethod
    def buscar(filial, texto: str) -> dict:
        """
        Resolve o que foi lido/digitado. Devolve o que achou e por qual via.

        A busca é ordenada do mais específico ao mais genérico, e o token vem
        primeiro: ele é único no sistema inteiro, enquanto o número da OP se
        repete entre filiais.
        """
        texto = (texto or '').strip()
        if not texto:
            return {'achou': False, 'erro': 'Leia ou digite um código.'}

        base = Expedicao.objects.for_filial(filial).select_related(
            'ordem', 'ordem__pedido__cliente',
        )

        expedicao = base.filter(codigo=texto).first()
        if expedicao:
            return {'achou': True, 'expedicao': expedicao, 'via': 'código da expedição'}

        volume = Volume.objects.filter(
            codigo=texto, expedicao__filial=filial,
        ).select_related('expedicao').first()
        if volume:
            return {
                'achou': True, 'expedicao': volume.expedicao, 'volume': volume,
                'via': f'etiqueta do volume {volume.numero}',
            }

        # Número da OP, com ou sem o prefixo — quem digita raramente escreve
        # "OP-2026-" inteiro.
        alvo = texto.upper()
        ordem = OrdemProducao.objects.for_filial(filial).filter(
            numero__iendswith=alvo.removeprefix('OP-'),
        ).first()
        if ordem:
            expedicao = base.filter(ordem=ordem).exclude(status=S.CANCELADA).first()
            if expedicao:
                return {'achou': True, 'expedicao': expedicao, 'via': 'número da OP'}
            return {
                'achou': False, 'ordem': ordem,
                'erro': f'A ordem {ordem.numero} ainda não tem expedição aberta.',
            }

        if texto.isdigit():
            expedicao = base.filter(numero=int(texto)).first()
            if expedicao:
                return {'achou': True, 'expedicao': expedicao, 'via': 'número da expedição'}

        return {'achou': False, 'erro': f'Nada encontrado para “{texto}”.'}

    # ── Indicadores ──────────────────────────────────────────────────────

    @staticmethod
    def resumo(expedicoes) -> list[dict]:
        """Quantas expedições em cada etapa, na ordem do processo."""
        rotulos = dict(Expedicao.Status.choices)
        contagem = {}
        for e in expedicoes:
            contagem[e.status] = contagem.get(e.status, 0) + 1
        return [
            {'status': s, 'label': rotulos[s], 'total': contagem.get(s, 0)}
            for s in Expedicao.ETAPAS
        ]
