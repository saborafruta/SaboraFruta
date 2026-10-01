from django.urls import path

from . import views

app_name = 'agenda'

urlpatterns = [
    path('', views.AgendaView.as_view(), name='agenda'),
    path('novo/', views.AgendamentoCreateView.as_view(), name='agendamento-create'),
    path('agendamentos/<int:pk>/', views.AgendamentoDetailView.as_view(), name='agendamento-detail'),
    path('agendamentos/<int:pk>/status/', views.AgendamentoStatusView.as_view(), name='agendamento-status'),
    path('bloqueios/', views.BloqueioListCreateView.as_view(), name='bloqueio-list'),
    path('bloqueios/<int:pk>/remover/', views.BloqueioDeleteView.as_view(), name='bloqueio-delete'),
    path('profissionais/', views.ProfissionalListView.as_view(), name='profissional-list'),
    path('profissionais/novo/', views.ProfissionalConfigView.as_view(), name='profissional-create'),
    path('profissionais/<int:pk>/', views.ProfissionalConfigView.as_view(), name='profissional-update'),
    path('api/disponibilidade/', views.DisponibilidadeApiView.as_view(), name='disponibilidade-api'),
]
