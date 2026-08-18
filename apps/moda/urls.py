"""
Rotas do vertical Moda.

As rotas de item são geradas a partir de `menu.py` — escrever 62 `path()`
à mão significaria manter duas listas em paralelo e, mais cedo ou mais
tarde, um link do menu apontando para rota inexistente.

Quando uma tela real ficar pronta, declare a rota dela em `ROTAS_PRONTAS`
(antes do catch-all) apontando para a view definitiva. O endereço não muda,
então o link do menu continua valendo.
"""
from django.urls import path

from . import views, views_cadastros as vc

app_name = 'moda'

# Telas já implementadas — declaradas ANTES do catch-all, senão o
# placeholder as engoliria. O endereço é o mesmo que o menu já aponta.
ROTAS_PRONTAS: list = [
    path('produtos/grades/', vc.GradeListView.as_view(), name='grade-list'),
    path('produtos/grades/nova/', vc.GradeFormView.as_view(), name='grade-create'),
    path('produtos/grades/<int:pk>/', vc.GradeFormView.as_view(), name='grade-update'),

    path('produtos/cores/', vc.CorListView.as_view(), name='cor-list'),
    path('produtos/cores/nova/', vc.CorFormView.as_view(), name='cor-create'),
    path('produtos/cores/<int:pk>/', vc.CorFormView.as_view(), name='cor-update'),

    path('produtos/produtos/', vc.ProdutoListView.as_view(), name='produto-list'),
    path('produtos/produtos/novo/', vc.ProdutoFormView.as_view(), name='produto-create'),
    path('produtos/produtos/<int:pk>/', vc.ProdutoDetailView.as_view(), name='produto-detail'),
    path('produtos/produtos/<int:pk>/editar/', vc.ProdutoFormView.as_view(), name='produto-update'),
    path('produtos/produtos/<int:pk>/cores/', vc.ProdutoCorAddView.as_view(), name='produto-cor-add'),
    path('produtos/produtos/<int:pk>/variantes/', vc.ProdutoGerarVariantesView.as_view(), name='produto-gerar-variantes'),
]

urlpatterns = [
    path('', views.HubView.as_view(), name='hub'),
    *ROTAS_PRONTAS,
    path('<slug:grupo_slug>/', views.GrupoView.as_view(), name='grupo'),
    path('<slug:grupo_slug>/<slug:item_slug>/', views.ItemView.as_view(), name='item'),
]
