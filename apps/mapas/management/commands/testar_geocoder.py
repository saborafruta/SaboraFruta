"""
Diagnóstico da configuração de geocodificação.

    python manage.py testar_geocoder
    python manage.py testar_geocoder --endereco "Av Paulista, 1000, Sao Paulo, SP"

Serve para confirmar que MAPAS_GEOCODER / MAPAS_GEOCODER_API_KEY foram
aplicados corretamente no ambiente — sem precisar rodar o backfill inteiro.

A chave NUNCA é impressa: só se ela está presente e o seu tamanho. Logs de
deploy são retidos e frequentemente compartilhados.
"""
from django.conf import settings
from django.core.management.base import BaseCommand

from apps.mapas import constants as c
from apps.mapas.services.geocoder import GeocodificacaoService, construir_geocoder

ENDERECO_PADRAO = 'Av Capitao Mor Gouveia, 3005, Lagoa Nova, Natal, RN, Brasil'


class Command(BaseCommand):
    help = 'Mostra qual provider de geocodificação está ativo e testa um endereço.'

    def add_arguments(self, parser):
        parser.add_argument('--endereco', default=ENDERECO_PADRAO)
        parser.add_argument(
            '--sem-cache', action='store_true',
            help='Ignora o cache e força uma chamada real ao provider.',
        )

    def handle(self, *args, **opts):
        configurado = getattr(settings, 'MAPAS_GEOCODER', 'nominatim')
        chave = getattr(settings, 'MAPAS_GEOCODER_API_KEY', '') or ''
        url_propria = getattr(settings, 'MAPAS_NOMINATIM_URL', '') or ''

        self.stdout.write('── Configuração ──')
        self.stdout.write(f'  MAPAS_GEOCODER          = {configurado}')
        self.stdout.write(
            f'  MAPAS_GEOCODER_API_KEY  = '
            + (f'presente ({len(chave)} caracteres)' if chave else 'AUSENTE')
        )
        self.stdout.write(f'  MAPAS_NOMINATIM_URL     = {url_propria or "(padrão público)"}')

        geocoder = construir_geocoder()
        self.stdout.write('── Provider resolvido ──')
        self.stdout.write(f'  classe   : {type(geocoder).__name__}')
        self.stdout.write(f'  nome     : {geocoder.nome}')

        # O interruptor do ajuste automatico ao salvar. Se estiver desligado
        # em producao, o cadastro nunca entra no mapa sozinho -- e isso e
        # invisivel na tela, so aparece aqui.
        if getattr(settings, 'MAPAS_GEOCODIFICAR_AO_SALVAR', True):
            self.stdout.write(self.style.SUCCESS(
                '  ajuste automatico ao salvar: LIGADO'))
        else:
            self.stdout.write(self.style.WARNING(
                '  ajuste automatico ao salvar: DESLIGADO '
                '(MAPAS_GEOCODIFICAR_AO_SALVAR=false).'))
            self.stdout.write(self.style.WARNING(
                '  Os cadastros ficam pendentes para o "manage.py geocodificar".'))

        # O aviso mais importante do comando: dizer se a configuração atual
        # pode ser usada legalmente para geocodificar a base inteira.
        if geocoder.permite_uso_comercial:
            self.stdout.write(self.style.SUCCESS('  uso comercial em massa: LIBERADO'))
        else:
            self.stdout.write(self.style.ERROR('  uso comercial em massa: NAO LIBERADO'))
            if configurado.lower() in ('locationiq', 'geoapify') and not chave:
                self.stdout.write(self.style.WARNING(
                    f'  causa: MAPAS_GEOCODER={configurado} mas sem '
                    'MAPAS_GEOCODER_API_KEY -- caiu para o Nominatim publico.'
                ))
            else:
                self.stdout.write(
                    '  causa: instancia publica do Nominatim proibe geocodificacao\n'
                    '         sistematica/em massa. Defina MAPAS_GEOCODER=locationiq\n'
                    '         (ou geoapify) + MAPAS_GEOCODER_API_KEY, ou aponte\n'
                    '         MAPAS_NOMINATIM_URL para uma instancia propria.'
                )

        endereco = opts['endereco']
        self.stdout.write('── Teste ──')
        self.stdout.write(f'  endereco: {endereco}')

        servico = GeocodificacaoService(geocoder=geocoder)
        import hashlib

        chave_cache = hashlib.md5(endereco.lower().encode('utf-8')).hexdigest()
        if opts['sem_cache']:
            from apps.mapas.models import CacheGeocodificacao

            CacheGeocodificacao.objects.filter(pk=chave_cache).delete()

        res = servico.resolver(endereco, chave_cache)
        if res.ok:
            self.stdout.write(self.style.SUCCESS(
                f'  OK: lat={res.latitude} lng={res.longitude} precisao={res.precisao}'
            ))
            if not c.dentro_do_brasil(res.latitude, res.longitude):
                self.stdout.write(self.style.WARNING('  (fora do bbox do Brasil!)'))
        else:
            self.stdout.write(self.style.ERROR(f'  FALHOU: {res.erro}'))
