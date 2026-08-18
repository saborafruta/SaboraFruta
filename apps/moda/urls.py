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
    path('comercial/pedidos/', vc.PedidoListView.as_view(), name='pedido-list'),
    path('comercial/pedidos/novo/', vc.PedidoFormView.as_view(), name='pedido-create'),
    path('comercial/pedidos/<int:pk>/', vc.PedidoDetailView.as_view(), name='pedido-detail'),
    path('comercial/pedidos/<int:pk>/editar/', vc.PedidoFormView.as_view(), name='pedido-update'),
    path('comercial/pedidos/<int:pk>/status/', vc.PedidoStatusView.as_view(), name='pedido-status'),
    path('comercial/pedidos/<int:pk>/itens/', vc.ItemPedidoCreateView.as_view(), name='pedido-item-add'),
    path('comercial/pedidos/<int:pk>/itens/<int:item_pk>/remover/', vc.ItemPedidoDeleteView.as_view(), name='pedido-item-delete'),
    path('comercial/pedidos/<int:pk>/itens/<int:item_pk>/arte/', vc.PersonalizacaoCreateView.as_view(), name='pedido-arte-add'),
    path('comercial/pedidos/<int:pk>/itens/<int:item_pk>/arte/<int:arte_pk>/remover/', vc.PersonalizacaoDeleteView.as_view(), name='pedido-arte-delete'),
    path('comercial/pedidos/<int:pk>/itens/<int:item_pk>/visual/', vc.VisualCreateView.as_view(), name='pedido-visual-add'),
    path('comercial/pedidos/<int:pk>/itens/<int:item_pk>/visual/<int:visual_pk>/remover/', vc.VisualDeleteView.as_view(), name='pedido-visual-delete'),
    path('comercial/pedidos/<int:pk>/grade/', vc.GradePedidoSalvarView.as_view(), name='pedido-grade-salvar'),
    path('comercial/pedidos/<int:pk>/grade/tamanho/', vc.GradeTamanhoAddView.as_view(), name='pedido-grade-tamanho-add'),
    path('comercial/pedidos/<int:pk>/grade/tamanho/<int:tamanho_pk>/remover/', vc.GradeTamanhoRemoveView.as_view(), name='pedido-grade-tamanho-remove'),
    path('comercial/pedidos/<int:pk>/itens/<int:item_pk>/grade-produto/', vc.GradeAplicarDoProdutoView.as_view(), name='pedido-grade-do-produto'),
    path('comercial/pedidos/<int:pk>/itens/<int:item_pk>/grade-copiar/', vc.GradeCopiarView.as_view(), name='pedido-grade-copiar'),
    path('comercial/pedidos/<int:pk>/itens/<int:item_pk>/duplicar/', vc.ItemDuplicarView.as_view(), name='pedido-item-duplicar'),

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
