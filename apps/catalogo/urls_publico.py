from django.urls import path

from .views_publico import CatalogoPublicoView, ClienteCatalogoView

app_name = 'catalogo_publico'

urlpatterns = [
    path('<str:token>/', CatalogoPublicoView.as_view(), name='catalogo'),
    path('<str:token>/cliente/', ClienteCatalogoView.as_view(), name='cliente'),
]
