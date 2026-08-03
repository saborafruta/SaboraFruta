"""
Rastreamento em tempo real (§13).

Recebe a posição, atualiza a última conhecida, decide se vale guardar no
histórico e devolve o que o mapa ao vivo precisa mostrar.

A entrada de posição é a mesma do §12 (cercas): uma posição alimenta os dois.
Separar em dois endpoints faria o celular mandar tudo duas vezes.
"""
from __future__ import annotations

import datetime

from django.utils import timezone

from apps.mapas.services.otimizacao import distancia_haversine_m

# Filtro do histórico: só grava um ponto novo se o motorista andou pelo menos
# isto, ou se faz tempo demais desde o último. Sem o filtro, um veículo parado
# no semáforo geraria centenas de pontos idênticos.
DISTANCIA_MINIMA_M = 60
INTERVALO_MAXIMO_S = 180

# Acima disso a posição é velha demais para ser "tempo real". O mapa mostra o
# motorista em cinza em vez de escondê-lo: sumir da tela seria lido como
# "acabou o turno", quando pode ser só falta de sinal.
LIMITE_ONLINE_S = 300

# Expurgo padrão do histórico. O trajeto interessa por alguns dias; guardar
# meses multiplicaria a tabela sem ninguém consultar.
RETENCAO_PADRAO_DIAS = 30


