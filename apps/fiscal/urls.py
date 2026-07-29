from django.urls import path

from apps.fiscal import views

app_name = 'fiscal'

urlpatterns = [
    path('manifesto/', views.ManifestoFiscalListView.as_view(), name='manifesto-list'),
    path(
        'manifesto/saidas/<int:pk>/consultar/',
        views.DocumentoFiscalSaidaConsultarView.as_view(),
        name='documento-saida-consultar',
    ),
    path('manifesto/config/', views.ManifestoFiscalConfigView.as_view(), name='manifesto-config'),
    path(
        'manifesto/<int:pk>/importar-entrada/',
        views.ManifestoFiscalImportarEntradaView.as_view(),
        name='manifesto-importar-entrada',
    ),
    path(
        'manifesto/<int:pk>/anexar-xml/',
        views.ManifestoFiscalAnexarXMLView.as_view(),
        name='manifesto-anexar-xml',
    ),
    path('manifesto/<int:pk>/<slug:acao>/', views.ManifestoFiscalAcaoView.as_view(), name='manifesto-acao'),

    # Focus NFe — webhook de status (assíncrono)
    path('webhook/focusnfe/', views.webhook_focusnfe, name='webhook-focusnfe'),

    # Focus NFe — consultas auxiliares
    path('api/consulta/cnpj/<str:valor>/', views.consulta_cnpj, name='consulta-cnpj'),
    path('api/consulta/ncm/<str:valor>/', views.consulta_ncm, name='consulta-ncm'),
    path('api/consulta/cfop/<str:valor>/', views.consulta_cfop, name='consulta-cfop'),
    path('api/consulta/cnae/<str:valor>/', views.consulta_cnae, name='consulta-cnae'),
    path('api/consulta/municipios/<str:valor>/', views.consulta_municipios_api, name='consulta-municipios-api'),

    # Focus NFe - paginas de consulta fiscal
    path('consultas/cfop/', views.consultas_cfop, name='consultas-cfop'),
    path('consultas/cnae/', views.consultas_cnae, name='consultas-cnae'),
    path('consultas/cnpj/', views.consultas_cnpj_page, name='consultas-cnpj'),
    path('consultas/ncm/', views.consultas_ncm, name='consultas-ncm'),
    path('consultas/municipios/', views.consultas_municipios, name='consultas-municipios'),
]
