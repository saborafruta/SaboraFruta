"""
O túnel de congelamento: quem está dentro, há quanto tempo, e o que saiu.

POR QUE ISTO NÃO É UM MODELO NOVO. A passagem pelo túnel já é registrada:
é a etapa `CONGELAMENTO` da ordem (`ApontamentoEtapa`), que tem hora de
início, hora de conclusão, operador, equipamento, quantidade e temperatura.
Criar uma "PassagemTunel" ao lado daria dois registros do mesmo evento --
e no dia em que discordassem, ninguém saberia qual mostrar à fiscalização.

O QUE FALTAVA NÃO ERA DADO, ERA A PERGUNTA. A tela de processo pergunta
"como vai esta ordem?" e responde etapa por etapa. Quem opera o túnel faz a
pergunta ao contrário: "o que está DENTRO agora, e o que já passou do
tempo?" -- e essa pergunta atravessa as ordens. É só isso que este serviço
faz: vira a consulta de lado.

O TEMPO ALVO VEM DA RECEITA, não de uma constante. Polpa em bloco de 10 kg
e picolé no mesmo túnel não levam o mesmo tempo, e um número fixo no código
transformaria metade das cargas em alarme falso. Receita sem tempo
declarado devolve `None`, e a tela diz "sem tempo definido" -- inventar um
alvo seria pior do que não ter: cobraria a fábrica por uma regra que
ninguém combinou.

O RELÓGIO CORRE NO SERVIDOR. O tempo dentro do túnel é calculado a cada
leitura da tela, a partir da hora de entrada -- nunca guardado num campo,
que envelheceria em silêncio.
"""
from __future__ import annotations

from django.utils import timezone

from apps.core.services.exceptions import DomainError
from apps.polpa.models import ApontamentoEtapa, Camara, OrdemPolpa
from apps.polpa.models.processo import Etapa

from .frio import FrioService
from .processo import ProcessoService

SIT = ApontamentoEtapa.Situacao


