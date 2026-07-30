"""
Backfill de geocodificação.

    python manage.py geocodificar                    # todas as entidades
    python manage.py geocodificar --modelo cliente   # só clientes
    python manage.py geocodificar --limite 50 --dry-run

É aqui que as chamadas de rede acontecem (nunca no request — ver
`apps.mapas.signals`). Roda um processo só, o que torna o throttle por
processo suficiente para respeitar o limite do provider.

Pode ser chamado por um cron externo; não há Celery worker em produção.
"""
from django.core.management.base import BaseCommand, CommandError

from apps.mapas import constants as c
from apps.mapas.managers import pendentes_de_geocodificacao
from apps.mapas.services import GeocodificacaoService

#: apelido -> (app_label, ModelName)
MODELOS = {
    'cliente': ('cadastros', 'Cliente'),
    'cliente_endereco': ('cadastros', 'ClienteEndereco'),
    'fornecedor': ('cadastros', 'Fornecedor'),
    'transportadora': ('cadastros', 'Transportadora'),
    'motorista': ('cadastros', 'Motorista'),
    'filial': ('core', 'Filial'),
}


class Command(BaseCommand):
    help = 'Preenche latitude/longitude a partir do endereço cadastrado.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--modelo', choices=sorted(MODELOS), action='append',
            help='Restringe a um ou mais modelos (repetível). Padrão: todos.',
        )
        parser.add_argument(
            '--limite', type=int, default=c.GEOCODER_LOTE_PADRAO,
            help=f'Máximo de registros por modelo (padrão {c.GEOCODER_LOTE_PADRAO}).',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Só lista o que faria, sem chamar o provider nem gravar.',
        )
        parser.add_argument(
            '--forcar', action='store_true',
            help='Reprocessa também quem já tem coordenada (ignora o hash).',
        )

    def handle(self, *args, **opts):
        from django.apps import apps as django_apps

        alvos = opts['modelo'] or sorted(MODELOS)
        limite = max(1, opts['limite'])
        dry = opts['dry_run']

        servico = None if dry else GeocodificacaoService()
        if not dry:
            geo = servico.geocoder
            self.stdout.write(f'provider: {geo.nome}')
            if not geo.permite_uso_comercial:
                self.stdout.write(self.style.WARNING(
                    '  AVISO: este provider (instancia publica) NAO permite '
                    'geocodificacao em massa para uso comercial. Configure '
                    'MAPAS_GEOCODER/MAPAS_GEOCODER_API_KEY ou MAPAS_NOMINATIM_URL.'
                ))

        total_ok = total_falha = total_visto = 0

        for apelido in alvos:
            app_label, model_name = MODELOS[apelido]
            try:
                model = django_apps.get_model(app_label, model_name)
            except LookupError as exc:  # pragma: no cover
                raise CommandError(f'modelo {apelido} indisponivel: {exc}')

            candidatos = pendentes_de_geocodificacao(model.objects.all())
            processados = ok = falha = 0

            for obj in candidatos.iterator(chunk_size=200):
                if processados >= limite:
                    break
                if not opts['forcar'] and not obj.geo_desatualizado:
                    continue
                if not obj.endereco_para_geocodificar():
                    continue

                processados += 1
                total_visto += 1

                if dry:
                    self.stdout.write(
                        f'  [dry] {apelido}#{obj.pk}: {obj.endereco_para_geocodificar()}'
                    )
                    continue

                if servico.geocodificar_objeto(obj):
                    ok += 1
                else:
                    falha += 1
                    self.stdout.write(
                        f'  falha {apelido}#{obj.pk}: {obj.geo_erro}'
                    )

            total_ok += ok
            total_falha += falha
            self.stdout.write(
                f'{apelido}: {processados} processado(s), {ok} com coordenada, {falha} sem'
            )

        if dry:
            self.stdout.write(self.style.SUCCESS(f'dry-run: {total_visto} pendente(s)'))
        else:
            self.stdout.write(self.style.SUCCESS(
                f'concluido: {total_ok} geocodificado(s), {total_falha} falha(s)'
            ))
