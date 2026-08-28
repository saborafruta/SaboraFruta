"""
A viagem inteira em uma linha: onde ela está e o que falta.

    VIAGEM → CARGA → MDF-e → TRANSPORTE → (vendas, bonificações, saldos)
           → RETORNO → NF-e DE RETORNO → CONFERÊNCIA → CONCILIAÇÃO
           → ENCERRAMENTO

A ARQUITETURA JÁ EXISTE ESPALHADA, E É ESSE O PROBLEMA

Cada etapa tem a sua tela, o seu serviço e a sua regra — e todas funcionam.
O que não existia era o lugar onde alguém vê a ORDEM: quem abre uma viagem
pela primeira vez não sabe se o próximo passo é emitir o MDF-e, conferir o
retorno ou encerrar, e descobre clicando. Uma operação aprendida por
tentativa é uma operação em que a etapa esquecida só aparece na
fiscalização.

NADA AQUI DECIDE NADA

Este serviço não valida, não bloqueia e não grava: ele pergunta a cada dono
de etapa em que pé ela está e põe as respostas em ordem. Quem recusa
encerrar continua sendo o `ViagemService`; quem diz se o MDF-e está
autorizado continua sendo o `MDFeViagemService`. Repetir as regras aqui
criaria uma segunda versão delas, que divergiria da primeira exatamente no
caso raro em que alguém consulta esta tela.

ETAPA QUE NÃO SE APLICA NÃO É "PENDENTE"

Uma viagem que só leva venda já faturada não tem remessa, não tem venda na
rua e não tem retorno — e marcar essas etapas como pendentes ensinaria a
ignorar o pendente, que é o pior efeito que um indicador pode ter. Elas
aparecem como DISPENSADAS, com o motivo escrito.
"""
from __future__ import annotations

from decimal import Decimal

from apps.fiscal.models import NaturezaOperacao
from apps.logistica.models import Viagem

ZERO = Decimal('0')

E = NaturezaOperacao.Especie

# Os estados de uma etapa. "Dispensada" existe porque nem toda viagem passa
# por todas — e uma etapa que não se aplica não é uma pendência.
CONCLUIDA = 'concluida'
ATUAL = 'atual'
PENDENTE = 'pendente'
DISPENSADA = 'dispensada'


