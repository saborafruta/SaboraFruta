from django.urls import path
from apps.qualidade.views import analises, checklist

app_name = "qualidade"

urlpatterns = [
    path("analises/", analises.analise_list, name="analise_list"),

    # O REGISTRO da conferência. As rotas acima cadastram o que conferir;
    # até agora não havia nenhuma para registrar o que se achou.
    path("checklists/", checklist.checklist_list, name="checklist_list"),
    path("checklists/abrir/", checklist.checklist_abrir, name="checklist_abrir"),
    path("checklists/<int:pk>/", checklist.checklist_detail, name="checklist_detail"),
    path("checklists/<int:pk>/salvar/", checklist.checklist_salvar, name="checklist_salvar"),
    path("checklists/<int:pk>/concluir/", checklist.checklist_concluir, name="checklist_concluir"),
    path("api/produtos/", analises.produto_search, name="produto_search"),
    path("parametros/criar/", analises.parametro_create, name="parametro_create"),
    path("parametros/<int:pk>/status/", analises.parametro_toggle, name="parametro_toggle"),
    path("padroes/criar/", analises.padrao_create, name="padrao_create"),
    path("padroes/<int:pk>/editar/", analises.padrao_update, name="padrao_update"),
    path("padroes/<int:pk>/status/", analises.padrao_toggle, name="padrao_toggle"),
    path("padroes/aplicar/", analises.aplicar_padroes_produto, name="aplicar_padroes_produto"),
]
