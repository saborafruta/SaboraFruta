from django.urls import path

from . import views

app_name = 'food_service'

urlpatterns = [
    path('', views.PainelMesasView.as_view(), name='painel'),
    path('api/painel/', views.api_painel_mesas, name='api-painel'),

    path('mesas/', views.MesaListView.as_view(), name='mesa-list'),
    path('mesas/nova/', views.MesaCreateView.as_view(), name='mesa-create'),
    path('mesas/<int:pk>/', views.MesaUpdateView.as_view(), name='mesa-update'),
    path('mesas/<int:pk>/toggle-ativo/', views.MesaToggleAtivoView.as_view(), name='mesa-toggle-ativo'),
    path('mesas/<int:pk>/marcar-reservada/', views.MesaMarcarReservadaView.as_view(), name='mesa-marcar-reservada'),
    path('mesas/<int:pk>/marcar-livre/', views.MesaMarcarLivreView.as_view(), name='mesa-marcar-livre'),
    path('mesas/<int:pk>/excluir/', views.MesaDeleteView.as_view(), name='mesa-delete'),

    path('comandas/abrir/', views.ComandaAbrirView.as_view(), name='comanda-abrir'),
    path('comandas/historico/', views.ComandaHistoricoListView.as_view(), name='comanda-historico'),
    path('comandas/<int:pk>/', views.ComandaDetailView.as_view(), name='comanda-detail'),
    path('comandas/<int:pk>/itens/adicionar/', views.ComandaAdicionarItemView.as_view(), name='comanda-item-adicionar'),
    path('comandas/<int:pk>/itens/<int:item_pk>/remover/', views.ComandaRemoverItemView.as_view(), name='comanda-item-remover'),
    path('comandas/<int:pk>/itens/<int:item_pk>/transferir/', views.ComandaTransferirItemView.as_view(), name='comanda-item-transferir'),
    path('comandas/<int:pk>/unir/', views.ComandaUnirView.as_view(), name='comanda-unir'),
    path('comandas/<int:pk>/transferir-mesa/', views.ComandaTransferirMesaView.as_view(), name='comanda-transferir-mesa'),
    path('comandas/<int:pk>/unir-mesas/', views.ComandaUnirMesasView.as_view(), name='comanda-unir-mesas'),
    path('comandas/<int:pk>/liberar-mesa/', views.ComandaLiberarMesaView.as_view(), name='comanda-liberar-mesa'),
    path('comandas/<int:pk>/fechar/', views.ComandaFecharView.as_view(), name='comanda-fechar'),

    path('reservas/', views.ReservaListView.as_view(), name='reserva-list'),
    path('reservas/nova/', views.ReservaCreateView.as_view(), name='reserva-create'),
    path('reservas/<int:pk>/cancelar/', views.ReservaCancelarView.as_view(), name='reserva-cancelar'),
    path('reservas/<int:pk>/atender/', views.ReservaAtenderView.as_view(), name='reserva-atender'),
]
