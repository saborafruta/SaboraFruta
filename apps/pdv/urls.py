from django.urls import path
from apps.pdv.views import pdv

app_name = "pdv"

urlpatterns = [
    path("", pdv.pdv_home, name="home"),
    path("vendas/", pdv.vendas_list, name="vendas_list"),
    path("orcamentos/", pdv.orcamentos_list, name="orcamentos_list"),
    # Busca
    path("api/produtos/", pdv.buscar_produto, name="api_produtos"),
    path("api/clientes/", pdv.buscar_cliente, name="api_clientes"),
    # Estado e caixa
    path("api/estado/", pdv.api_estado, name="api_estado"),
    path("api/caixa/criar/", pdv.api_caixa_criar, name="api_caixa_criar"),
    path("api/caixa/abrir/", pdv.api_caixa_abrir, name="api_caixa_abrir"),
    path("api/caixa/resumo/", pdv.api_caixa_resumo, name="api_caixa_resumo"),
    path("api/caixa/relatorio-data/", pdv.api_caixa_relatorio_data, name="api_caixa_relatorio_data"),
    path("api/caixa/movimentacao/", pdv.api_caixa_movimentacao, name="api_caixa_movimentacao"),
    path("api/caixa/fechar/", pdv.api_caixa_fechar, name="api_caixa_fechar"),
    # Vendas
    path("api/venda/finalizar/", pdv.api_venda_finalizar, name="api_venda_finalizar"),
    path("api/venda/finalizar/forcado/", pdv.api_venda_finalizar_forcado, name="api_venda_finalizar_forcado"),
    path("api/venda/pendente/", pdv.api_venda_pendente, name="api_venda_pendente"),
    path("api/venda/orcamento/", pdv.api_venda_orcamento, name="api_venda_orcamento"),
    path("api/pendentes/", pdv.api_pendentes, name="api_pendentes"),
    path("api/pendentes/<int:pk>/detalhe/", pdv.api_pendente_detalhe, name="api_pendente_detalhe"),
    path("api/pendentes/<int:pk>/cancelar/", pdv.api_pendente_cancelar, name="api_pendente_cancelar"),
    # Orçamentos
    path("api/orcamentos/", pdv.api_orcamentos, name="api_orcamentos"),
    path("api/orcamentos/<int:pk>/detalhe/", pdv.api_orcamento_detalhe, name="api_orcamento_detalhe"),
    path("api/orcamentos/<int:pk>/cancelar/", pdv.api_orcamento_cancelar, name="api_orcamento_cancelar"),
    path("api/orcamentos/<int:pk>/retomar/", pdv.api_orcamento_retomar, name="api_orcamento_retomar"),
    path("api/historico/", pdv.api_historico, name="api_historico"),
    path("api/historico/cliente/<int:cliente_id>/", pdv.api_historico_cliente, name="api_historico_cliente"),
    # Configurações / Formas de Pagamento
    path("api/formas-pagamento/", pdv.api_formas_pagamento, name="api_formas_pagamento"),
    # Clientes
    path("api/cliente/criar/", pdv.api_cliente_criar, name="api_cliente_criar"),
    path("api/clientes/debug/", pdv.api_clientes_debug, name="api_clientes_debug"),
    # Delivery
    path("delivery/", pdv.delivery_kanban, name="delivery"),
    path("delivery/<int:pk>/mover/", pdv.delivery_mover, name="delivery_mover"),
    path("delivery/<int:pk>/atualizar/", pdv.delivery_atualizar, name="delivery_atualizar"),
    # Venda finalizada — detalhe e cancelamento
    path("api/venda/<int:pk>/detalhe/", pdv.api_venda_detalhe, name="api_venda_detalhe"),
    path("api/venda/<int:pk>/cancelar/", pdv.api_venda_cancelar, name="api_venda_cancelar"),
    # Fiscal — NFC-e / NF-e
    path("api/venda/<int:pk>/emitir-nfce/", pdv.api_emitir_nfce, name="api_emitir_nfce"),
    path("api/venda/<int:pk>/cancelar-nfce/", pdv.api_cancelar_nfce, name="api_cancelar_nfce"),
    path("api/venda/<int:pk>/preview-nfce/", pdv.api_preview_nfce, name="api_preview_nfce"),
]
