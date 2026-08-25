"""
Código de barras — mudou para `apps.core.services.barras`.

Este módulo continua existindo porque `views_qr.py` e `views_cadastros.py`
importam daqui, e trocar os imports agora seria mexer em arquivo que outra
frente está editando. O código real é do core: o segundo vertical que
precisou de barras mostrou que ele nunca foi da confecção.
"""
from apps.core.services.barras import MAIOR_IMPRIMIVEL, MENOR_IMPRIMIVEL, suportado, svg

__all__ = ['MAIOR_IMPRIMIVEL', 'MENOR_IMPRIMIVEL', 'suportado', 'svg']
