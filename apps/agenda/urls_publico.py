from django.urls import path

from . import views_publico


app_name = 'agenda_publica'

urlpatterns = [
    path('<str:token>/', views_publico.AgendaPublicaView.as_view(), name='agendar'),
    path('<str:token>/horarios/', views_publico.HorariosPublicosView.as_view(), name='horarios'),
]
