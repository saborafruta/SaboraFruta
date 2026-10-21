from django.urls import path

from apps.financeiro.views.boleto_publico import baixar_pdf, visualizar


app_name = "boleto_publico"

urlpatterns = [
    path("<uuid:token>/", visualizar, name="visualizar"),
    path("<uuid:token>/pdf/", baixar_pdf, name="pdf"),
]
