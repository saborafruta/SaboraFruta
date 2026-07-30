"""
Detecção de endereço alterado (§2 da especificação).

Estratégia deliberada: o signal **não** chama o geocoder. Ele só invalida a
coordenada, marcando o registro como pendente. A chamada HTTP fica para o
comando de backfill.

Por quê: geocodificar dentro do `post_save` colocaria uma requisição de rede
de até 10s (mais o throttle de 1,1s exigido pela política do provider) no
meio do salvamento de um cliente. Numa importação de 500 clientes isso somaria
mais de 10 minutos de request e estouraria o timeout de 120s do gunicorn — e
pior, uma queda do provider passaria a impedir o cadastro de clientes.
Invalidar é instantâneo e não pode falhar.
"""
import logging

from django.db.models.signals import pre_save

logger = logging.getLogger(__name__)

#: models que ganham geocodificação automática (todos herdam CoordenadaMixin)
_MODELS_GEO = (
    ('cadastros', 'Cliente'),
    ('cadastros', 'ClienteEndereco'),
    ('cadastros', 'Fornecedor'),
    ('cadastros', 'Transportadora'),
    ('cadastros', 'Motorista'),
    ('core', 'Filial'),
)


def _invalidar_se_endereco_mudou(sender, instance, **kwargs):
    """
    Zera a coordenada quando o endereço muda, para o backfill reprocessar.

    Coordenada ajustada à mão (`geo_fixado`) é preservada: o usuário arrastou
    o pino porque o geocoder errou, então o endereço textual não manda mais.
    """
    if instance.pk is None or getattr(instance, 'geo_fixado', False):
        return

    try:
        anterior = sender.objects.filter(pk=instance.pk).only(
            'endereco', 'numero', 'bairro', 'cidade', 'uf', 'cep',
        ).first()
        if anterior is None:
            return
        if anterior.hash_endereco_atual() == instance.hash_endereco_atual():
            return

        instance.latitude = None
        instance.longitude = None
        instance.geo_precisao = ''
        instance.geo_endereco_hash = ''
        instance.geo_erro = ''
    except Exception:  # pragma: no cover - nunca impedir o save
        logger.exception('falha ao invalidar coordenada de %s#%s', sender.__name__, instance.pk)


def _registrar():
    from django.apps import apps as django_apps

    for app_label, model_name in _MODELS_GEO:
        try:
            model = django_apps.get_model(app_label, model_name)
        except LookupError:  # pragma: no cover
            logger.warning('modelo %s.%s nao encontrado para signal geo', app_label, model_name)
            continue
        pre_save.connect(
            _invalidar_se_endereco_mudou,
            sender=model,
            dispatch_uid=f'mapas_invalida_geo_{app_label}_{model_name}',
        )


_registrar()
