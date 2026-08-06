from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve
from django.views.generic import RedirectView
from apps.core.views.health import health_check

urlpatterns = [
    path('health/', health_check, name='health'),
    path('admin/', admin.site.urls),
    path('', RedirectView.as_view(url='/dashboard/', permanent=False)),
    path('', include('apps.core.urls', namespace='core')),
    path('cadastros/', include('apps.cadastros.urls', namespace='cadastros')),
    path('produtos/', include('apps.produtos.urls', namespace='produtos')),
    path('estoque/', include('apps.estoque.urls', namespace='estoque')),
    path('producao/', include('apps.producao.urls', namespace='producao')),
    path('vendas/', include('apps.vendas.urls', namespace='vendas')),
    path('compras/', include('apps.compras.urls', namespace='compras')),
    path('fiscal/', include('apps.fiscal.urls', namespace='fiscal')),
    # Novos módulos (mai/2026)
    path('financeiro/', include('apps.financeiro.urls', namespace='financeiro')),
    path('logistica/', include('apps.logistica.urls', namespace='logistica')),
    path('pdv/', include('apps.pdv.urls', namespace='pdv')),
    path('qualidade/', include('apps.qualidade.urls', namespace='qualidade')),
    path('analytics/', include('apps.analytics.urls', namespace='analytics')),
    path('lotes/', include('apps.lotes.urls', namespace='lotes')),
    path('cashback/', include('apps.cashback.urls', namespace='cashback')),
    path('crm/', include('apps.crm.urls', namespace='crm')),
    path('mapas/', include('apps.mapas.urls', namespace='mapas')),
    path('food-service/', include('apps.food_service.urls', namespace='food_service')),
    path('cardapio/', include('apps.food_service.urls_publico', namespace='food_service_publico')),
]

handler403 = 'apps.core.views.errors.permission_denied'

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    try:
        import debug_toolbar
        urlpatterns = [path('__debug__/', include(debug_toolbar.urls))] + urlpatterns
    except ImportError:
        pass
else:
    # MVP: serve uploads locais no Railway. Em producao madura, trocar por S3/R2.
    urlpatterns += [
        re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
    ]
