"""
Serializers do módulo de Mapas.

Os marcadores usam um payload deliberadamente enxuto e uniforme entre as
camadas: o mapa pode receber milhares de pontos por carga, e cada campo extra
multiplica pelo número de marcadores. Os dados ricos do popup (última compra,
crédito, dias sem comprar) vêm num segundo request, só do marcador clicado.
"""
from rest_framework import serializers


class MarcadorSerializer(serializers.Serializer):
    """Payload mínimo de um pino no mapa."""

    id = serializers.IntegerField()
    nome = serializers.CharField()
    lat = serializers.FloatField()
    lng = serializers.FloatField()
    # Cor efetiva do pino: normalmente a da camada, mas clientes usam a do
    # status de recompra (vermelho = atrasado), conforme §9.
    cor = serializers.CharField(required=False, allow_blank=True)
    cidade = serializers.CharField(required=False, allow_blank=True)


class ClienteProximoSerializer(serializers.Serializer):
    """
    Cliente devolvido pela busca por proximidade (§7 e §8).

    Junta a distância calculada no banco com os indicadores de recompra que o
    módulo de CRM mantém — é essa combinação que responde "vale a pena passar
    lá agora?".
    """

    id = serializers.IntegerField()
    nome = serializers.CharField()
    cpf_cnpj = serializers.CharField(allow_blank=True)
    cidade = serializers.CharField(allow_blank=True)
    bairro = serializers.CharField(allow_blank=True)
    telefone = serializers.CharField(allow_blank=True)
    whatsapp = serializers.CharField(allow_blank=True)
    lat = serializers.FloatField()
    lng = serializers.FloatField()
    distancia_m = serializers.IntegerField()
    distancia_texto = serializers.CharField()
    # Enriquecimento do CRM — ausente quando o cliente não tem padrão ainda.
    ultima_compra = serializers.DateField(allow_null=True)
    dias_sem_comprar = serializers.IntegerField(allow_null=True)
    valor_medio = serializers.DecimalField(max_digits=14, decimal_places=2, allow_null=True)
    frequencia = serializers.CharField(allow_blank=True)
    status_recompra = serializers.CharField(allow_blank=True)
    score = serializers.IntegerField(allow_null=True)


def formatar_distancia(metros: float) -> str:
    """320 -> '320 m'; 1234 -> '1,2 km' (formato do exemplo da especificação)."""
    metros = float(metros or 0)
    if metros < 1000:
        return f'{int(round(metros))} m'
    return f'{metros / 1000:.1f}'.replace('.', ',') + ' km'


def serializar_cliente_proximo(cliente) -> dict:
    """Achata Cliente + `.recompra` no formato do `ClienteProximoSerializer`."""
    r = getattr(cliente, 'recompra', None)
    distancia = getattr(cliente, 'distancia_m', 0) or 0

    return {
        'id': cliente.pk,
        'nome': cliente.nome_fantasia or cliente.razao_social or f'Cliente {cliente.pk}',
        'cpf_cnpj': cliente.cpf_cnpj or '',
        'cidade': cliente.cidade or '',
        'bairro': cliente.bairro or '',
        'telefone': cliente.celular or cliente.telefone or '',
        'whatsapp': _whatsapp(cliente),
        'lat': cliente.latitude,
        'lng': cliente.longitude,
        'distancia_m': int(round(distancia)),
        'distancia_texto': formatar_distancia(distancia),
        'ultima_compra': r.ultima_compra if r else None,
        'dias_sem_comprar': r.dias_desde_ultima_compra if r else None,
        'valor_medio': r.valor_medio if r else None,
        'frequencia': r.get_frequencia_display() if r else '',
        'status_recompra': r.status if r else '',
        'score': r.score if r else None,
    }


def _whatsapp(cliente) -> str:
    """Telefone em dígitos com 55 na frente, pronto para link wa.me."""
    bruto = cliente.celular or cliente.telefone or ''
    digitos = ''.join(filter(str.isdigit, bruto))
    if len(digitos) < 10:
        return ''
    return digitos if digitos.startswith('55') else f'55{digitos}'
