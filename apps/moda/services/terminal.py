"""
Terminais de setor — as telas simplificadas do chão de fábrica.

Corte, Sublimação, Costura e Acabamento pedem coisas diferentes na
especificação: "perdas" num, "falhas" no outro, "rejeição" no terceiro,
"reprovada" no quarto. Mas é a MESMA informação: quantas peças entraram,
quantas saíram boas e quantas se perderam. Aqui existe um terminal só,
parametrizado pelo vocabulário de cada setor.

Quatro telas com quatro modelos separados dariam quatro maneiras de gravar
a mesma coisa, e o relatório de produção teria de somar as quatro sabendo
que "falha" e "rejeição" são a mesma coluna. O apontamento continua sendo o
da etapa do fluxo, que é o que o WIP, o Kanban e o painel já leem.

O que muda de setor para setor é só a APRESENTAÇÃO: quais campos aparecem e
como se chamam.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from apps.moda.models import EtapaOrdem, OrdemProducao

E = EtapaOrdem.Etapa
S = EtapaOrdem.Status


@dataclass(frozen=True)
class Setor:
    slug: str
    titulo: str
    etapa: str
    # Rótulos do vocabulário do setor. O campo gravado é sempre o mesmo.
    rotulo_produzida: str
    rotulo_perda: str
    rotulo_planejada: str = 'A produzir'
    # Campos extras que só alguns setores mostram.
    pede_maquina: bool = False
    pede_tempo: bool = False
    ajuda: str = ''


SETORES: dict[str, Setor] = {
    'corte': Setor(
        slug='corte', titulo='Corte', etapa=E.CORTE,
        rotulo_produzida='Quantidade cortada', rotulo_perda='Perdas',
        rotulo_planejada='A cortar',
        ajuda='Registre início, fim, quantidade cortada e perdas de corte.',
    ),
    'sublimacao': Setor(
        slug='sublimacao', titulo='Sublimação / Bordado / Silk', etapa=E.ESTAMPA,
        rotulo_produzida='Quantidade estampada', rotulo_perda='Falhas',
        rotulo_planejada='A estampar',
        ajuda=(
            'Sublimação, bordado e silk são a mesma etapa do fluxo — a peça '
            'passa uma vez pela estamparia, seja qual for o processo.'
        ),
    ),
    'costura': Setor(
        slug='costura', titulo='Costura', etapa=E.COSTURA,
        rotulo_produzida='Produção', rotulo_perda='Rejeição',
        rotulo_planejada='A costurar',
        pede_maquina=True, pede_tempo=True,
        ajuda='Operador, máquina, tempo gasto, produção e rejeição.',
    ),
    'acabamento': Setor(
        slug='acabamento', titulo='Acabamento', etapa=E.ACABAMENTO,
        rotulo_produzida='Quantidade aprovada', rotulo_perda='Quantidade reprovada',
        rotulo_planejada='Quantidade revisada',
        ajuda=(
            'Revisada é o que chegou; aprovada mais reprovada tem de fechar '
            'com ela.'
        ),
    ),
}

# Endereços do menu que caem no mesmo terminal. Bordado e silk são a mesma
# etapa da sublimação: a peça passa uma vez pela estamparia, e três telas
# gravando a mesma etapa fariam a última a salvar apagar as outras duas.
APELIDOS = {'bordado': 'sublimacao', 'silk': 'sublimacao'}


@dataclass
class Linha:
    ordem: OrdemProducao
    etapa: EtapaOrdem
    pendente_antes: bool = False


@dataclass
class Fila:
    setor: Setor
    em_andamento: list[Linha] = field(default_factory=list)
    aguardando: list[Linha] = field(default_factory=list)
    concluidas_hoje: list[Linha] = field(default_factory=list)

    @property
    def pecas_em_andamento(self) -> int:
        return sum(l.etapa.planejada for l in self.em_andamento)

    @property
    def pecas_aguardando(self) -> int:
        return sum(l.etapa.planejada for l in self.aguardando)

    @property
    def produzidas_hoje(self) -> int:
        return sum(l.etapa.quantidade_produzida for l in self.concluidas_hoje)

    @property
    def perdas_hoje(self) -> int:
        return sum(l.etapa.perda for l in self.concluidas_hoje)


class TerminalService:

    @staticmethod
    def setor(slug: str) -> Setor | None:
        return SETORES.get(APELIDOS.get(slug, slug))

    @classmethod
    def fila(cls, filial, setor: Setor, hoje=None) -> Fila:
        """
        O que este setor tem para fazer, agora.

        Três grupos, nesta ordem: o que já está na máquina, o que chegou e
        espera, e o que saiu hoje. Quem opera precisa ver primeiro o que tem
        em mãos — uma lista única ordenada por prazo faria a peça em cima da
        máquina aparecer no meio de trinta outras.
        """
        from django.utils import timezone
        from apps.moda.services.pcp import PESO_PRIORIDADE
        from datetime import date

        hoje = hoje or timezone.localdate()

        ordens = (
            OrdemProducao.objects.for_filial(filial)
            .exclude(status__in=OrdemProducao.STATUS_ENCERRADOS)
            .select_related('pedido', 'pedido__cliente', 'item', 'item__produto')
            .prefetch_related('etapas')
        )

        fila = Fila(setor=setor)
        for ordem in ordens:
            etapas = list(ordem.etapas.all())
            alvo = next((e for e in etapas if e.etapa == setor.etapa), None)
            if alvo is None or alvo.status == S.PULADA:
                continue

            linha = Linha(ordem=ordem, etapa=alvo)

            if alvo.status == S.EM_ANDAMENTO:
                fila.em_andamento.append(linha)
            elif alvo.status == S.CONCLUIDA:
                if alvo.data_conclusao == hoje:
                    fila.concluidas_hoje.append(linha)
            else:
                # Pendente: só entra na fila se as etapas anteriores já
                # acabaram. Mostrar o que ainda está no corte na tela da
                # costura faria o operador apontar produção de peça que não
                # existe.
                anteriores_abertas = [
                    e for e in etapas
                    if e.sequencia < alvo.sequencia and not e.encerrada
                ]
                linha.pendente_antes = bool(anteriores_abertas)
                if not anteriores_abertas:
                    fila.aguardando.append(linha)

        ordenar = lambda l: (  # noqa: E731
            PESO_PRIORIDADE.get(l.ordem.prioridade, 9),
            l.ordem.prazo or date.max,
            l.ordem.numero,
        )
        fila.em_andamento.sort(key=ordenar)
        fila.aguardando.sort(key=ordenar)
        fila.concluidas_hoje.sort(key=ordenar)
        return fila

    @staticmethod
    def campos_do_setor(setor: Setor) -> list[str]:
        """Campos que este terminal envia — o resto nem aparece na tela."""
        campos = ['status', 'responsavel', 'data_inicio', 'data_conclusao',
                  'quantidade_produzida', 'perda', 'observacao']
        if setor.pede_maquina:
            campos.append('maquina')
        if setor.pede_tempo:
            campos.append('tempo_minutos')
        return campos
