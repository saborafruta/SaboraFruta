from django.urls import path

from . import views

app_name = 'crm'

urlpatterns = [
    path('recompra/', views.AlertasRecompraView.as_view(), name='recompra'),
    path('recompra/recalcular/', views.RecompraRecalcularView.as_view(), name='recompra-recalcular'),
]
