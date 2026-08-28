"""
O log das operações da viagem.

POR QUE UM LOG, SE AS TRAVAS JÁ IMPEDEM O ERRO
==============================================

As travas impedem o impossível; o log explica o possível. Uma viagem que sai
com 300 e volta com 170 está certa pela conta e pode estar errada pela
história — e a diferença entre as duas só aparece quando se consegue ler o que
aconteceu, na ordem, com quem e por quê.

É também o que responde à pergunta que vem depois do problema: "quem baixou
essas 30 caixas, e quando?" Sem registro, a resposta é a memória de quem
estava lá.

REGISTRA A QUANTIDADE ANTES E DEPOIS
====================================

Guardar só a movimentação obriga quem lê a somar tudo desde o começo para
saber onde o saldo estava. Guardar os dois lados torna cada linha legível
sozinha — e denuncia buraco: se o "depois" de uma linha não bate com o "antes"
da seguinte, alguém mexeu por fora.
"""
from decimal import Decimal

from apps.core.models import RegistroAuditoria

ZERO = Decimal('0')
MODULO = 'logistica'

# As casas dos campos de quantidade. NA MESMA ESCALA SEMPRE: uma linha com
# "300" e a seguinte com "300.000" parecem números diferentes numa coluna que
# existe justamente para ser lida de cima a baixo.
CASAS = Decimal('0.001')


def _numero(valor) -> str:
    try:
        return str(Decimal(str(valor)).quantize(CASAS))
    except Exception:  # noqa: BLE001
        return str(valor)


