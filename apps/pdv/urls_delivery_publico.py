from django.urls import path

from apps.pdv.views import delivery_publico


app_name = 'delivery_publico'

urlpatterns = [
    path('<str:token>/', delivery_publico.painel, name='painel'),
    path('<str:token>/pedido/<int:pk>/comprovante/', delivery_publico.comprovante, name='comprovante'),
    path('<str:token>/pedido/<int:pk>/comprovante/pdf/', delivery_publico.comprovante_pdf, name='comprovante_pdf'),
    path('<str:token>/pedido/<int:pk>/concluir/', delivery_publico.concluir, name='concluir'),
    path('<str:token>/parada/<str:parada_id>/concluir/', delivery_publico.concluir_parada_extra, name='concluir_parada_extra'),
]
