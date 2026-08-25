"""
Rotas do vertical Polpa de Frutas.

As rotas de item são geradas a partir de `menu.py` pelo catch-all do fim —
escrever um `path()` por tela significaria manter duas listas em paralelo
e, mais cedo ou mais tarde, um link do menu apontando para rota inexistente.

Quando uma tela real fica pronta, declare a rota dela em `ROTAS_PRONTAS`,
ANTES do catch-all (senão o placeholder a engole), apontando para a view
definitiva. O endereço não muda, então o link do menu continua valendo.

ATENÇÃO À ORDEM. Rota com dois segmentos (`<grupo>/<item>/`) casa com
qualquer coisa: uma rota pronta declarada depois dela nunca é alcançada, e
o sintoma é cruel — o link sai certo no HTML (o `reverse` acha pelo nome) e
a página dá 404 (o `resolve` acha pelo padrão).
"""
from django.urls import path

from . import views, views_recebimento as vrec

app_name = 'polpa'

ROTAS_PRONTAS: list = [
    # ── Recebimento ─────────────────────────────────────────────────────
    # O endereço é o mesmo que o menu já aponta (`recebimento/romaneios/`),
    # para o item do menu não precisar mudar quando a tela fica pronta.
    path('recebimento/romaneios/', vrec.RecebimentoListView.as_view(), name='recebimento-list'),
    path('recebimento/romaneios/novo/', vrec.RecebimentoFormView.as_view(), name='recebimento-create'),
    path('recebimento/romaneios/<int:pk>/', vrec.RecebimentoDetailView.as_view(), name='recebimento-detail'),
    path('recebimento/romaneios/<int:pk>/editar/', vrec.RecebimentoFormView.as_view(), name='recebimento-update'),
    path('recebimento/romaneios/<int:pk>/classificar/', vrec.ClassificarView.as_view(), name='recebimento-classificar'),
    path('recebimento/romaneios/<int:pk>/aprovar/', vrec.AprovarView.as_view(), name='recebimento-aprovar'),
    path('recebimento/romaneios/<int:pk>/recusar/', vrec.RecusarView.as_view(), name='recebimento-recusar'),
    path('recebimento/romaneios/<int:pk>/cancelar/', vrec.CancelarView.as_view(), name='recebimento-cancelar'),

    # A lista de recusas é a MESMA fila, filtrada — e não uma tela paralela
    # que amanhã mostraria outra contagem da mesma coisa.
    path('recebimento/recusas/', vrec.RecusasView.as_view(), name='recebimento-recusas'),

    # ── Cadastro das frutas ─────────────────────────────────────────────
    path('formulacao/rendimento/', vrec.FrutaListView.as_view(), name='fruta-list'),
    path('formulacao/rendimento/nova/', vrec.FrutaFormView.as_view(), name='fruta-create'),
    path('formulacao/rendimento/<int:pk>/editar/', vrec.FrutaFormView.as_view(), name='fruta-update'),
]

urlpatterns = [
    path('', views.HubView.as_view(), name='hub'),
    *ROTAS_PRONTAS,
    path('<slug:grupo_slug>/', views.GrupoView.as_view(), name='grupo'),
    path('<slug:grupo_slug>/<slug:item_slug>/', views.ItemView.as_view(), name='item'),
]
