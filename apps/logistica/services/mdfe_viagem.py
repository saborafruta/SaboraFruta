"""
Os documentos fiscais de uma viagem, e o MDF-e que os consolida.

UM CAMINHÃO, VÁRIAS NATUREZAS, UM MANIFESTO

A mesma viagem leva mercadoria vendida, mercadoria sem comprador e cortesia.
Fiscalmente são operações distintas — cada uma com sua nota, seu CFOP e seu
destinatário. Fisicamente é uma carga só, e é isso que o MDF-e descreve.

O MANIFESTO NÃO MUDA A NATUREZA DE NADA. Ele é documento de TRANSPORTE:
consolida o que está no caminhão para a fiscalização de estrada. A NF-e de
venda continua sendo venda, a de remessa continua sendo remessa, e a de
bonificação continua sendo bonificação — vinculá-las ao mesmo manifesto não
as transforma numa só operação. Este serviço só ARRUMA a lista; ele não
recalcula, não reclassifica e não emite nada.

A CONSOLIDAÇÃO É EXPLÍCITA, E POR ISSO

"Quando permitido pela legislação aplicável" é uma decisão que depende de UF,
de regime e do que a contabilidade orienta — não de código. Por isso o
sistema MOSTRA tudo que a viagem produziu e deixa a escolha para quem
manifesta, em vez de vincular tudo sozinho ao apertar um botão. Vincular
automaticamente seria decidir, em silêncio, uma questão que não é do
software.

O QUE IMPEDE UMA NOTA DE ENTRAR NO MANIFESTO

Três coisas, e todas aparecem escritas na tela em vez de sumirem com a
linha: nota sem chave (ainda não transmitida), nota que não está autorizada,
e nota já vinculada a outro MDF-e. Esconder a linha faria a pessoa procurar
a nota que ela sabe que existe.
"""
from __future__ import annotations

from decimal import Decimal

from apps.financeiro.constants.enums import StatusDocumentoFiscal
from apps.financeiro.models.fiscal import DocumentoFiscal
from apps.logistica.models import DocumentoMDFe, ItemCarga, MDFe, VendaViagem

ZERO = Decimal('0')

# A origem do documento diz que operação ele é. O rótulo é o que a tela
# mostra -- e é o mesmo vocabulário da carga, para quem lê não ter de
# traduzir.
ROTULOS = {
    'viagem_remessa': 'Remessa',
    'viagem_venda_fora': 'Venda',
    'viagem_bonificacao': 'Bonificação',
    'viagem_retorno': 'Retorno',
    'pedido_venda': 'Venda',
}

# Manifesto que ainda vale. Cancelado não ampara carga nenhuma.
MDFE_VIVOS = (
    MDFe.Status.RASCUNHO,
    MDFe.Status.AGUARDANDO_NFE,
    MDFe.Status.PROCESSANDO,
    MDFe.Status.AUTORIZADO,
    MDFe.Status.ENCERRADO,
)


