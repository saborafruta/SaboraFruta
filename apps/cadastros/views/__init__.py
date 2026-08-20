from .cliente import (
    ClienteListView, ClienteCreateView, ClienteUpdateView,
    ClienteDeleteView, ClienteToggleAtivoView, ClienteInlineEditView, ClienteExportCsvView,
    ClienteExportTodosCsvView, ClienteExportPdfView,
    ClienteImportCsvView, ClienteImportTemplateCsvView,
    consultar_cep_ajax,
)
from .fornecedor import (
    FornecedorListView, FornecedorCreateView, FornecedorAjaxCreateView, FornecedorUpdateView,
    FornecedorDeleteView, FornecedorToggleAtivoView, FornecedorInlineEditView, FornecedorExportCsvView,
    FornecedorExportTodosCsvView, FornecedorExportPdfView,
)
from .funcionario import (
    FuncionarioListView, FuncionarioCreateView, FuncionarioUpdateView,
    FuncionarioToggleAtivoView,
)
from .rota_praca import (
    PracaListView, PracaCreateView, PracaUpdateView, PracaToggleAtivoView,
    RotaListView, RotaCreateView, RotaUpdateView, RotaToggleAtivoView,
)
from .transportadora import (
    TransportadoraListView, TransportadoraCreateView, TransportadoraUpdateView, TransportadoraToggleAtivoView,
    MotoristaListView, MotoristaCreateView, MotoristaUpdateView, MotoristaToggleAtivoView, MotoristaAjaxCreateView,
    VeiculoListView, VeiculoCreateView, VeiculoUpdateView, VeiculoToggleAtivoView, VeiculoAjaxCreateView,
    RepresentanteListView, RepresentanteCreateView, RepresentanteUpdateView,
)
from .audit import CadastroLogItemsView, CadastroLogExportCsvView, CadastroLogExportPdfView

__all__ = [
    'ClienteListView', 'ClienteCreateView', 'ClienteUpdateView',
    'ClienteDeleteView', 'ClienteToggleAtivoView', 'ClienteInlineEditView', 'ClienteExportCsvView',
    'ClienteExportTodosCsvView', 'ClienteExportPdfView',
    'ClienteImportCsvView', 'ClienteImportTemplateCsvView',
    'consultar_cep_ajax',
    'FornecedorListView', 'FornecedorCreateView', 'FornecedorAjaxCreateView', 'FornecedorUpdateView',
    'FornecedorDeleteView', 'FornecedorToggleAtivoView', 'FornecedorInlineEditView', 'FornecedorExportCsvView',
    'FornecedorExportTodosCsvView', 'FornecedorExportPdfView',
    'FuncionarioListView', 'FuncionarioCreateView', 'FuncionarioUpdateView',
    'FuncionarioToggleAtivoView',
    'TransportadoraListView', 'TransportadoraCreateView', 'TransportadoraUpdateView', 'TransportadoraToggleAtivoView',
    'MotoristaListView', 'MotoristaCreateView', 'MotoristaUpdateView', 'MotoristaToggleAtivoView', 'MotoristaAjaxCreateView',
    'VeiculoListView', 'VeiculoCreateView', 'VeiculoUpdateView', 'VeiculoToggleAtivoView', 'VeiculoAjaxCreateView',
    'RepresentanteListView', 'RepresentanteCreateView', 'RepresentanteUpdateView',
    'PracaListView', 'PracaCreateView', 'PracaUpdateView', 'PracaToggleAtivoView',
    'RotaListView', 'RotaCreateView', 'RotaUpdateView', 'RotaToggleAtivoView',
    'CadastroLogItemsView', 'CadastroLogExportCsvView', 'CadastroLogExportPdfView',
]

