from django.db import models


class RegimeIBSCBS(models.TextChoices):
    """Forma de apuracao de IBS/CBS, separada do regime empresarial."""

    MEI = 'mei', 'MEI'
    SIMPLES = 'simples', 'Simples Nacional'
    REGULAR = 'regular', 'Regime regular'


def regime_ibs_cbs_padrao(regime_empresarial):
    """Fallback conservador quando o cadastro ainda nao separa IBS/CBS."""
    if regime_empresarial == 'mei':
        return RegimeIBSCBS.MEI
    if regime_empresarial == 'simples_nacional':
        return RegimeIBSCBS.SIMPLES
    if regime_empresarial in {'lucro_presumido', 'lucro_real'}:
        return RegimeIBSCBS.REGULAR
    return ''
