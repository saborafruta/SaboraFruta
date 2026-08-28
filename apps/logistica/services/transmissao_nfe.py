"""
A transmissão para a SEFAZ das notas que a viagem emite.

O CICLO QUE PARAVA NA PORTA

Remessa, venda na rua, bonificação e retorno já nasciam conferidas,
numeradas e com o payload pronto — e ficavam `PENDENTE` para sempre. Uma
nota que o ERP considera emitida e a SEFAZ nunca viu é pior do que nota
nenhuma: o estoque baixou, o cliente levou a mercadoria, o número foi
consumido, e não existe documento amparando nada disso. O caminhão sai com
papel que não vale.

O QUE ESTE SERVIÇO É, E O QUE ELE NÃO É

Ele transmite o que já foi decidido. Não confere, não numera, não escolhe
CFOP e não monta payload — isso é do emissor de cada natureza, que já sabe
fazer. Aqui só se cuida da conversa com a SEFAZ: token da filial, envio,
retorno e o que fazer quando ela recusa.

TRANSMITE O QUE FOI CONFERIDO, E NÃO O QUE MUDOU DEPOIS

A emissão e a transmissão são dois momentos, e entre eles a operação
continua andando. Por isso o payload guardado na nota é o que vai — remontar
agora mandaria para a SEFAZ números diferentes dos que o ERP registrou nesta
nota, e a divergência só apareceria no XML autorizado. Nota antiga, de antes
deste campo existir, é remontada a partir da origem: é o melhor que dá para
fazer por ela, e o serviço diz isso em vez de fingir.

A AUTORIZAÇÃO É ASSÍNCRONA, E A TELA NÃO PODE MENTIR SOBRE ISSO

A Focus quase sempre responde "processando", e o status final chega depois.
Mostrar "autorizada" no otimismo faria alguém imprimir DANFE de nota que a
SEFAZ ainda vai rejeitar. Enquanto não volta, o documento fica em
PROCESSANDO e a consulta existe para quem quiser saber agora.

REJEIÇÃO NÃO QUEIMA A NOTA. Recusada é um estado de trabalho: corrige-se o
cadastro que causou a rejeição e transmite-se de novo, com o mesmo número.
Reservar outro número a cada tentativa encheria a numeração de buracos que a
SEFAZ cobra depois com inutilização.
"""
from __future__ import annotations

from apps.core.services.exceptions import DadosInvalidosError
from apps.financeiro.constants.enums import StatusDocumentoFiscal
from apps.financeiro.models.fiscal import DocumentoFiscal

# As quatro naturezas que a viagem emite, e quem sabe remontar cada uma.
ORIGENS = (
    'viagem_remessa',
    'viagem_venda_fora',
    'viagem_bonificacao',
    'viagem_retorno',
)

# Estados a partir dos quais transmitir faz sentido. Autorizada não volta
# para a fila; cancelada e inutilizada acabaram.
TRANSMISSIVEIS = (
    StatusDocumentoFiscal.PENDENTE,
    StatusDocumentoFiscal.REJEITADA,
)


