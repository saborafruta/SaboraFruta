"""
Expoe os grupos do vertical para o sidebar monta-los por laco.

Fica aqui, e nao em `apps/core/context_processors.py`, para a dependencia
apontar na direcao certa: polpa conhece core, core nao precisa conhecer
polpa -- do mesmo jeito que o vertical de moda ja' se acopla.
"""
from .menu import GRUPOS


def polpa_menu(request):
    # Lista estatica vinda de menu.py, sem consulta ao banco. Quem decide
    # mostrar ou nao e' o `{% if 'polpa' in modulos_ativos %}` do sidebar.
    return {'polpa_grupos': GRUPOS}