class MDFeViagemService:

    # ── O manifesto ──────────────────────────────────────────────────────

    @staticmethod
    def mdfe_da_viagem(viagem):
        """
        O manifesto vivo desta viagem — um por viagem, como o modelo diz.

        `None` quando ainda não existe: a tela oferece criar em vez de
        inventar um manifesto vazio que ninguém pediu.
        """
        return (
            MDFe.objects.filter(viagem=viagem, status__in=MDFE_VIVOS)
            .order_by('-id')
            .first()
        )

    @classmethod
    def painel(cls, viagem, mdfe=None) -> dict:
        """
        O manifesto num quadro só: identificação, transporte, rota e totais.

        TUDO LIDO DE ONDE JÁ É VERDADE. Número, série, chave, status, peso e
        valor são do próprio MDF-e; veículo e motorista também, porque é o
        manifesto que responde por quem levou — a viagem pode ter trocado de
        motorista depois de o documento sair, e o que vale para a
        fiscalização é o que está no papel.

        SEM MANIFESTO O PAINEL EXISTE ASSIM MESMO, e diz "Não emitido". Uma
        tela vazia faria parecer que a viagem não tem nada a manifestar,
        quando o que ela não tem é o documento.

        VOLUMES: o MDF-e não guarda essa contagem, e ela só existe quando a
        carga passou por um romaneio. Onde não existe, o painel diz "não
        informado" em vez de somar quantidade de itens como se fossem
        volumes — caixa e unidade não são a mesma coisa, e um número errado
        aqui vira divergência na balança.
        """
        mdfe = mdfe if mdfe is not None else cls.mdfe_da_viagem(viagem)
        if mdfe is None:
            return {
                'mdfe': None,
                'status': 'Não emitido',
                'status_valor': MDFe.Status.RASCUNHO,
                'emitido': False,
                'viagem': viagem,
                'motorista': viagem.motorista_nome,
                'placa': viagem.veiculo_placa,
                'veiculo': viagem.veiculo_descricao,
                'uf_origem': viagem.uf_origem,
                'uf_destino': viagem.uf_destino,
                'documentos': 0,
                'peso': None,
                'volumes': None,
                'valor': None,
                'chave': '',
                'emitido_em': None,
                'autorizado_em': None,
            }

        romaneio = getattr(mdfe, 'romaneio', None)
        return {
            'mdfe': mdfe,
            # ── Identificação ────────────────────────────────────────────
            'numero': mdfe.numero,
            'serie': mdfe.serie,
            'chave': mdfe.chave_acesso or '',
            'status': mdfe.get_status_display(),
            'status_valor': mdfe.status,
            'emitido': True,
            # ── Transporte ───────────────────────────────────────────────
            'veiculo': mdfe.veiculo_descricao or viagem.veiculo_descricao,
            'placa': mdfe.veiculo_placa or viagem.veiculo_placa,
            'motorista': mdfe.motorista_nome or viagem.motorista_nome,
            # ── Rota ─────────────────────────────────────────────────────
            'uf_origem': mdfe.uf_carregamento or viagem.uf_origem,
            'uf_destino': mdfe.uf_descarregamento or viagem.uf_destino,
            'percurso': mdfe.percurso_ufs or viagem.percurso_ufs,
            # ── Carga ────────────────────────────────────────────────────
            'documentos': mdfe.documentos.count(),
            'peso': mdfe.peso_total_kg,
            'volumes': (
                romaneio.volume_total if romaneio is not None else None
            ),
            'valor': mdfe.valor_total,
            # ── Datas ────────────────────────────────────────────────────
            'emitido_em': mdfe.data_emissao,
            'autorizado_em': mdfe.data_autorizacao,
            'protocolo': mdfe.protocolo_autorizacao,
            'mensagem_sefaz': mdfe.mensagem_sefaz,
            'viagem': viagem,
        }

    @staticmethod
    def pendencias_do_painel(painel: dict) -> list[str]:
        """
        O que falta para este manifesto poder ser transmitido.

        EM TEXTO, E NA TELA. Descobrir que falta placa na hora de transmitir
        é descobrir com o caminhão esperando -- e a SEFAZ recusa por dados
        que o sistema já tinha como conferir antes.
        """
        if not painel['emitido']:
            return ['Esta viagem ainda não tem MDF-e.']

        faltando = []
        if not painel['placa']:
            faltando.append('Sem placa do veículo.')
        if not painel['motorista']:
            faltando.append('Sem motorista.')
        if not painel['uf_origem'] or not painel['uf_destino']:
            faltando.append('Rota incompleta — falta UF de origem ou destino.')
        if not painel['documentos']:
            faltando.append('Nenhum documento vinculado ao manifesto.')
        if painel['volumes'] is None:
            faltando.append(
                'Quantidade de volumes não informada — ela não vem do MDF-e, '
                'e sim do romaneio da carga.'
            )
        return faltando

    # ── Os documentos ────────────────────────────────────────────────────

    @staticmethod
    def _origens_da_viagem(viagem) -> dict:
        """
        Onde procurar os documentos desta viagem, por origem.

        A VIAGEM É A CHAVE de umas e não de outras: remessa e retorno
        apontam para a viagem; venda na rua e bonificação apontam para a
        entrega; e a nota do pedido carregado nasceu antes da viagem, e é a
        carga que sabe dela.
        """
        entregas = list(
            VendaViagem.objects.filter(viagem=viagem).values_list('pk', flat=True)
        )
        return {
            'viagem_remessa': [viagem.pk],
            'viagem_retorno': [viagem.pk],
            'viagem_venda_fora': entregas,
            'viagem_bonificacao': entregas,
        }

    @classmethod
    def _notas_dos_pedidos(cls, viagem) -> list:
        """As NF-e que já existiam quando o pedido foi carregado."""
        return list(
            DocumentoFiscal.objects.filter(
                pk__in=ItemCarga.objects
                .filter(viagem=viagem, documento_fiscal__isnull=False)
                .values_list('documento_fiscal_id', flat=True),
            )
        )

    @classmethod
    def documentos(cls, viagem) -> list[dict]:
        """
        Tudo que esta viagem produziu de documento fiscal, numa lista só.

        É a tabela que a fiscalização de estrada espera ver: tipo, número,
        destinatário e valor — com o manifesto ao lado dizendo o que já está
        consolidado.
        """
        mdfe = cls.mdfe_da_viagem(viagem)
        notas = []
        for origem, ids in cls._origens_da_viagem(viagem).items():
            if not ids:
                continue
            notas.extend(
                DocumentoFiscal.objects
                .filter(origem_tipo=origem, origem_id__in=ids)
                .order_by('numero')
            )
        # A MESMA NOTA POR DOIS CAMINHOS. A remessa aponta para a viagem E
        # fica gravada na linha da carga que ela ampara; sem tirar a
        # repetição, ela apareceria duas vezes na tabela e o valor da carga
        # sairia dobrado.
        ja_listadas = {nota.pk for nota in notas}
        notas.extend(
            nota for nota in cls._notas_dos_pedidos(viagem)
            if nota.pk not in ja_listadas
        )

        vinculados = set(
            DocumentoMDFe.objects
            .filter(documento_fiscal__in=notas)
            .values_list('documento_fiscal_id', 'mdfe_id')
        )
        no_mdfe = {doc for doc, manifesto in vinculados
                   if mdfe and manifesto == mdfe.pk}
        em_outro = {doc for doc, manifesto in vinculados
                    if not mdfe or manifesto != mdfe.pk}

        linhas = []
        for nota in notas:
            snapshot = nota.destinatario_snapshot or {}
            linhas.append({
                'documento': nota,
                'tipo': ROTULOS.get(nota.origem_tipo, 'Outro'),
                'destinatario': snapshot.get('nome') or '—',
                'valor': nota.valor_total or ZERO,
                'chave': nota.chave or '',
                'vinculado': nota.pk in no_mdfe,
                'em_outro_mdfe': nota.pk in em_outro,
                'impedimento': cls._impedimento(nota, nota.pk in em_outro),
            })
        # Cancelada, rejeitada e denegada não amparam carga nenhuma, mas
        # continuam na lista com o motivo: sumir com a linha faria a pessoa
        # procurar a nota que ela sabe que existe.
        linhas.sort(key=lambda l: (l['tipo'], l['documento'].numero))
        return linhas

    @staticmethod
    def _impedimento(nota, em_outro_mdfe: bool) -> str:
        """Por que esta nota não pode entrar no manifesto — vazio se puder."""
        if nota.status in (
            StatusDocumentoFiscal.CANCELADA,
            StatusDocumentoFiscal.REJEITADA,
            StatusDocumentoFiscal.DENEGADA,
            StatusDocumentoFiscal.INUTILIZADA,
        ):
            return f'Nota {nota.get_status_display().lower()}.'
        if not nota.chave:
            return 'Sem chave — a nota ainda não foi transmitida.'
        if nota.status != StatusDocumentoFiscal.AUTORIZADA:
            return 'A nota ainda não está autorizada.'
        if em_outro_mdfe:
            return 'Já vinculada a outro MDF-e.'
        return ''

    @classmethod
    def resumo(cls, linhas: list[dict]) -> dict:
        return {
            'documentos': len(linhas),
            'valor': sum((l['valor'] or ZERO for l in linhas), ZERO),
            'vinculados': sum(1 for l in linhas if l['vinculado']),
            'disponiveis': sum(
                1 for l in linhas if not l['vinculado'] and not l['impedimento']
            ),
            'impedidos': sum(1 for l in linhas if l['impedimento']),
        }

    # ── Consolidar ───────────────────────────────────────────────────────

    @classmethod
    def vincular(cls, viagem, documentos_ids, usuario=None) -> int:
        """
        Põe as notas escolhidas no manifesto da viagem. Devolve quantas.

        AS REGRAS DE VÍNCULO SÃO AS DO MDF-e, e não deste serviço: quem
        confere filial, autorização e vínculo duplicado é o mesmo código que
        a tela de MDF-e já usa. Uma segunda checagem aqui divergiria dele no
        dia em que uma das duas mudasse.
        """
        from apps.core.services.exceptions import DadosInvalidosError
        from apps.logistica.views import _vincular_nfe_ao_mdfe

        mdfe = cls.mdfe_da_viagem(viagem)
        if mdfe is None:
            raise DadosInvalidosError(
                'Esta viagem ainda não tem MDF-e. Crie o manifesto antes de '
                'consolidar os documentos.'
            )

        permitidos = {
            linha['documento'].pk: linha
            for linha in cls.documentos(viagem)
            if not linha['impedimento'] and not linha['vinculado']
        }
        escolhidos = [
            permitidos[int(i)]['documento']
            for i in documentos_ids
            if str(i).isdigit() and int(i) in permitidos
        ]
        if not escolhidos:
            raise DadosInvalidosError(
                'Nenhum documento disponível entre os escolhidos — veja o '
                'motivo ao lado de cada linha.'
            )

        for nota in escolhidos:
            _vincular_nfe_ao_mdfe(mdfe, nota)
        return len(escolhidos)

    @staticmethod
    def desvincular(viagem, documento_id) -> bool:
        """
        Tira uma nota do manifesto.

        EXISTE PORQUE ERRAR É NORMAL: marcar a nota errada na pressa da doca
        é comum, e sem o caminho de volta a correção seria refazer o
        manifesto inteiro.
        """
        from apps.core.services.exceptions import DadosInvalidosError

        mdfe = MDFeViagemService.mdfe_da_viagem(viagem)
        if mdfe is None:
            return False
        if mdfe.status == MDFe.Status.AUTORIZADO:
            raise DadosInvalidosError(
                'O manifesto já está autorizado — mudar o que ele ampara '
                'agora exige cancelá-lo na SEFAZ.'
            )

        apagados, _ = DocumentoMDFe.objects.filter(
            mdfe=mdfe, documento_fiscal_id=documento_id,
        ).delete()
        if apagados:
            mdfe.recalcular_totais()
        return bool(apagados)
