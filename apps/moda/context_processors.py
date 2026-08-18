"""
Expõe os grupos do vertical para o sidebar montá-los por laço.

Fica aqui, e não em `apps/core/context_processors.py`, para a dependência
apontar na direção certa: moda conhece core, core não precisa conhecer
moda. Assim um vertical futuro se acopla do mesmo jeito, sem mexer no core.
"""
from .menu import GRUPOS


def moda_menu(request):
    # Lista estática vinda de menu.py, sem consulta ao banco. Quem decide
    # mostrar ou não é o `{% if 'moda' in modulos_ativos %}` do sidebar.
    return {'moda_grupos': GRUPOS}
