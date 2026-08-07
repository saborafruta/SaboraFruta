from django.urls import path

from . import views

app_name = 'food_service'

urlpatterns = [
    path('', views.PainelMesasView.as_view(), name='painel'),
    path('api/painel/', views.api_painel_mesas, name='api-painel'),
    path('api/chamados/', views.api_chamados_pendentes, name='api-chamados'),
    path('chamados/<int:pk>/atender/', views.ChamadoAtenderView.as_view(), name='chamado-atender'),

    path('mesas/', views.MesaListView.as_view(), name='mesa-list'),
    path('mesas/nova/', views.MesaCreateView.as_view(), name='mesa-create'),
    path('mesas/<int:pk>/', views.MesaUpdateView.as_view(), name='mesa-update'),
    path('mesas/<int:pk>/qr-code/', views.MesaQrCodeView.as_view(), name='mesa-qr-code'),
    path('mesas/<int:pk>/toggle-ativo/', views.MesaToggleAtivoView.as_view(), name='mesa-toggle-ativo'),
    path('mesas/<int:pk>/marcar-reservada/', views.MesaMarcarReservadaView.as_view(), name='mesa-marcar-reservada'),
    path('mesas/<int:pk>/marcar-livre/', views.MesaMarcarLivreView.as_view(), name='mesa-marcar-livre'),
    path('mesas/<int:pk>/excluir/', views.MesaDeleteView.as_view(), name='mesa-delete'),

    path('comandas/abrir/', views.ComandaAbrirView.as_view(), name='comanda-abrir'),
    path('comandas/historico/', views.ComandaHistoricoListView.as_view(), name='comanda-historico'),
    path('comandas/<int:pk>/', views.ComandaDetailView.as_view(), name='comanda-detail'),
    path('comandas/<int:pk>/itens/adicionar/', views.ComandaAdicionarItemView.as_view(), name='comanda-item-adicionar'),
    path('comandas/<int:pk>/itens/<int:item_pk>/remover/', views.ComandaRemoverItemView.as_view(), name='comanda-item-remover'),
    path('comandas/<int:pk>/itens/<int:item_pk>/complementos/adicionar/', views.ComandaAdicionarComplementoView.as_view(), name='comanda-item-complemento-adicionar'),
    path('comandas/<int:pk>/itens/<int:item_pk>/complementos/<int:complemento_pk>/remover/', views.ComandaRemoverComplementoView.as_view(), name='comanda-item-complemento-remover'),
    path('comandas/<int:pk>/itens/<int:item_pk>/transferir/', views.ComandaTransferirItemView.as_view(), name='comanda-item-transferir'),
    path('comandas/<int:pk>/unir/', views.ComandaUnirView.as_view(), name='comanda-unir'),
    path('comandas/<int:pk>/separar/', views.ComandaSepararView.as_view(), name='comanda-separar'),
    path('comandas/<int:pk>/transferir-mesa/', views.ComandaTransferirMesaView.as_view(), name='comanda-transferir-mesa'),
    path('comandas/<int:pk>/unir-mesas/', views.ComandaUnirMesasView.as_view(), name='comanda-unir-mesas'),
    path('comandas/<int:pk>/liberar-mesa/', views.ComandaLiberarMesaView.as_view(), name='comanda-liberar-mesa'),
    path('comandas/<int:pk>/fechar/', views.ComandaFecharView.as_view(), name='comanda-fechar'),
    path('comandas/<int:pk>/pedidos-pendentes/<int:pedido_pk>/confirmar/', views.ComandaPedidoPendenteConfirmarView.as_view(), name='comanda-pedido-pendente-confirmar'),
    path('comandas/<int:pk>/pedidos-pendentes/<int:pedido_pk>/recusar/', views.ComandaPedidoPendenteRecusarView.as_view(), name='comanda-pedido-pendente-recusar'),

    path('reservas/', views.ReservaListView.as_view(), name='reserva-list'),
    path('reservas/nova/', views.ReservaCreateView.as_view(), name='reserva-create'),
    path('reservas/<int:pk>/cancelar/', views.ReservaCancelarView.as_view(), name='reserva-cancelar'),
    path('reservas/<int:pk>/atender/', views.ReservaAtenderView.as_view(), name='reserva-atender'),

    path('kds/', views.KdsView.as_view(), name='kds'),
    path('api/kds/', views.api_kds, name='api-kds'),
    path('kds/<int:pk>/avancar/', views.KdsAvancarStatusView.as_view(), name='kds-avancar'),
    path('kds/<int:pk>/prioridade/', views.KdsAlterarPrioridadeView.as_view(), name='kds-prioridade'),
    path('kds/<int:pk>/cancelar/', views.KdsCancelarItemView.as_view(), name='kds-cancelar'),
]
