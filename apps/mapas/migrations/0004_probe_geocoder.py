"""
Sondagem da configuração do geocoder em produção.

Não altera schema nem dados. Imprime no stdout (que o Railway captura como log
do deploy) qual provider foi resolvido a partir das variáveis de ambiente e o
resultado de UMA chamada real, para confirmar que a chave é válida.

A chave NUNCA é impressa — só se está presente e o seu tamanho. Log de deploy
é retido e costuma ser compartilhado.

Toda a parte de rede está dentro de try/except: uma indisponibilidade do
provider não pode fazer o `migrate` do releaseCommand falhar, porque isso
derrubaria o site.

Pode ser removida depois de lida; é um no-op idempotente.
"""
from django.db import migrations

ENDERECO = 'Av Capitao Mor Gouveia, 3005, Lagoa Nova, Natal, RN, Brasil'


def _sondar(apps, schema_editor):
    from django.conf import settings

    nome = getattr(settings, 'MAPAS_GEOCODER', 'nominatim')
    chave = getattr(settings, 'MAPAS_GEOCODER_API_KEY', '') or ''
    url_propria = getattr(settings, 'MAPAS_NOMINATIM_URL', '') or ''

    print('[PROBE-GEOCODER] MAPAS_GEOCODER         =', nome)
    print('[PROBE-GEOCODER] MAPAS_GEOCODER_API_KEY =',
          f'presente ({len(chave)} caracteres)' if chave else 'AUSENTE')
    print('[PROBE-GEOCODER] MAPAS_NOMINATIM_URL    =', url_propria or '(padrao publico)')

    try:
        # construir_geocoder() só lê settings: não importa models, então é
        # seguro chamar de dentro de uma migration.
        from apps.mapas.services.geocoder import construir_geocoder

        geocoder = construir_geocoder()
        print('[PROBE-GEOCODER] provider resolvido    =', type(geocoder).__name__)
        print('[PROBE-GEOCODER] uso comercial liberado=', geocoder.permite_uso_comercial)

        if type(geocoder).__name__ == 'NominatimGeocoder' and nome.lower() != 'nominatim':
            print('[PROBE-GEOCODER] ATENCAO: caiu para Nominatim -- chave ausente ou '
                  'MAPAS_GEOCODER com valor invalido.')

        res = geocoder.geocodificar(ENDERECO)
        if res.ok:
            print(f'[PROBE-GEOCODER] TESTE OK: lat={res.latitude} lng={res.longitude} '
                  f'precisao={res.precisao}')
        else:
            print('[PROBE-GEOCODER] TESTE SEM RESULTADO:', res.erro)

    except Exception as exc:
        # Inclui o status HTTP quando houver: 401/403 = chave invalida,
        # 429 = quota estourada. É a informação que resolve o diagnóstico.
        status = getattr(getattr(exc, 'response', None), 'status_code', None)
        print(f'[PROBE-GEOCODER] TESTE FALHOU: {type(exc).__name__}: {exc}'
              + (f' (HTTP {status})' if status else ''))

    print('[PROBE-GEOCODER] fim (nada foi alterado)')


class Migration(migrations.Migration):

    dependencies = [
        ('mapas', '0003_cliente_territorio'),
    ]

    operations = [
        migrations.RunPython(_sondar, migrations.RunPython.noop),
    ]
