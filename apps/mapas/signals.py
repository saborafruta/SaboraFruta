"""
Coordenadas automáticas a partir do endereço (§2 da especificação).

São dois momentos:

1. `pre_save` — detecta que o endereço mudou e invalida a coordenada antiga.
   Instantâneo e à prova de falha: não pode impedir o cadastro.

2. `post_save` — geocodifica o registro na hora, para cumprir "caso o endereço
   seja alterado, atualizar automaticamente latitude e longitude". É UMA
   chamada HTTP para UM registro, com timeout curto e exceção engolida.

O perigo do passo 2 é o lote: uma importação de 500 clientes viraria 500
chamadas de rede dentro do request e estouraria o timeout de 120s do gunicorn.
Por isso existe o `modo_lote()` — um contexto que desliga a geocodificação
automática. Quem grava em massa (importação de CSV, replicação entre filiais,
o próprio comando `geocodificar`) entra nesse modo, e os registros ficam
pendentes para o backfill processar com throttle.
"""
import logging
import threading
from contextlib import contextmanager

from django.db.models.signals import post_save, pre_save

logger = logging.getLogger(__name__)

#: Estado por thread — cada worker do gunicorn atende um request por vez, então
#: o escopo de thread é o escopo do request.
_local = threading.local()


def em_modo_lote() -> bool:
    return getattr(_local, 'modo_lote', False)


@contextmanager
def modo_lote():
    """
    Desliga a geocodificação automática no bloco.

    Use ao gravar muitos registros de uma vez. Eles continuam marcados como
    pendentes (o `pre_save` segue rodando), então o backfill os pega depois.
    """
    anterior = em_modo_lote()
    _local.modo_lote = True
    try:
        yield
    finally:
        _local.modo_lote = anterior

#: models que ganham geocodificação automática (todos herdam CoordenadaMixin)
_MODELS_GEO = (
    ('cadastros', 'Cliente'),
    ('cadastros', 'ClienteEndereco'),
    ('cadastros', 'Fornecedor'),
    ('cadastros', 'Transportadora'),
    ('cadastros', 'Motorista'),
    ('core', 'Filial'),
    ('core', 'Empresa'),
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


def _geocodificar_apos_salvar(sender, instance, **kwargs):
    """
    Preenche latitude/longitude logo após o save.

    Só age quando falta coordenada (`geo_desatualizado`), então salvar um
    cliente sem mexer no endereço não gasta chamada — e o cache do provider
    cobre endereços repetidos.

    Qualquer falha é engolida: o registro fica pendente para o backfill, mas o
    cadastro do usuário nunca é perdido por causa de um provider fora do ar.
    """
    from django.conf import settings

    if em_modo_lote():
        return
    # Interruptor global: desligado nos testes (senao cada fixture viraria uma
    # chamada de rede) e disponivel como valvula de escape se o provider ficar
    # degradado em producao.
    if not getattr(settings, 'MAPAS_GEOCODIFICAR_AO_SALVAR', True):
        return
    try:
        if not instance.geo_desatualizado:
            return

        from apps.mapas.services import GeocodificacaoService

        # `salvar=False` + update_fields manual: chamar obj.save() aqui
        # dispararia o post_save de novo e entraria em recursao.
        servico = GeocodificacaoService()
        servico.geocodificar_objeto(instance, salvar=False)
        sender.objects.filter(pk=instance.pk).update(
            latitude=instance.latitude,
            longitude=instance.longitude,
            geo_precisao=instance.geo_precisao,
            geo_endereco_hash=instance.geo_endereco_hash,
            geo_atualizado_em=instance.geo_atualizado_em,
            geo_erro=instance.geo_erro,
        )
    except Exception:
        logger.exception(
            'falha ao geocodificar %s#%s apos salvar', sender.__name__, instance.pk,
        )


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
        post_save.connect(
            _geocodificar_apos_salvar,
            sender=model,
            dispatch_uid=f'mapas_geocodifica_{app_label}_{model_name}',
        )


_registrar()
