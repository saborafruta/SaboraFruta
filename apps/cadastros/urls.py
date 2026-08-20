from django.urls import path

from apps.cadastros import views

app_name = 'cadastros'

urlpatterns = [
    # CEP
    path('cep/', views.consultar_cep_ajax, name='consultar-cep'),
    path('<str:tipo>/<int:pk>/log/registros/', views.CadastroLogItemsView.as_view(), name='cadastro-log-items'),
    path('<str:tipo>/<int:pk>/log/exportar/csv/', views.CadastroLogExportCsvView.as_view(), name='cadastro-log-export-csv'),
    path('<str:tipo>/<int:pk>/log/exportar/pdf/', views.CadastroLogExportPdfView.as_view(), name='cadastro-log-export-pdf'),

    # Clientes
    path('clientes/', views.ClienteListView.as_view(), name='cliente-list'),
    path('clientes/exportar/csv/', views.ClienteExportCsvView.as_view(), name='cliente-export-csv'),
    path('clientes/exportar/pdf/', views.ClienteExportPdfView.as_view(), name='cliente-export-pdf'),
    path('clientes/exportar/todos/csv/', views.ClienteExportTodosCsvView.as_view(), name='cliente-export-todos-csv'),
    path('clientes/importar/', views.ClienteImportCsvView.as_view(), name='cliente-import-csv'),
    path('clientes/importar/modelo/', views.ClienteImportTemplateCsvView.as_view(), name='cliente-import-csv-template'),
    path('clientes/novo/', views.ClienteCreateView.as_view(), name='cliente-create'),
    path('clientes/<int:pk>/inline-edit/', views.ClienteInlineEditView.as_view(), name='cliente-inline-edit'),
    path('clientes/<int:pk>/', views.ClienteUpdateView.as_view(), name='cliente-update'),
    path('clientes/<int:pk>/excluir/', views.ClienteDeleteView.as_view(), name='cliente-delete'),
    path('clientes/<int:pk>/toggle-ativo/', views.ClienteToggleAtivoView.as_view(), name='cliente-toggle-ativo'),

    # Fornecedores
    path('fornecedores/', views.FornecedorListView.as_view(), name='fornecedor-list'),
    path('fornecedores/exportar/csv/', views.FornecedorExportCsvView.as_view(), name='fornecedor-export-csv'),
    path('fornecedores/exportar/pdf/', views.FornecedorExportPdfView.as_view(), name='fornecedor-export-pdf'),
    path('fornecedores/exportar/todos/csv/', views.FornecedorExportTodosCsvView.as_view(), name='fornecedor-export-todos-csv'),
    path('fornecedores/novo/', views.FornecedorCreateView.as_view(), name='fornecedor-create'),
    path('fornecedores/ajax-create/', views.FornecedorAjaxCreateView.as_view(), name='fornecedor-ajax-create'),
    path('fornecedores/<int:pk>/inline-edit/', views.FornecedorInlineEditView.as_view(), name='fornecedor-inline-edit'),
    path('fornecedores/<int:pk>/', views.FornecedorUpdateView.as_view(), name='fornecedor-update'),
    path('fornecedores/<int:pk>/excluir/', views.FornecedorDeleteView.as_view(), name='fornecedor-delete'),
    path('fornecedores/<int:pk>/toggle-ativo/', views.FornecedorToggleAtivoView.as_view(), name='fornecedor-toggle-ativo'),

    # Funcionarios
    path('funcionarios/', views.FuncionarioListView.as_view(), name='funcionario-list'),
    path('funcionarios/novo/', views.FuncionarioCreateView.as_view(), name='funcionario-create'),
    path('funcionarios/<int:pk>/', views.FuncionarioUpdateView.as_view(), name='funcionario-update'),
    path('funcionarios/<int:pk>/toggle-ativo/', views.FuncionarioToggleAtivoView.as_view(), name='funcionario-toggle-ativo'),

    # Transportadoras
    path('transportadoras/', views.TransportadoraListView.as_view(), name='transportadora-list'),
    path('transportadoras/novo/', views.TransportadoraCreateView.as_view(), name='transportadora-create'),
    path('transportadoras/<int:pk>/', views.TransportadoraUpdateView.as_view(), name='transportadora-update'),
    path('transportadoras/<int:pk>/toggle-ativo/', views.TransportadoraToggleAtivoView.as_view(), name='transportadora-toggle-ativo'),

    # Veiculos
    path('veiculos/', views.VeiculoListView.as_view(), name='veiculo-list'),
    path('veiculos/novo/', views.VeiculoCreateView.as_view(), name='veiculo-create'),
    path('veiculos/ajax-create/', views.VeiculoAjaxCreateView.as_view(), name='veiculo-ajax-create'),
    path('veiculos/<int:pk>/', views.VeiculoUpdateView.as_view(), name='veiculo-update'),
    path('veiculos/<int:pk>/toggle-ativo/', views.VeiculoToggleAtivoView.as_view(), name='veiculo-toggle-ativo'),

    # Motoristas
    path('motoristas/', views.MotoristaListView.as_view(), name='motorista-list'),
    path('motoristas/novo/', views.MotoristaCreateView.as_view(), name='motorista-create'),
    path('motoristas/ajax-create/', views.MotoristaAjaxCreateView.as_view(), name='motorista-ajax-create'),
    path('motoristas/<int:pk>/', views.MotoristaUpdateView.as_view(), name='motorista-update'),
    path('motoristas/<int:pk>/toggle-ativo/', views.MotoristaToggleAtivoView.as_view(), name='motorista-toggle-ativo'),

    # Representantes
    path('representantes/', views.RepresentanteListView.as_view(), name='representante-list'),
    path('representantes/novo/', views.RepresentanteCreateView.as_view(), name='representante-create'),
    path('representantes/<int:pk>/', views.RepresentanteUpdateView.as_view(), name='representante-update'),

    # Praças
    path('pracas/', views.PracaListView.as_view(), name='praca-list'),
    path('pracas/nova/', views.PracaCreateView.as_view(), name='praca-create'),
    path('pracas/<int:pk>/', views.PracaUpdateView.as_view(), name='praca-update'),
    path('pracas/<int:pk>/toggle-ativo/', views.PracaToggleAtivoView.as_view(), name='praca-toggle-ativo'),

    # Rotas
    path('rotas/', views.RotaListView.as_view(), name='rota-list'),
    path('rotas/nova/', views.RotaCreateView.as_view(), name='rota-create'),
    path('rotas/<int:pk>/', views.RotaUpdateView.as_view(), name='rota-update'),
    path('rotas/<int:pk>/toggle-ativo/', views.RotaToggleAtivoView.as_view(), name='rota-toggle-ativo'),
]

