"""
Diagnóstico da configuração de roteirização e otimização.

    python manage.py testar_rotas

Serve para confirmar que MAPAS_ROTA_PROVIDER / MAPAS_ROTA_API_KEY /
MAPAS_OSRM_URL foram aplicados no ambiente — sem precisar montar uma rota no
mapa para descobrir.

A chave NUNCA é impressa: só se está presente e o seu tamanho. Logs de deploy
ficam retidos e costumam ser compartilhados.
"""
from django.conf import settings
from django.core.management.base import BaseCommand

from apps.mapas.services.otimizacao import construir_otimizador
from apps.mapas.services.roteirizacao import construir_roteirizador

# Dois pontos em Natal/RN, ~5 km por rua. Distância conhecida o bastante para
# um resultado absurdo saltar aos olhos.
PONTOS = [(-5.7945, -35.2110), (-5.8320, -35.2050)]


class Command(BaseCommand):
    help = 'Mostra qual provider de rotas está ativo e faz uma chamada real.'

    def handle(self, *args, **opts):
        self._configuracao()
        self._rota()
        self._otimizacao()

    def _configuracao(self):
        chave = getattr(settings, 'MAPAS_ROTA_API_KEY', '') or ''
        self.stdout.write(self.style.MIGRATE_HEADING('Configuração'))
        self.stdout.write(
            f'  MAPAS_ROTA_PROVIDER = '
            f'{getattr(settings, "MAPAS_ROTA_PROVIDER", "osrm") or "osrm"}')
        # Presença e tamanho, nunca o valor.
        self.stdout.write(
            f'  MAPAS_ROTA_API_KEY  = '
            f'{"definida (" + str(len(chave)) + " caracteres)" if chave else "AUSENTE"}')
        self.stdout.write(
            f'  MAPAS_OSRM_URL      = '
            f'{getattr(settings, "MAPAS_OSRM_URL", "") or "(padrão: servidor público)"}')
        self.stdout.write(
            f'  MAPAS_VROOM_URL     = '
            f'{getattr(settings, "MAPAS_VROOM_URL", "") or "(não definida)"}')
        self.stdout.write('')

    def _rota(self):
        roteirizador = construir_roteirizador()
        self.stdout.write(self.style.MIGRATE_HEADING('Roteirização (§4)'))
        self.stdout.write(f'  Provider ativo: {roteirizador.nome}')

        if roteirizador.permite_uso_comercial:
            self.stdout.write(self.style.SUCCESS('  Uso comercial: liberado'))
        else:
            self.stdout.write(self.style.WARNING(
                '  Uso comercial: NÃO liberado — servidor público, só para testes.\n'
                '  Configure MAPAS_ROTA_PROVIDER=openrouteservice + '
                'MAPAS_ROTA_API_KEY,\n  ou aponte MAPAS_OSRM_URL para uma '
                'instância própria.'))

        self.stdout.write('  Chamando o provider...')
        try:
            rota = roteirizador.rota(PONTOS)
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f'  FALHOU: {exc}'))
            self.stdout.write(self.style.WARNING(
                '  Chave inválida, quota estourada ou provider fora do ar. '
                'A tela cai\n  no traçado em linha reta quando isso acontece.'))
            return

        self.stdout.write(self.style.SUCCESS(
            f'  OK — {rota.distancia_km} km, {rota.duracao_texto}, '
            f'{len(rota.geometria)} pontos no traçado'))
        self.stdout.write('')

    def _otimizacao(self):
        otimizador = construir_otimizador()
        self.stdout.write(self.style.MIGRATE_HEADING('Otimização (§5)'))
        self.stdout.write(f'  Otimizador ativo: {otimizador.nome}')

        if otimizador.nome == 'local':
            self.stdout.write(self.style.WARNING(
                '  Usando o otimizador local (heurística em Python). Funciona,\n'
                '  mas o VROOM do OpenRouteService dá resultado melhor em\n'
                '  roteiros grandes — a mesma MAPAS_ROTA_API_KEY já o habilita.'))
        else:
            self.stdout.write(self.style.SUCCESS('  VROOM habilitado.'))
