"""
A cadeia de frio: temperatura, posições e os quatro alertas.

OS QUATRO ALERTAS SÃO DE NATUREZAS DIFERENTES, e tratá-los igual é o erro
que faz um deles nunca chegar a ninguém:

  · TEMPERATURA FORA DO PADRÃO é EVENTO. Aconteceu às 3h40, durou o que
    durou, e o fato não muda depois. Vira notificação na hora do registro —
    e continua verdade mesmo que a câmara volte à faixa dez minutos depois,
    porque o que estava lá dentro passou por aquilo;

  · VENCIMENTO PRÓXIMO, CAPACIDADE ESTOURADA e LOTE BLOQUEADO são
    CONDIÇÕES. Valem enquanto valem e param de valer quando alguém resolve.
    Notificar a cada varredura encheria o sino de repetição até ninguém
    olhar mais; por isso são LISTA — a tela mostra o que está valendo agora.

A TEMPERATURA ATUAL É A ÚLTIMA LEITURA. Um campo "temperatura atual" que
alguém edita guarda o número digitado por último e não diz quando: "está a
-18°C" sem data é uma afirmação sobre o passado que se lê como presente.
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.services.exceptions import DomainError
from apps.estoque.models import LoteProduto
from apps.polpa.models import (
    Camara, LeituraTemperatura, LoteArmazenado, Posicao,
)
from apps.polpa.services.armazenagem import JANELA_ALERTA, ArmazenagemService

ZERO = Decimal('0')


class FrioService:

    # ── Temperatura ──────────────────────────────────────────────────────

    @staticmethod
    @transaction.atomic
    def registrar_leitura(camara: Camara, temperatura, usuario=None, dados=None):
        """
        Grava uma medição e avisa quando ela sai da faixa.

        O AVISO SAI NA HORA DO REGISTRO, e não numa varredura noturna: uma
        câmara que subiu às 3h40 precisa ser vista às 3h45, não amanhã. E o
        aviso é EVENTO — não se desliga sozinho quando a temperatura volta,
        porque o que estava dentro já passou por aquilo.
        """
        dados = dados or {}
        if temperatura is None:
            raise DomainError('Informe a temperatura medida.')

        leitura = LeituraTemperatura.objects.create(
            filial=camara.filial,
            camara=camara,
            temperatura=temperatura,
            medida_em=dados.get('medida_em') or timezone.now(),
            medido_por=usuario,
            observacao=(dados.get('observacao') or '')[:160],
        )
        if leitura.fora_da_faixa:
            FrioService._avisar_temperatura(leitura)
        return leitura

    @staticmethod
    def _avisar_temperatura(leitura: LeituraTemperatura) -> bool:
        """
        Manda o desvio para onde a fábrica olha.

        NÃO ESTOURA: falhar aqui impediria registrar a leitura, e o que se
        perde ao travar é justamente o registro do desvio.
        """
        from apps.core.models import Notificacao

        try:
            camara = leitura.camara
            Notificacao.objects.create(
                filial=leitura.filial,
                tipo=Notificacao.Tipo.ALERTA_SISTEMA,
                titulo=f'{camara.nome}: {leitura.temperatura}°C fora da faixa',
                mensagem=(
                    f'Faixa da câmara: {camara.faixa or "não definida"}. '
                    f'Desvio de {leitura.desvio}°C, medido em '
                    f'{timezone.localtime(leitura.medida_em):%d/%m %H:%M}.'
                )[:500],
                # A REFERÊNCIA É A LEITURA, e não a câmara: cada desvio é um
                # fato próprio, e usar a câmara faria o segundo desvio do dia
                # sobrescrever o primeiro pela restrição de unicidade.
                referencia_tipo='polpa.LeituraTemperatura',
                referencia_id=str(leitura.pk),
            )
            return True
        except Exception:  # noqa: BLE001 — aviso não derruba o registro
            return False

    @staticmethod
    def temperatura_atual(camara: Camara):
        """A última leitura desta câmara — ou `None` se nunca mediram."""
        return camara.leituras.order_by('-medida_em').first()

    @classmethod
    def painel_temperatura(cls, filial) -> list[dict]:
        """Cada câmara com a última leitura e a situação dela."""
        linhas = []
        for camara in Camara.objects.for_filial(filial).filter(ativo=True):
            leitura = cls.temperatura_atual(camara)
            linhas.append({
                'camara': camara,
                'leitura': leitura,
                # SEM LEITURA NÃO É "TUDO BEM": é câmara que ninguém mede, e
                # a tela precisa dizer isso com a mesma clareza de um desvio.
                'sem_leitura': leitura is None,
                'fora_da_faixa': bool(leitura and leitura.fora_da_faixa),
                'desvio': leitura.desvio if leitura else None,
            })
        return linhas

    # ── Posições ─────────────────────────────────────────────────────────

    @staticmethod
    @transaction.atomic
    def mover(armazenado: LoteArmazenado, posicao: Posicao, usuario=None, motivo=''):
        """
        Move o lote para outra posição — e pode mudar de câmara junto.

        O HISTÓRICO NÃO É GRAVADO AQUI porque já existe: `LoteArmazenado`
        está registrado na auditoria do vertical, e cada movimento grava
        quem, quando e o de-para campo a campo. Uma tabela de movimentos
        própria contaria a mesma história um pouco diferente, e a segunda
        versão é a que envelhece.
        """
        if posicao.filial_id != armazenado.filial_id:
            raise DomainError('A posição é de outra filial.')
        if not posicao.ativo:
            raise DomainError(
                f'A posição {posicao.codigo} está inativa — escolha outra.'
            )

        armazenado.posicao = posicao
        armazenado.camara = posicao.camara
        armazenado.endereco = posicao.codigo[:40]
        if motivo:
            armazenado.observacao = (
                f'{armazenado.observacao}\n{timezone.localdate():%d/%m}: {motivo}'
            ).strip()
        armazenado.save()
        return armazenado

    @staticmethod
    def mapa(camara: Camara) -> list[dict]:
        """
        As posições da câmara com o que está em cada uma.

        LIVRE É INFORMAÇÃO. Quem chega com o pallet precisa saber onde
        colocar sem procurar de porta aberta — e uma lista só do que está
        ocupado não responde isso.
        """
        linhas = []
        for posicao in camara.posicoes.filter(ativo=True).prefetch_related(
            'lotes__lote__produto',
        ):
            lotes = list(posicao.lotes.all())
            linhas.append({
                'posicao': posicao,
                'lotes': lotes,
                'livre': not lotes,
                'peso': posicao.peso_ocupado,
                'ocupacao': posicao.ocupacao,
            })
        return linhas

    # ── Alertas ──────────────────────────────────────────────────────────

    @classmethod
    def alertas(cls, filial) -> dict:
        """
        Os quatro alertas da cadeia de frio, cada um com o que resolver.

        SÃO LISTAS, e não contadores. "3 problemas" não faz ninguém se
        mexer; "o lote PA-77 vence em 4 dias na Câmara 1" faz — e é a
        diferença entre um painel que se olha e um que se ignora.
        """
        return {
            'temperatura': cls._alerta_temperatura(filial),
            'vencimento': cls._alerta_vencimento(filial),
            'capacidade': cls._alerta_capacidade(filial),
            'bloqueados': cls._alerta_bloqueados(filial),
        }

    @classmethod
    def _alerta_temperatura(cls, filial) -> list[dict]:
        """Câmaras cuja última leitura está fora da faixa — ou sem leitura."""
        return [
            linha for linha in cls.painel_temperatura(filial)
            if linha['fora_da_faixa'] or linha['sem_leitura']
        ]

    @staticmethod
    def _alerta_vencimento(filial) -> list[dict]:
        """Lotes vencidos ou dentro da janela de decisão."""
        return [
            linha for linha in ArmazenagemService.estoque(filial)
            if linha['situacao'] in ('vencido', 'vencendo')
        ]

    @staticmethod
    def _alerta_capacidade(filial) -> list[dict]:
        """
        Câmaras acima da capacidade declarada.

        Sem capacidade cadastrada a câmara NÃO entra na lista: não dá para
        afirmar que estourou o que ninguém mediu, e um alerta baseado em
        palpite é o que faz a fábrica desligar os alertas.
        """
        return [
            linha for linha in ArmazenagemService.por_camara(filial)
            if linha['ocupacao'] is not None and linha['ocupacao'] > 100
        ]

    @staticmethod
    def _alerta_bloqueados(filial):
        """
        Lotes bloqueados que ainda ocupam câmara.

        Bloqueado com saldo é espaço parado esperando decisão -- descarte,
        reprocesso ou liberação. Some da lista quando o saldo zera, porque
        aí ele não ocupa mais nada.
        """
        return (
            LoteProduto.objects
            .filter(
                filial=filial, status=LoteProduto.Status.BLOQUEADO,
                quantidade_atual__gt=0,
            )
            .select_related('produto', 'armazenamento_polpa__camara')
            .order_by('data_validade')
        )

    @classmethod
    def resumo_alertas(cls, filial) -> dict:
        alertas = cls.alertas(filial)
        return {
            'temperatura': len(alertas['temperatura']),
            'vencimento': len(alertas['vencimento']),
            'capacidade': len(alertas['capacidade']),
            'bloqueados': alertas['bloqueados'].count(),
            'janela': JANELA_ALERTA,
            'total': (
                len(alertas['temperatura']) + len(alertas['vencimento'])
                + len(alertas['capacidade']) + alertas['bloqueados'].count()
            ),
        }
