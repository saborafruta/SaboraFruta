from django.urls import path

from apps.fiscal import views, views_natureza

app_name = 'fiscal'

urlpatterns = [
    # Configurações Fiscais → Naturezas de operação
    path('naturezas/', views_natureza.NaturezaOperacaoListView.as_view(), name='natureza-list'),
    path('naturezas/nova/', views_natureza.NaturezaOperacaoFormView.as_view(), name='natureza-create'),
    path('naturezas/<int:pk>/', views_natureza.NaturezaOperacaoFormView.as_view(), name='natureza-edit'),
    path('naturezas/<int:pk>/regras/', views_natureza.RegraNaturezaCreateView.as_view(), name='natureza-regra-create'),
    path('naturezas/<int:pk>/regras/<int:regra_pk>/remover/', views_natureza.RegraNaturezaDeleteView.as_view(), name='natureza-regra-delete'),

    path('manifesto/', views.ManifestoFiscalListView.as_view(), name='manifesto-list'),
    path(
        'manifesto/saidas/<int:pk>/consultar/',
        views.DocumentoFiscalSaidaConsultarView.as_view(),
        name='documento-saida-consultar',
    ),
    path(
        'manifesto/saidas/<int:pk>/',
        views.DocumentoFiscalSaidaDetailView.as_view(),
        name='documento-saida-detail',
    ),
    path(
        'manifesto/saidas/<int:pk>/xml/<slug:tipo>/',
        views.DocumentoFiscalXMLView.as_view(),
        name='documento-saida-xml',
    ),
    path(
        'manifesto/saidas/exportar-xml/',
        views.DocumentoFiscalExportarXMLView.as_view(),
        name='documento-saida-exportar-xml',
    ),
    path(
        'manifesto/saidas/backup-focus/',
        views.DocumentoFiscalBackupFocusView.as_view(),
        name='documento-saida-backup-focus',
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
