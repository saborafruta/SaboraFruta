"""
Detecção de entrada e saída em cercas virtuais (§12).

O serviço recebe uma posição e decide o que mudou. Toda a lógica está aqui, e
não no endpoint, porque a fonte de posição vai variar — hoje o navegador do
motorista, amanhã talvez um rastreador do veículo — e a regra de disparo não
pode depender de quem contou onde ele está.
"""
from __future__ import annotations

from django.db import transaction
from django.db.models import F, Window
from django.db.models.functions import RowNumber
from django.utils import timezone

from apps.mapas.services.otimizacao import distancia_haversine_m

# Margem de saída: entra ao cruzar o raio, mas só sai depois de se afastar mais
# um tanto. Sem isso, um motorista parado exatamente na borda geraria uma
# enxurrada de entrada/saída — o GPS de celular oscila dezenas de metros
# parado, e o relatório viraria ruído em vez de registro de visita.
MARGEM_SAIDA_M = 60

# Quanto o ponto pode estar longe de qualquer cerca e ainda ser considerado.
# Recorte grosso por bounding box antes da conta de distância, para não medir
# a distância até todas as cercas da empresa a cada posição recebida.
GRAU_POR_KM = 1 / 111.0


class GeofenceService:
    """Processa posições e registra as travessias."""

    @classmethod
    def _escopo(cls, filial):
        from apps.mapas.services import ProximidadeService

        return ProximidadeService._escopo_filiais(filial)

    @classmethod
    def cercas_candidatas(cls, filial, latitude, longitude, raio_busca_m=20000):
        """
        Cercas que podem conter o ponto, por recorte retangular.

        O retângulo é generoso de propósito: ele só evita a conta de distância
        para cercas obviamente distantes. Quem decide dentro/fora é a
        haversine, logo em seguida.
        """
        from apps.mapas.models import Geofence

        delta = raio_busca_m / 1000 * GRAU_POR_KM
        return Geofence.objects.filter(
            filial__in=cls._escopo(filial), ativo=True,
            latitude__gte=latitude - delta, latitude__lte=latitude + delta,
            longitude__gte=longitude - delta, longitude__lte=longitude + delta,
        )

    @classmethod
    def dentro_de(cls, geofence, latitude, longitude, *, ja_dentro=False):
        """
        O ponto está dentro da cerca?

        `ja_dentro` aplica a histerese: quem já estava dentro só é considerado
        fora depois de passar do raio **mais a margem**. É o que impede o
        vaivém na borda.
        """
        distancia = distancia_haversine_m(
            (geofence.latitude, geofence.longitude), (latitude, longitude),
        )
        limite = geofence.raio_m + (MARGEM_SAIDA_M if ja_dentro else 0)
        return distancia <= limite, distancia

    @classmethod
    def estado_atual(cls, motorista, geofences):
        """
        `{geofence_id: True/False}` — quem o motorista já estava dentro.

        Deduzido do último evento de cada cerca, em uma query só. É por isso
        que não existe tabela de posições: dois registros por visita respondem
        o que milhares de pings responderiam.
        """
        from apps.mapas.models import EventoGeofence

        ids = [g.pk for g in geofences]
        if not ids:
            return {}

        # O último evento de cada cerca para este motorista, numa query só --
        # o que seria um loop de N queries.
        #
        # NUMERA E PEGA O PRIMEIRO, em vez de `distinct on`: aquilo só existe no
        # PostgreSQL, e derrubava os testes inteiros deste módulo com
        # "DISTINCT ON fields is not supported by this database backend". A
        # janela resolve no banco do mesmo jeito e roda nos dois.
        ultimos = (
            EventoGeofence.objects
            .filter(motorista=motorista, geofence_id__in=ids)
            .annotate(_ordem=Window(
                expression=RowNumber(),
                partition_by=[F('geofence_id')],
                order_by=[F('momento').desc(), F('id').desc()],
            ))
            .filter(_ordem=1)
            .values_list('geofence_id', 'tipo')
        )
        return {
            gid: (tipo == EventoGeofence.Tipo.ENTRADA) for gid, tipo in ultimos
        }

    @classmethod
    @transaction.atomic
    def processar_posicao(cls, *, filial, motorista, latitude, longitude,
                          momento=None):
        """
        Registra as travessias que esta posição provoca.

        Devolve a lista de eventos criados — normalmente vazia, porque a maior
        parte das posições não muda nada. Só há evento quando o estado vira.
        """
        from apps.mapas.models import EventoGeofence

        momento = momento or timezone.now()
        cercas = list(cls.cercas_candidatas(filial, latitude, longitude))
        if not cercas:
            return []

        estado = cls.estado_atual(motorista, cercas)
        novos = []

        for cerca in cercas:
            antes = estado.get(cerca.pk, False)
            agora, distancia = cls.dentro_de(
                cerca, latitude, longitude, ja_dentro=antes,
            )
            if agora == antes:
                continue

            novos.append(EventoGeofence(
                geofence=cerca, motorista=motorista,
                tipo=(EventoGeofence.Tipo.ENTRADA if agora
                      else EventoGeofence.Tipo.SAIDA),
                momento=momento, latitude=latitude, longitude=longitude,
                distancia_m=int(round(distancia)),
            ))

        if novos:
            EventoGeofence.objects.bulk_create(novos)
        return novos

    @classmethod
    def visitas(cls, filial, *, inicio=None, fim=None, geofence_id=None,
                motorista_id=None):
        """
        Eventos emparelhados em visitas: entrada, saída e permanência.

        Uma lista crua de eventos obriga quem lê a casar as linhas na mão. A
        pergunta real é "quanto tempo o motorista ficou lá", e ela só existe
        no par.

        Entrada sem saída correspondente aparece como visita **em aberto** —
        ou o motorista ainda está lá, ou o rastreamento parou no meio. Omitir
        a linha esconderia as duas situações.
        """
        from apps.mapas.models import EventoGeofence
        from apps.mapas.services.painel import PainelService

        inicio, fim = PainelService.periodo_padrao(inicio, fim)

        qs = (
            EventoGeofence.objects
            .filter(geofence__filial__in=cls._escopo(filial),
                    momento__date__gte=inicio, momento__date__lte=fim)
            .select_related('geofence', 'motorista')
            .order_by('geofence_id', 'motorista_id', 'momento', 'id')
        )
        if geofence_id:
            qs = qs.filter(geofence_id=geofence_id)
        if motorista_id:
            qs = qs.filter(motorista_id=motorista_id)

        visitas, abertas = [], {}
        for ev in qs:
            chave = (ev.geofence_id, ev.motorista_id)
            if ev.tipo == EventoGeofence.Tipo.ENTRADA:
                # Duas entradas seguidas não deveriam acontecer; se
                # acontecerem, a primeira vira visita em aberto em vez de ser
                # descartada em silêncio.
                if chave in abertas:
                    visitas.append(cls._visita(abertas.pop(chave), None))
                abertas[chave] = ev
            else:
                visitas.append(cls._visita(abertas.pop(chave, None), ev))

        for aberta in abertas.values():
            visitas.append(cls._visita(aberta, None))

        visitas.sort(key=lambda v: v['entrada'] or v['saida'], reverse=True)
        return {'inicio': inicio, 'fim': fim, 'visitas': visitas}

    @staticmethod
    def _visita(entrada, saida):
        from apps.mapas.services.painel import PainelService

        ref = entrada or saida
        segundos = None
        if entrada and saida:
            segundos = int((saida.momento - entrada.momento).total_seconds())

        return {
            'cerca': ref.geofence.nome,
            'motorista': ref.motorista.nome,
            'entrada': entrada.momento if entrada else None,
            'saida': saida.momento if saida else None,
            'permanencia_s': segundos,
            'permanencia': (PainelService.formatar_duracao(segundos)
                            if segundos is not None else '—'),
            # Sem entrada: o rastreamento começou com o motorista já dentro.
            'em_aberto': saida is None,
            'sem_entrada': entrada is None,
        }
