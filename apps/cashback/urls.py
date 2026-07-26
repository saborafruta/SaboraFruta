from django.urls import path

from . import views

app_name = "cashback"

urlpatterns = [
    path("configuracao/", views.ConfiguracaoCashbackView.as_view(), name="configuracao"),

    path("regras/", views.RegrasCashbackView.as_view(), name="regras"),
    path("regras/buscar-alvo/", views.RegraCashbackBuscaAlvoView.as_view(), name="regra-buscar-alvo"),
    path("regras/<str:nivel>/<int:pk>/excluir/", views.RegraCashbackDeleteView.as_view(), name="regra-excluir"),

    path("campanhas/", views.CampanhaCashbackListView.as_view(), name="campanha-list"),
    path("campanhas/nova/", views.CampanhaCashbackCreateView.as_view(), name="campanha-create"),
    path("campanhas/<int:pk>/editar/", views.CampanhaCashbackUpdateView.as_view(), name="campanha-update"),
    path("campanhas/<int:pk>/toggle/", views.CampanhaCashbackToggleView.as_view(), name="campanha-toggle"),

    path("carteira/", views.CarteiraCashbackBuscaView.as_view(), name="carteira-busca"),
    path("carteira/<int:cliente_id>/", views.CarteiraCashbackDetailView.as_view(), name="carteira-detail"),
]