class TransmissaoNFeViagemService:

    # ── Leitura ──────────────────────────────────────────────────────────

    @staticmethod
    def da_viagem(viagem) -> list:
        """
        Todas as notas que esta viagem emitiu, de todas as naturezas.

        LIDAS PELA ORIGEM, e não por uma lista guardada: o vínculo já existe
        em cada documento, e uma segunda lista discordaria dele no dia em que
        uma nota fosse cancelada.
        """
        from apps.logistica.models import VendaViagem

        vendas = list(
            VendaViagem.objects.filter(viagem=viagem).values_list('pk', flat=True)
        )
        return list(
            DocumentoFiscal.objects
            .filter(filial=viagem.filial)
            .filter(origem_tipo__in=ORIGENS)
            .filter(
                origem_id__in=[viagem.pk] + vendas,
            )
            .order_by('numero')
        )

    @staticmethod
    def pode_transmitir(documento) -> str:
        """
        Por que esta nota não pode ir para a SEFAZ — vazio quando pode.

        A RECUSA É EXPLICADA ANTES DO CLIQUE. "Falhou" manda a pessoa
        adivinhar; "a filial não tem token da Focus" manda ela a um lugar.
        """
        if documento.origem_tipo not in ORIGENS:
            return 'Esta nota não é de uma viagem.'
        if documento.status == StatusDocumentoFiscal.AUTORIZADA:
            return 'Nota já autorizada pela SEFAZ.'
        if documento.status == StatusDocumentoFiscal.PROCESSANDO:
            return 'Já enviada — aguardando a resposta da SEFAZ.'
        if documento.status not in TRANSMISSIVEIS:
            return f'Nota {documento.get_status_display().lower()} não se transmite.'
        if not (getattr(documento.filial, 'focusnfe_token', '') or '').strip():
            return (
                'A filial está sem o token da Focus NFe. Configure em '
                'Parâmetros › Fiscal antes de transmitir.'
            )
        return ''

    # ── Escrita ──────────────────────────────────────────────────────────

    @classmethod
    def transmitir(cls, documento, usuario=None):
        """
        Manda a nota para a SEFAZ e devolve o documento com o que voltou.

        O PAYLOAD É O QUE FOI CONFERIDO na emissão. Só se remonta quando a
        nota é antiga e não tem payload guardado.
        """
        impedimento = cls.pode_transmitir(documento)
        if impedimento:
            raise DadosInvalidosError(impedimento)

        payload = documento.payload_envio or cls._remontar(documento)
        if not payload:
            raise DadosInvalidosError(
                'Esta nota não tem o conteúdo de envio guardado e não foi '
                'possível remontá-la. Cancele e emita novamente.'
            )

        servico = cls._servico(documento.filial)
        return servico.emitir(documento, payload)

    @classmethod
    def consultar(cls, documento):
        """
        Pergunta à SEFAZ em que pé está a nota.

        EXISTE PORQUE A AUTORIZAÇÃO É ASSÍNCRONA: o webhook resolve o caso
        normal, e esta consulta resolve o dia em que ele não chega — que é
        justamente o dia em que alguém precisa da DANFE.
        """
        if documento.origem_tipo not in ORIGENS:
            raise DadosInvalidosError('Esta nota não é de uma viagem.')
        if not (getattr(documento.filial, 'focusnfe_token', '') or '').strip():
            raise DadosInvalidosError(
                'A filial está sem o token da Focus NFe.'
            )
        return cls._servico(documento.filial).consultar(documento)

    # ── A conversa com a Focus ───────────────────────────────────────────

    @staticmethod
    def _servico(filial):
        """
        O cliente da Focus com o token DESTA filial.

        O TOKEN É POR FILIAL porque a inscrição é: emitir a nota de uma
        filial com o token de outra manda o documento para o CNPJ errado, e
        a SEFAZ autoriza — o erro não aparece aqui, aparece no livro fiscal
        de quem não emitiu nada.
        """
        from apps.fiscal.integrations.focusnfe import FocusNFeClient
        from apps.fiscal.integrations.focusnfe.config import FocusNFeConfig
        from apps.fiscal.services.focusnfe_service import FocusNFeService

        config = FocusNFeConfig.from_env(
            token=(filial.focusnfe_token or '').strip(),
            ambiente=getattr(filial, 'focusnfe_ambiente', None),
        )
        return FocusNFeService(client=FocusNFeClient(config=config))

    @staticmethod
    def _remontar(documento) -> dict:
        """
        Remonta o payload de uma nota emitida antes de o campo existir.

        SÓ PARA AS ANTIGAS. Para as novas, remontar seria trocar o que foi
        conferido pelo que o cadastro diz agora.
        """
        from apps.logistica.models import VendaViagem, Viagem
        from apps.logistica.services.bonificacao_nfe import BonificacaoNFeService
        from apps.logistica.services.remessa_nfe import RemessaVendaForaService
        from apps.logistica.services.retorno_nfe import RetornoVendaForaService
        from apps.logistica.services.venda_fora_nfe import VendaForaNFeService

        numero, serie = documento.numero, documento.serie
        if documento.origem_tipo == 'viagem_remessa':
            viagem = Viagem.objects.filter(pk=documento.origem_id).first()
            return RemessaVendaForaService.construir_payload(viagem, numero, serie) if viagem else {}
        if documento.origem_tipo == 'viagem_retorno':
            viagem = Viagem.objects.filter(pk=documento.origem_id).first()
            return RetornoVendaForaService.construir_payload(viagem, numero, serie) if viagem else {}

        venda = VendaViagem.objects.filter(pk=documento.origem_id).first()
        if venda is None:
            return {}
        if documento.origem_tipo == 'viagem_bonificacao':
            return BonificacaoNFeService.construir_payload(venda, numero, serie)
        return VendaForaNFeService.construir_payload(venda, numero, serie)