class TunelService:
    """A fila, o dentro e o histórico do túnel."""

    ETAPA = Etapa.CONGELAMENTO

    # ── Consulta ─────────────────────────────────────────────────────────

    @classmethod
    def _base(cls, filial):
        return (
            ApontamentoEtapa.objects.for_filial(filial)
            .filter(etapa=cls.ETAPA)
            .select_related(
                'ordem', 'ordem__ordem', 'ordem__ordem__produto_acabado',
                'ordem__receita', 'operador', 'equipamento', 'lote',
            )
        )

    @staticmethod
    def alvo(op: OrdemPolpa) -> dict | None:
        """
        O que a receita manda para o congelamento desta ordem.

        `None` quando a receita não declara a etapa ou não declara o tempo:
        é o caso de "ninguém definiu", e ele precisa aparecer na tela como
        pendência de cadastro -- não como carga em dia.
        """
        receita = getattr(op, 'receita', None)
        if receita is None:
            return None
        etapa = receita.etapas.filter(etapa=Etapa.CONGELAMENTO).first()
        if etapa is None:
            return None
        return {
            'minutos': etapa.tempo_minutos or None,
            'temperatura_min': etapa.temperatura_min,
            'temperatura_max': etapa.temperatura_max,
            'faixa': etapa.faixa_temperatura,
        }

    @staticmethod
    def _fora_da_faixa(temperatura, alvo: dict | None) -> bool:
        """
        Se a temperatura de saída ficou fora do que a receita manda.

        Sem temperatura medida ou sem faixa declarada, a resposta é "não
        sei" -- e "não sei" não vira alarme, vira o aviso de que falta
        medir ou falta cadastrar.
        """
        if temperatura is None or not alvo:
            return False
        minimo = alvo.get('temperatura_min')
        maximo = alvo.get('temperatura_max')
        if minimo is not None and temperatura < minimo:
            return True
        return maximo is not None and temperatura > maximo

    @classmethod
    def _linha(cls, etapa: ApontamentoEtapa, agora) -> dict:
        """Uma carga, do jeito que a tela do túnel precisa ler."""
        alvo = cls.alvo(etapa.ordem)
        alvo_minutos = alvo['minutos'] if alvo else None

        if etapa.situacao == SIT.EM_ANDAMENTO and etapa.iniciada_em:
            minutos = int((agora - etapa.iniciada_em).total_seconds() // 60)
        else:
            minutos = etapa.duracao_minutos

        restante = None
        excedido = None
        if alvo_minutos is not None and minutos is not None:
            if minutos > alvo_minutos:
                excedido = minutos - alvo_minutos
            else:
                restante = alvo_minutos - minutos

        return {
            'etapa': etapa,
            'ordem': etapa.ordem,
            'produto': etapa.ordem.produto,
            'lote': etapa.ordem.lote,
            'minutos': minutos,
            'alvo': alvo,
            'alvo_minutos': alvo_minutos,
            'restante': restante,
            'excedido': excedido,
            'no_prazo': excedido is None and alvo_minutos is not None,
            # Sem alvo não há atraso a declarar -- há cadastro faltando, e a
            # tela mostra isso em vez de fingir que está tudo certo.
            'sem_alvo': alvo_minutos is None,
            'temperatura_fora': cls._fora_da_faixa(etapa.temperatura, alvo),
        }

    @classmethod
    def dentro(cls, filial, agora=None) -> list[dict]:
        """As cargas que estão no túnel agora, a mais antiga primeiro."""
        agora = agora or timezone.now()
        etapas = cls._base(filial).filter(
            situacao=SIT.EM_ANDAMENTO,
        ).order_by('iniciada_em', 'id')
        return [cls._linha(e, agora) for e in etapas]

    @classmethod
    def esperando(cls, filial, agora=None) -> list[dict]:
        """
        A fila para entrar: ordens abertas com o congelamento não iniciado.

        Só ordem ABERTA -- ordem encerrada com etapa pendente é história, e
        listá-la encheria a fila de trabalho que ninguém vai fazer.
        """
        agora = agora or timezone.now()
        etapas = cls._base(filial).filter(
            situacao=SIT.PENDENTE,
            ordem__situacao__in=OrdemPolpa.ABERTAS,
        ).order_by('ordem__ordem__numero')
        return [cls._linha(e, agora) for e in etapas]

    @classmethod
    def saidas(cls, filial, limite: int = 40, agora=None) -> list[dict]:
        """O que já saiu, do mais recente para o mais antigo."""
        agora = agora or timezone.now()
        etapas = cls._base(filial).filter(
            situacao=SIT.CONCLUIDA,
        ).order_by('-concluida_em', '-id')[:limite]
        return [cls._linha(e, agora) for e in etapas]

    @classmethod
    def tuneis(cls, filial) -> list[dict]:
        """
        As câmaras do tipo túnel, com a última temperatura medida.

        A carga e a câmara são coisas diferentes: a carga tem tempo, a
        câmara tem temperatura. Quem opera precisa das duas na mesma tela --
        um túnel a -25°C explica uma carga que saiu no tempo e mesmo assim
        saiu mole.
        """
        linhas = []
        for camara in Camara.objects.for_filial(filial).filter(
            tipo=Camara.Tipo.TUNEL, ativo=True,
        ):
            leitura = FrioService.temperatura_atual(camara)
            linhas.append({
                'camara': camara,
                'leitura': leitura,
                'sem_leitura': leitura is None,
                'fora_da_faixa': bool(leitura and leitura.fora_da_faixa),
            })
        return linhas

    @classmethod
    def resumo(cls, filial, agora=None) -> dict:
        """Os números do topo da tela."""
        agora = agora or timezone.now()
        dentro = cls.dentro(filial, agora)
        hoje = [
            linha for linha in cls.saidas(filial, limite=200, agora=agora)
            if linha['etapa'].concluida_em
            and timezone.localtime(linha['etapa'].concluida_em).date()
            == timezone.localdate(agora)
        ]

        duracoes = [l['minutos'] for l in hoje if l['minutos'] is not None]
        return {
            'dentro': len(dentro),
            'passaram_do_tempo': sum(1 for l in dentro if l['excedido']),
            'sem_alvo': sum(1 for l in dentro if l['sem_alvo']),
            'saidas_hoje': len(hoje),
            # `None`, e não zero: sem nenhuma saída não há média, e um zero
            # aqui se leria como "congelou instantaneamente".
            'tempo_medio': (
                int(sum(duracoes) / len(duracoes)) if duracoes else None
            ),
            'fora_da_faixa_hoje': sum(1 for l in hoje if l['temperatura_fora']),
        }

    @classmethod
    def painel(cls, filial, agora=None) -> dict:
        agora = agora or timezone.now()
        return {
            'dentro': cls.dentro(filial, agora),
            'esperando': cls.esperando(filial, agora),
            'saidas': cls.saidas(filial, agora=agora),
            'tuneis': cls.tuneis(filial),
            'resumo': cls.resumo(filial, agora),
        }

    # ── Movimento ────────────────────────────────────────────────────────

    @classmethod
    def entrar(cls, etapa: ApontamentoEtapa, dados: dict, usuario=None):
        """
        Põe a carga no túnel: marca a hora de entrada e quem a colocou.

        A ENTRADA NÃO PEDE TEMPERATURA DE SAÍDA, obviamente -- mas também
        não pede a de entrada, porque o campo de temperatura da etapa é o
        que a fiscalização lê como "a que temperatura este lote congelou", e
        gravá-lo na entrada faria a saída sobrescrevê-lo. O que prova o
        congelamento é o número da saída.
        """
        if etapa.etapa != cls.ETAPA:
            raise DomainError('Esta etapa não é o congelamento.')
        if etapa.situacao == SIT.EM_ANDAMENTO:
            raise DomainError('Esta carga já está no túnel.')
        if etapa.situacao == SIT.CONCLUIDA:
            raise DomainError('Esta carga já passou pelo túnel.')

        return ProcessoService.apontar(
            etapa,
            {
                'situacao': SIT.EM_ANDAMENTO,
                'quantidade_entrada': dados.get('quantidade_entrada'),
                'equipamento': dados.get('equipamento'),
                'observacao': dados.get('observacao') or etapa.observacao,
                'operador': dados.get('operador'),
            },
            usuario,
        )

    @classmethod
    def sair(cls, etapa: ApontamentoEtapa, dados: dict, usuario=None):
        """
        Tira a carga do túnel: hora de saída, temperatura e o que saiu.

        EXIGE QUE TENHA ENTRADO. Sem isso, apontar a saída de uma carga que
        nunca entrou gravaria entrada e saída no mesmo instante -- uma
        passagem de zero minuto, que é justamente o registro que faria o
        histórico do túnel parecer perfeito.
        """
        if etapa.etapa != cls.ETAPA:
            raise DomainError('Esta etapa não é o congelamento.')
        if etapa.situacao != SIT.EM_ANDAMENTO:
            raise DomainError(
                'Esta carga não está no túnel — registre a entrada primeiro.'
            )

        return ProcessoService.apontar(
            etapa,
            {
                'situacao': SIT.CONCLUIDA,
                'quantidade_saida': dados.get('quantidade_saida'),
                'temperatura': dados.get('temperatura'),
                'motivo_perda': dados.get('motivo_perda') or etapa.motivo_perda,
                'observacao': dados.get('observacao') or etapa.observacao,
                'operador': dados.get('operador'),
            },
            usuario,
        )