class RastreioService:
    """Posições ao vivo, histórico de percurso e expurgo."""

    @classmethod
    def _escopo(cls, filial):
        from apps.mapas.services import ProximidadeService

        return ProximidadeService._escopo_filiais(filial)

    @classmethod
    def registrar(cls, *, filial, motorista, latitude, longitude,
                  velocidade_kmh=None, precisao_m=None, momento=None,
                  destino_venda_id=None, destino_cliente_id=None):
        """
        Atualiza a posição do motorista e devolve o registro.

        Quando o aparelho não informa velocidade — comum em celular parado ou
        com GPS fraco —, ela é calculada entre esta posição e a anterior. Se
        nem isso for possível, fica nula: um zero seria lido como "parado",
        que é uma afirmação diferente de "não sei".
        """
        from apps.mapas.models import PosicaoMotorista

        momento = momento or timezone.now()
        anterior = PosicaoMotorista.objects.filter(motorista=motorista).first()

        if velocidade_kmh is None:
            velocidade_kmh = cls._velocidade_entre(
                anterior, latitude, longitude, momento)

        posicao, _ = PosicaoMotorista.objects.update_or_create(
            motorista=motorista,
            defaults={
                'filial': filial,
                'latitude': latitude, 'longitude': longitude,
                'momento': momento,
                'velocidade_kmh': velocidade_kmh,
                'precisao_m': precisao_m,
                'destino_venda_id': destino_venda_id,
                'destino_cliente_id': destino_cliente_id,
            },
        )
        cls._talvez_gravar_percurso(
            filial, motorista, latitude, longitude, momento, velocidade_kmh)
        return posicao

    @staticmethod
    def _velocidade_entre(anterior, latitude, longitude, momento):
        """km/h entre duas posições; None quando não dá para afirmar."""
        if anterior is None:
            return None
        segundos = (momento - anterior.momento).total_seconds()
        # Intervalo muito curto: o erro do GPS domina a conta e produziria
        # velocidades absurdas para quem está parado.
        if segundos < 5 or segundos > 3600:
            return None

        metros = distancia_haversine_m(
            (anterior.latitude, anterior.longitude), (latitude, longitude))
        return round(metros / segundos * 3.6, 1)

    @classmethod
    def _talvez_gravar_percurso(cls, filial, motorista, latitude, longitude,
                                momento, velocidade_kmh):
        """Grava no histórico só quando andou o bastante ou faz tempo demais."""
        from apps.mapas.models import PontoPercurso

        ultimo = (
            PontoPercurso.objects
            .filter(motorista=motorista).order_by('-momento').first()
        )
        if ultimo is not None:
            andou = distancia_haversine_m(
                (ultimo.latitude, ultimo.longitude), (latitude, longitude))
            esperou = (momento - ultimo.momento).total_seconds()
            if andou < DISTANCIA_MINIMA_M and esperou < INTERVALO_MAXIMO_S:
                return None

        return PontoPercurso.objects.create(
            motorista=motorista, filial=filial,
            latitude=latitude, longitude=longitude,
            momento=momento, velocidade_kmh=velocidade_kmh,
        )

    @classmethod
    def ao_vivo(cls, filial, *, limite_s=LIMITE_ONLINE_S):
        """
        Motoristas com posição conhecida, para o mapa ao vivo.

        Traz também quem está desatualizado, marcado como tal. Esconder quem
        parou de reportar faria o veículo sumir da tela sem explicação — e
        "sumiu" é ambíguo entre fim de turno e falta de sinal.
        """
        from apps.mapas.models import PosicaoMotorista

        agora = timezone.now()
        qs = (
            PosicaoMotorista.objects
            .filter(filial__in=cls._escopo(filial))
            .select_related('motorista', 'destino_cliente',
                            'destino_venda', 'destino_venda__cliente')
            .order_by('-momento')
        )

        motoristas = []
        for p in qs:
            atraso = int((agora - p.momento).total_seconds())
            destino = p.destino
            motoristas.append({
                'motorista_id': p.motorista_id,
                'nome': p.motorista.nome,
                'lat': p.latitude,
                'lng': p.longitude,
                'velocidade_kmh': p.velocidade_kmh,
                'precisao_m': p.precisao_m,
                'momento': timezone.localtime(p.momento).isoformat(),
                'atraso_s': atraso,
                'atraso_texto': cls.tempo_desde(atraso),
                'online': atraso <= limite_s,
                'destino': (
                    {
                        'id': destino.pk,
                        'nome': (destino.nome_fantasia or destino.razao_social
                                 or f'Cliente {destino.pk}'),
                        'lat': destino.latitude,
                        'lng': destino.longitude,
                    }
                    if destino is not None else None
                ),
                'venda_id': p.destino_venda_id,
            })
        return motoristas

    @classmethod
    def percurso(cls, filial, motorista_id, *, inicio=None, fim=None):
        """Pontos do trajeto de um motorista, para desenhar a linha."""
        from apps.mapas.models import PontoPercurso
        from apps.mapas.services.painel import PainelService

        inicio, fim = PainelService.periodo_padrao(inicio, fim)
        pontos = (
            PontoPercurso.objects
            .filter(filial__in=cls._escopo(filial), motorista_id=motorista_id,
                    momento__date__gte=inicio, momento__date__lte=fim)
            .order_by('momento')
            .values_list('latitude', 'longitude', 'momento', 'velocidade_kmh')
        )

        linha = [[lat, lng] for lat, lng, _, _ in pontos]
        km = sum(
            distancia_haversine_m(linha[i - 1], linha[i])
            for i in range(1, len(linha))
        ) / 1000

        velocidades = [v for _, _, _, v in pontos if v]
        return {
            'inicio': inicio.isoformat(),
            'fim': fim.isoformat(),
            'pontos': linha,
            'total_pontos': len(linha),
            'km': round(km, 1),
            'velocidade_media_kmh': (
                round(sum(velocidades) / len(velocidades), 1) if velocidades else None
            ),
            'velocidade_maxima_kmh': round(max(velocidades), 1) if velocidades else None,
        }

    @staticmethod
    def tempo_desde(segundos):
        """`45` -> `'agora'`; `300` -> `'5 min'`; `7200` -> `'2 h'`."""
        segundos = int(segundos or 0)
        if segundos < 60:
            return 'agora'
        if segundos < 3600:
            return f'{segundos // 60} min'
        if segundos < 86400:
            return f'{segundos // 3600} h'
        return f'{segundos // 86400} d'

    @classmethod
    def limpar_motorista(cls, filial, motorista_id):
        """
        Apaga a posição atual e o percurso de um motorista.

        Serve para descartar um teste ou zerar quem saiu da equipe. Os eventos
        de cerca (§12) **não** são tocados: eles registram visitas que de fato
        aconteceram e podem já estar num relatório impresso — apagá-los de
        carona num botão de rastreio seria destruir dado que ninguém pediu
        para destruir.
        """
        from apps.mapas.models import PontoPercurso, PosicaoMotorista

        filiais = cls._escopo(filial)
        posicoes, _ = PosicaoMotorista.objects.filter(
            motorista_id=motorista_id, filial__in=filiais).delete()
        pontos, _ = PontoPercurso.objects.filter(
            motorista_id=motorista_id, filial__in=filiais).delete()
        return {'posicoes': posicoes, 'pontos': pontos}

    @classmethod
    def expurgar(cls, dias=RETENCAO_PADRAO_DIAS):
        """Apaga pontos de percurso mais antigos que `dias`."""
        from apps.mapas.models import PontoPercurso

        corte = timezone.localdate() - datetime.timedelta(days=dias)
        apagados, _ = PontoPercurso.objects.filter(momento__date__lt=corte).delete()
        return apagados
