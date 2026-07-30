"""
Parâmetros do módulo de Mapas e Geolocalização.

Único arquivo a mexer para ajustar cores das camadas, raios oferecidos na
tela ou os limites de throttle da geocodificação.
"""

# ── Camadas do mapa ──────────────────────────────────────────────────────
# chave -> (rótulo, cor do marcador, ícone). A cor é usada tanto na legenda
# do menu lateral quanto no divIcon do Leaflet, então fica só aqui.
CAMADAS = {
    'clientes':       ('Clientes',       '#3b82f6', 'store'),
    'fornecedores':   ('Fornecedores',   '#f59e0b', 'truck'),
    'filiais':        ('Filiais',        '#22c55e', 'building'),
    'motoristas':     ('Motoristas',     '#a855f7', 'user'),
    'transportadoras': ('Transportadoras', '#06b6d4', 'shipping'),
    'entregas':       ('Pedidos em entrega', '#ef4444', 'package'),
}

# Situação de recompra -> cor, reaproveitando a semântica já usada no CRM
# (apps.crm.models.RecompraCliente.Status) para o mapa não inventar outra.
CORES_STATUS_RECOMPRA = {
    'vermelho': '#ef4444',
    'amarelo':  '#f59e0b',
    'verde':    '#22c55e',
    'cinza':    '#6b7280',
}

# ── Busca por proximidade ────────────────────────────────────────────────
RAIOS_OFERECIDOS_M = [1000, 3000, 5000, 10000]
RAIO_PADRAO_M = 3000
RAIO_MAXIMO_M = 50000        # teto de segurança da API
LIMITE_PROXIMIDADE = 100     # nº máximo de registros devolvidos

# Limite de marcadores por camada numa carga do mapa. Acima disso a resposta
# vem truncada e avisa — evita mandar 50k pontos para o browser.
LIMITE_MARCADORES = 3000

# ── Geocodificação ───────────────────────────────────────────────────────
# Intervalo mínimo entre requisições ao provider, em segundos. O Nominatim
# público exige >= 1s; providers comerciais aceitam bem mais.
GEOCODER_INTERVALO_S = 1.1
GEOCODER_TIMEOUT_S = 10
# Tentativas por endereço antes de marcar erro e parar de insistir.
GEOCODER_MAX_TENTATIVAS = 3
# Tamanho do lote do comando de backfill.
GEOCODER_LOTE_PADRAO = 200

# Caixa envolvente do Brasil — descarta coordenada que o provider devolveu
# fora do país (acontece com endereço ambíguo do tipo "Natal" -> África do Sul).
BRASIL_BBOX = {
    'lat_min': -34.0, 'lat_max': 5.5,
    'lng_min': -74.0, 'lng_max': -34.0,
}


def dentro_do_brasil(lat: float, lng: float) -> bool:
    b = BRASIL_BBOX
    return b['lat_min'] <= lat <= b['lat_max'] and b['lng_min'] <= lng <= b['lng_max']
