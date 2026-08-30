from django.urls import path
from .views import comprovante_publico

app_name = 'pdv_publico'
urlpatterns = [
    path('<str:token>/', comprovante_publico.visualizar, name='comprovante'),
    path('<str:token>/pdf/', comprovante_publico.baixar_pdf, name='pdf'),
]
