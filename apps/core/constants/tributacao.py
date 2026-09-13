from django.db import models


class RegimeIBSCBS(models.TextChoices):
    """Forma de apuracao de IBS/CBS, separada do regime empresarial."""

    MEI = 'mei', 'MEI'
    SIMPLES = 'simples', 'Simples Nacional'
    REGULAR = 'regular', 'Regime regular'


REGIME_SIMPLES_NACIONAL_HIBRIDO = 'simples_nacional_hibrido'

REGIMES_EMPRESARIAIS_COTACAO = (
    ('mei', 'MEI'),
    ('simples_nacional', 'Simples Nacional'),
    (REGIME_SIMPLES_NACIONAL_HIBRIDO, 'Simples Nacional Híbrido'),
    ('lucro_presumido', 'Lucro Presumido'),
    ('lucro_real', 'Lucro Real'),
)


def normalizar_regimes_cotacao(regime_empresarial, regime_ibs_cbs):
    """Converte o atalho híbrido nos dois regimes persistidos separadamente."""
    if regime_empresarial == REGIME_SIMPLES_NACIONAL_HIBRIDO:
        return 'simples_nacional', RegimeIBSCBS.REGULAR
    return regime_empresarial, regime_ibs_cbs


def regime_empresarial_para_seletor(regime_empresarial, regime_ibs_cbs):
    """Apresenta como híbrido o Simples que apura IBS/CBS pelo regime regular."""
    if regime_empresarial == 'simples_nacional' and regime_ibs_cbs == RegimeIBSCBS.REGULAR:
        return REGIME_SIMPLES_NACIONAL_HIBRIDO
    return regime_empresarial


def regime_ibs_cbs_padrao(regime_empresarial):
    """Fallback conservador quando o cadastro ainda nao separa IBS/CBS."""
    if regime_empresarial == 'mei':
        return RegimeIBSCBS.MEI
    if regime_empresarial == 'simples_nacional':
        return RegimeIBSCBS.SIMPLES
    if regime_empresarial in {'lucro_presumido', 'lucro_real'}:
        return RegimeIBSCBS.REGULAR
    return ''
