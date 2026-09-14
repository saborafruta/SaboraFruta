"""URLs da API REST de vendas do PDV. Ver docstring de apps/pdv/api/views.py."""
from django.urls import path

from . import views

app_name = 'pdv_api'

urlpatterns = [
    path('vendas/', views.VendaCreateView.as_view(), name='vendas'),
]