class LogViagemService:
    """Escreve no registro de auditoria as operações que mexem na viagem."""

    # As operações que este módulo registra. São os pontos em que quantidade
    # ou documento mudam -- os únicos que alguém vai querer reconstituir.
    CARGA_ITEM_INCLUIDO = 'carga_item_incluido'
    CARGA_ITEM_REMOVIDO = 'carga_item_removido'
    CARGA_FECHADA = 'carga_fechada'
    VENDA_REGISTRADA = 'venda_registrada'
    VENDA_CANCELADA = 'venda_cancelada'
    BONIFICACAO_REGISTRADA = 'bonificacao_registrada'
    RETORNO_REGISTRADO = 'retorno_registrado'
    BAIXA_REGISTRADA = 'baixa_registrada'
    DOCUMENTO_EMITIDO = 'documento_emitido'
    DOCUMENTO_CANCELADO = 'documento_cancelado'
    STATUS_ALTERADO = 'status_alterado'
    VIAGEM_ENCERRADA = 'viagem_encerrada'

    @classmethod
    def registrar(
        cls, viagem, operacao: str, *, usuario=None,
        quantidade_anterior=None, quantidade_nova=None,
        produto=None, documento=None, motivo: str = '',
        descricao: str = '', extras: dict | None = None,
    ) -> RegistroAuditoria:
        """
        Grava uma linha do histórico da viagem.

        NUNCA LEVANTA. Um log que derruba a operação que ele deveria registrar
        troca um problema de auditoria por um problema de produção -- o
        caminhão não pode ficar parado porque a escrita do histórico falhou.
        A falha some do fluxo, mas o dado que importa já está no saldo.
        """
        dados_anteriores, dados_novos = cls._quantidades(
            quantidade_anterior, quantidade_nova, produto,
        )
        try:
            return RegistroAuditoria.objects.create(
                filial=viagem.filial,
                usuario=usuario,
                modulo=MODULO,
                acao=operacao,
                objeto_tipo='logistica.Viagem',
                objeto_id=viagem.pk,
                objeto_descricao=descricao or f'Viagem #{viagem.numero:06d}',
                relacionado_tipo=cls._tipo_do_documento(documento),
                relacionado_id=getattr(documento, 'pk', None),
                justificativa=motivo,
                dados_anteriores=dados_anteriores,
                dados_novos=dados_novos,
                metadados=cls._metadados(viagem, produto, documento, extras),
            )
        except Exception:  # noqa: BLE001
            return None

    # ── Montagem ─────────────────────────────────────────────────────────

    @staticmethod
    def _quantidades(anterior, nova, produto) -> tuple[dict | None, dict | None]:
        """
        Os dois lados da mudança.

        `None` quando a operação não mexe em quantidade -- emitir uma nota, por
        exemplo. Gravar zero ali faria o histórico parecer ter zerado o saldo.
        """
        if anterior is None and nova is None:
            return None, None
        rotulo = str(produto) if produto is not None else ''
        antes = {'quantidade': _numero(anterior)} if anterior is not None else {}
        depois = {'quantidade': _numero(nova)} if nova is not None else {}
        if rotulo:
            antes['produto'] = rotulo
            depois['produto'] = rotulo
        return (antes or None), (depois or None)

    @staticmethod
    def _tipo_do_documento(documento) -> str:
        if documento is None:
            return ''
        meta = getattr(documento, '_meta', None)
        return meta.label if meta is not None else ''

    @staticmethod
    def _metadados(viagem, produto, documento, extras) -> dict:
        dados = {
            'viagem': viagem.numero,
            'status': viagem.status,
        }
        if produto is not None:
            dados['produto_id'] = produto.pk
        if documento is not None:
            numero = getattr(documento, 'numero', None)
            serie = getattr(documento, 'serie', None)
            if numero is not None:
                dados['documento'] = f'{numero}/{serie}' if serie else str(numero)
        if extras:
            dados.update(extras)
        return dados

    # ── Leitura ──────────────────────────────────────────────────────────

    @classmethod
    def historico(cls, viagem):
        """
        As linhas desta viagem, na ordem em que aconteceram.

        DA MAIS ANTIGA PARA A MAIS NOVA: o histórico se lê como narrativa, e
        ordenar do fim para o começo obriga quem investiga a ler de trás para
        frente para entender a sequência.
        """
        return (
            RegistroAuditoria.objects
            .filter(
                modulo=MODULO,
                objeto_tipo='logistica.Viagem',
                objeto_id=viagem.pk,
            )
            .select_related('usuario')
            .order_by('criado_em', 'id')
        )

    @classmethod
    def linhas(cls, viagem) -> list[dict]:
        """O histórico já pronto para a tela."""
        rotulos = dict(cls.OPERACOES)
        linhas = []
        for registro in cls.historico(viagem):
            anterior = (registro.dados_anteriores or {}).get('quantidade')
            nova = (registro.dados_novos or {}).get('quantidade')
            linhas.append({
                'registro': registro,
                'quando': registro.criado_em,
                'usuario': registro.usuario,
                'operacao': rotulos.get(registro.acao, registro.acao),
                'produto': (registro.dados_novos or registro.dados_anteriores or {}).get('produto', ''),
                'quantidade_anterior': anterior,
                'quantidade_nova': nova,
                'documento': (registro.metadados or {}).get('documento', ''),
                'motivo': registro.justificativa,
            })
        return linhas


LogViagemService.OPERACOES = (
    (LogViagemService.CARGA_ITEM_INCLUIDO, 'Item incluído na carga'),
    (LogViagemService.CARGA_ITEM_REMOVIDO, 'Item removido da carga'),
    (LogViagemService.CARGA_FECHADA, 'Carga fechada'),
    (LogViagemService.VENDA_REGISTRADA, 'Venda registrada'),
    (LogViagemService.VENDA_CANCELADA, 'Venda cancelada'),
    (LogViagemService.BONIFICACAO_REGISTRADA, 'Bonificação registrada'),
    (LogViagemService.RETORNO_REGISTRADO, 'Retorno registrado'),
    (LogViagemService.BAIXA_REGISTRADA, 'Baixa registrada'),
    (LogViagemService.DOCUMENTO_EMITIDO, 'Documento fiscal emitido'),
    (LogViagemService.DOCUMENTO_CANCELADO, 'Documento fiscal cancelado'),
    (LogViagemService.STATUS_ALTERADO, 'Etapa alterada'),
    (LogViagemService.VIAGEM_ENCERRADA, 'Viagem encerrada'),
)
