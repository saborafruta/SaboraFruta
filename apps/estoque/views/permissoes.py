"""Helpers de permissao do modulo de estoque."""
from django.contrib import messages
from django.shortcuts import redirect

from apps.core.services.permissions import PERMISSION_DENIED_MESSAGE


def permissoes_estoque(request):
    usuario = request.user
    pode_criar = usuario.tem_permissao('estoque', 'criar')
    pode_editar = usuario.tem_permissao('estoque', 'editar')
    pode_cancelar = usuario.tem_permissao('estoque', 'cancelar')
    pode_aprovar = usuario.tem_permissao('estoque', 'aprovar')
    pode_exportar = usuario.tem_permissao('estoque', 'exportar')
    return {
        'pode_ver': usuario.tem_permissao('estoque', 'ver'),
        'pode_criar': pode_criar,
        'pode_editar': pode_editar,
        'pode_cancelar': pode_cancelar,
        'pode_aprovar': pode_aprovar,
        'pode_exportar': pode_exportar,
        'pode_movimentar': pode_criar,
        'pode_ajustar': pode_editar,
        'pode_transferir': pode_aprovar,
        'pode_abrir_inventario': pode_criar,
        'pode_contar_inventario': pode_editar,
        'pode_fechar_inventario': pode_aprovar,
        'pode_baixar_validade': pode_cancelar,
    }


def bloquear_exportacao_sem_permissao(request, fallback='estoque:estoque-list', **kwargs):
    if request.user.tem_permissao('estoque', 'exportar'):
        return None
    messages.error(request, PERMISSION_DENIED_MESSAGE)
    return redirect(fallback, **kwargs)