class FluxoViagemService:

    @classmethod
    def etapas(cls, viagem) -> list[dict]:
        """
        As etapas da viagem, na ordem em que acontecem.

        A ORDEM É A DA OPERAÇÃO, e não a do menu: a carga fecha antes do
        MDF-e porque o manifesto declara o que já subiu no caminhão; o
        retorno vem antes da nota de retorno porque a nota documenta a
        contagem, e não a substitui.
        """
        from apps.logistica.services.estoque_viagem import EstoqueViagemService
        from apps.logistica.services.mdfe_viagem import MDFeViagemService
        from apps.logistica.services.retorno_nfe import RetornoVendaForaService
        from apps.logistica.services.viagem import ViagemService

        resumo = ViagemService.resumo(viagem)
        quadro = EstoqueViagemService.quadro(viagem)
        mdfe = MDFeViagemService.painel(viagem)
        pendencias = (
            [] if viagem.status in Viagem.STATUS_ENCERRADOS
            else ViagemService.pendencias_de_encerramento(viagem)
        )
        remetido = quadro['remetido']

        etapas = [
            cls._carga(viagem, resumo, quadro),
            cls._mdfe(viagem, mdfe),
            cls._transporte(viagem),
            cls._durante(viagem, quadro),
            cls._retorno(viagem, quadro, remetido),
            cls._nota_de_retorno(viagem, remetido, RetornoVendaForaService),
            cls._conferencia(viagem, quadro),
            cls._conciliacao(viagem, quadro, EstoqueViagemService),
            cls._encerramento(viagem, pendencias),
        ]
        return cls._marcar_a_atual(
            etapas, encerrada=viagem.status in Viagem.STATUS_ENCERRADOS,
        )

    # ── Cada etapa pergunta ao seu dono ──────────────────────────────────

    @staticmethod
    def _carga(viagem, resumo, quadro) -> dict:
        """As quatro linhas que compõem a carga, cada uma com sua natureza."""
        composicao = [
            ('🧾', 'Vendas já realizadas', quadro['vendas_realizadas']),
            ('🚚', 'Remessa para venda fora', quadro['remetido']),
            ('🎁', 'Bonificações', quadro['bonificacao_da_carga']),
            ('📄', 'Outros documentos fiscais', quadro['outras_remessas']),
        ]
        fechada = viagem.status not in Viagem.STATUS_EDITAVEIS
        return {
            'chave': 'carga',
            'icone': '📦',
            'titulo': 'Carga',
            'estado': CONCLUIDA if fechada else PENDENTE,
            'detalhe': (
                f'{resumo["total_fisico"]} unidades em {resumo["itens"]} itens'
                if resumo['itens'] else 'nenhum item lançado'
            ),
            'linhas': [
                {'icone': icone, 'rotulo': rotulo, 'quantidade': quantidade}
                for icone, rotulo, quantidade in composicao if quantidade
            ],
            'rota': 'logistica:viagem-detail',
        }

    @staticmethod
    def _mdfe(viagem, mdfe) -> dict:
        # AUTORIZADO OU ENCERRADO: o manifesto encerrado ja' cumpriu o papel
        # dele, e marcar a etapa como pendente por causa disso mandaria
        # alguem emitir um segundo MDF-e para a mesma viagem.
        from apps.logistica.models import MDFe

        cumprido = mdfe.get('status_valor') in (
            MDFe.Status.AUTORIZADO, MDFe.Status.ENCERRADO,
        )
        return {
            'chave': 'mdfe',
            'icone': '📋',
            'titulo': 'MDF-e',
            'estado': CONCLUIDA if cumprido else PENDENTE,
            'detalhe': (
                f'{mdfe["numero"]}/{mdfe["serie"]} · {mdfe["status"]}'
                if mdfe['emitido'] else f'{mdfe["status"]} — a carga está sem manifesto'
            ),
            'linhas': [],
            'rota': 'logistica:viagem-mdfe',
        }

    @staticmethod
    def _transporte(viagem) -> dict:
        return {
            'chave': 'transporte',
            'icone': '🚚',
            'titulo': 'Transporte',
            'estado': CONCLUIDA if viagem.saiu else PENDENTE,
            'detalhe': (
                f'{viagem.motorista_nome or "sem motorista"} · '
                f'{viagem.veiculo_placa or "sem placa"}'
            ),
            'linhas': [],
            'rota': 'logistica:viagem-detail',
        }

    @staticmethod
    def _durante(viagem, quadro) -> dict:
        """
        O que acontece com o caminhão na rua.

        SÓ EXISTE COM MERCADORIA SEM COMPRADOR: uma viagem que leva apenas
        venda já faturada não vende nem bonifica na rota — ela entrega.
        """
        movimento = quadro['venda_na_rua'] + quadro['bonificacao_na_rua']
        if not quadro['remetido']:
            estado, detalhe = DISPENSADA, 'a viagem não leva mercadoria sem comprador'
        elif movimento:
            estado = CONCLUIDA if not quadro['em_poder'] else ATUAL
            detalhe = (
                f'{quadro["venda_na_rua"]} vendidas · '
                f'{quadro["bonificacao_na_rua"]} bonificadas · '
                f'{quadro["em_poder"]} ainda no caminhão'
            )
        else:
            estado = PENDENTE
            detalhe = f'{quadro["em_poder"]} disponíveis para venda'
        return {
            'chave': 'durante',
            'icone': '🛣️',
            'titulo': 'Durante a viagem',
            'estado': estado,
            'detalhe': detalhe,
            'linhas': [
                {'icone': '🧾', 'rotulo': 'Novas vendas', 'quantidade': quadro['venda_na_rua']},
                {'icone': '🎁', 'rotulo': 'Bonificações entregues', 'quantidade': quadro['bonificacao_na_rua']},
                {'icone': '📦', 'rotulo': 'Estoque em trânsito', 'quantidade': quadro['em_poder']},
            ],
            'rota': 'logistica:viagem-vendas',
        }

    @staticmethod
    def _retorno(viagem, quadro, remetido) -> dict:
        if not remetido:
            estado, detalhe = DISPENSADA, 'não há remessa para retornar'
        elif quadro['em_poder']:
            estado = PENDENTE
            detalhe = f'{quadro["em_poder"]} ainda em poder da viagem'
        elif quadro['retorno']:
            estado, detalhe = CONCLUIDA, f'{quadro["retorno"]} voltaram ao estoque'
        else:
            estado, detalhe = CONCLUIDA, 'nada sobrou para voltar'
        return {
            'chave': 'retorno',
            'icone': '↩️',
            'titulo': 'Retorno',
            'estado': estado,
            'detalhe': detalhe,
            'linhas': [],
            'rota': 'logistica:viagem-retorno',
        }

    @staticmethod
    def _nota_de_retorno(viagem, remetido, servico) -> dict:
        nota = servico.nota_da_viagem(viagem)
        if not remetido:
            estado, detalhe = DISPENSADA, 'sem remessa, não há retorno a documentar'
        elif nota is not None:
            estado = CONCLUIDA
            detalhe = f'NF-e {nota.numero}/{nota.serie} · {nota.get_status_display()}'
        else:
            pendencias = servico.conferir(viagem)
            estado = PENDENTE
            detalhe = pendencias[0] if pendencias else 'pronta para emitir'
        return {
            'chave': 'nfe_retorno',
            'icone': '🧾',
            'titulo': 'NF-e de retorno',
            'estado': estado,
            'detalhe': detalhe,
            'linhas': [],
            'rota': 'logistica:viagem-retorno',
        }

    @staticmethod
    def _conferencia(viagem, quadro) -> dict:
        return {
            'chave': 'conferencia',
            'icone': '🔍',
            'titulo': 'Conferência',
            'estado': PENDENTE if quadro['em_poder'] else CONCLUIDA,
            'detalhe': (
                f'{quadro["em_poder"]} sem destino registrado'
                if quadro['em_poder'] else 'tudo que saiu tem destino'
            ),
            'linhas': [],
            'rota': 'logistica:viagem-acerto',
        }

    @staticmethod
    def _conciliacao(viagem, quadro, servico) -> dict:
        conciliacao = servico.conciliacao(quadro)
        return {
            'chave': 'conciliacao',
            'icone': conciliacao['sinal'],
            'titulo': 'Conciliação',
            'estado': (
                CONCLUIDA if conciliacao['cor'] == 'verde'
                else PENDENTE
            ),
            'detalhe': (
                f'{quadro["destinos"]} / {quadro["carga_inicial"]} — '
                f'{conciliacao["rotulo"]}'
            ),
            'linhas': [],
            'rota': 'logistica:viagem-acerto',
        }

    @staticmethod
    def _encerramento(viagem, pendencias) -> dict:
        if viagem.status == Viagem.Status.FINALIZADA:
            estado, detalhe = CONCLUIDA, 'viagem encerrada'
        elif viagem.status == Viagem.Status.CANCELADA:
            estado, detalhe = DISPENSADA, 'viagem cancelada'
        elif pendencias:
            estado, detalhe = PENDENTE, pendencias[0]
        else:
            estado, detalhe = PENDENTE, 'a carga fecha — pronta para encerrar'
        return {
            'chave': 'encerramento',
            'icone': '🏁',
            'titulo': 'Encerramento',
            'estado': estado,
            'detalhe': detalhe,
            'linhas': [],
            'rota': 'logistica:viagem-acerto',
        }

    # ── Onde a viagem está ───────────────────────────────────────────────

    @staticmethod
    def _marcar_a_atual(etapas: list[dict], encerrada: bool = False) -> list[dict]:
        """
        A primeira etapa não resolvida é a de agora.

        UMA SÓ FICA EM DESTAQUE. Marcar todas as pendentes daria uma lista de
        cobranças simultâneas onde existe uma fila — e quem opera precisa
        saber qual é o PRÓXIMO passo, não os sete que faltam.

        VIAGEM ENCERRADA NÃO TEM "AGORA". Nada mais vem a seguir, e apontar
        um próximo passo numa viagem que acabou seria pedir uma ação que o
        sistema já não aceita. O que ficou por fazer continua PENDENTE, à
        vista: uma viagem que encerrou sem MDF-e encerrou mesmo assim, e é
        exatamente isso que alguém precisa poder enxergar depois.
        """
        achou = encerrada
        for etapa in etapas:
            if not achou and etapa['estado'] in (PENDENTE, ATUAL):
                etapa['estado'] = ATUAL
                achou = True
            elif etapa['estado'] == ATUAL:
                etapa['estado'] = PENDENTE
        return etapas

    @classmethod
    def resumo(cls, etapas: list[dict]) -> dict:
        """Quantas etapas já ficaram para trás, e qual é a de agora."""
        contam = [e for e in etapas if e['estado'] != DISPENSADA]
        atual = next((e for e in etapas if e['estado'] == ATUAL), None)
        return {
            'total': len(contam),
            'concluidas': len([e for e in contam if e['estado'] == CONCLUIDA]),
            'atual': atual,
            'terminada': atual is None,
        }
