from django.urls import path
from apps.pdv.views import pdv
from apps.pdv.views.comprovante_publico import baixar_pdf_interno, criar_link, visualizar_interno

app_name = "pdv"

urlpatterns = [
    path('api/venda/<int:pk>/comprovante-link/', criar_link, name='api_comprovante_link'),
    path('venda/<int:pk>/comprovante/', visualizar_interno, name='comprovante_venda'),
    path('venda/<int:pk>/comprovante/pdf/', baixar_pdf_interno, name='comprovante_venda_pdf'),
    path('venda/<int:pk>/etiqueta/', pdv.etiqueta_venda, name='etiqueta_venda'),
    path("", pdv.pdv_home, name="home"),
    path("checkout/", pdv.checkout_venda, name="checkout"),
    path("vendas/", pdv.vendas_list, name="vendas_list"),
    path("orcamentos/", pdv.orcamentos_list, name="orcamentos_list"),
    # Pré-venda — vendedor externo, sem caixa, sem pagamento
    path("pre-venda/nova/", pdv.pre_venda_nova, name="pre_venda_nova"),
    path("api/pre-venda/criar/", pdv.api_pre_venda_criar, name="api_pre_venda_criar"),
    # Busca
    path("api/produtos/", pdv.buscar_produto, name="api_produtos"),
    path("api/checkout/produtos/", pdv.checkout_buscar_produto, name="api_checkout_produtos"),
    path(
        "api/checkout/liberar-busca-nome/",
        pdv.checkout_liberar_busca_nome,
        name="api_checkout_liberar_busca_nome",
    ),
    path(
        "api/checkout/liberar-desconto/",
        pdv.checkout_liberar_desconto,
        name="api_checkout_liberar_desconto",
    ),
    path(
        "api/checkout/consumir-busca-nome/",
        pdv.checkout_consumir_busca_nome,
        name="api_checkout_consumir_busca_nome",
    ),
    path(
        "api/checkout/encerrar-busca-nome/",
        pdv.checkout_encerrar_busca_nome,
        name="api_checkout_encerrar_busca_nome",
    ),
    path("api/clientes/", pdv.buscar_cliente, name="api_clientes"),
    # Estado e caixa
    path("api/estado/", pdv.api_estado, name="api_estado"),
    path("api/offline/instalacao/", pdv.api_instalacao_offline, name="api_instalacao_offline"),
    path("api/precos-cliente/", pdv.api_precos_cliente, name="api_precos_cliente"),
    path("api/caixa/criar/", pdv.api_caixa_criar, name="api_caixa_criar"),
    path("api/caixa/abrir/", pdv.api_caixa_abrir, name="api_caixa_abrir"),
    path("api/caixa/resumo/", pdv.api_caixa_resumo, name="api_caixa_resumo"),
    path("api/caixa/relatorio-data/", pdv.api_caixa_relatorio_data, name="api_caixa_relatorio_data"),
    path("api/caixa/movimentacao/", pdv.api_caixa_movimentacao, name="api_caixa_movimentacao"),
    path("api/caixa/fechar/", pdv.api_caixa_fechar, name="api_caixa_fechar"),
    # Vendas
    path("api/venda/finalizar/", pdv.api_venda_finalizar, name="api_venda_finalizar"),
    path("api/checkout/venda/finalizar/", pdv.api_checkout_venda_finalizar, name="api_checkout_venda_finalizar"),
    path("api/venda/finalizar/forcado/", pdv.api_venda_finalizar_forcado, name="api_venda_finalizar_forcado"),
    path("api/viagens-remessa-aberta/", pdv.api_viagens_remessa_aberta, name="api_viagens_remessa_aberta"),
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
    # Crédito de cliente
    path("api/credito-cliente/", pdv.api_credito_cliente, name="api_credito_cliente"),
    # Saldo de cashback
    path("api/cashback-saldo/", pdv.api_cashback_saldo, name="api_cashback_saldo"),
    # Clientes
    path("api/cliente/criar/", pdv.api_cliente_criar, name="api_cliente_criar"),
    path("api/cliente/<int:cliente_id>/editar/", pdv.api_cliente_editar, name="api_cliente_editar"),
    path("api/clientes/debug/", pdv.api_clientes_debug, name="api_clientes_debug"),
    # Delivery
    path("delivery/", pdv.delivery_kanban, name="delivery"),
    path("delivery/rotas/", pdv.delivery_rotas, name="delivery_rotas"),
    path("delivery/rotas/calcular/", pdv.delivery_rota_calcular, name="delivery_rota_calcular"),
    path("delivery/rotas/publicar/", pdv.delivery_rota_publicar, name="delivery_rota_publicar"),
    path("delivery/rotas/configuracao/", pdv.delivery_rota_salvar_configuracao, name="delivery_rota_salvar_configuracao"),
    path("delivery/rotas/oportunidades/", pdv.delivery_rota_oportunidades, name="delivery_rota_oportunidades"),
    path("delivery/rotas/parada-manual/localizar/", pdv.delivery_rota_localizar_parada_manual, name="delivery_rota_localizar_parada_manual"),
    path(
        "delivery/rotas/<int:pk>/endereco/",
        pdv.delivery_rota_atualizar_endereco,
        name="delivery_rota_atualizar_endereco",
    ),
    path("delivery/relatorio/", pdv.delivery_relatorio, name="delivery_relatorio"),
    path("delivery/relatorio/imprimir/", pdv.delivery_relatorio_pagina, name="delivery_relatorio_pagina"),
    path("delivery/<int:pk>/mover/", pdv.delivery_mover, name="delivery_mover"),
    path("delivery/<int:pk>/atualizar/", pdv.delivery_atualizar, name="delivery_atualizar"),
    # Venda finalizada — detalhe e cancelamento
    path("api/venda/<int:pk>/detalhe/", pdv.api_venda_detalhe, name="api_venda_detalhe"),
    path("api/venda/<int:pk>/cancelar/", pdv.api_venda_cancelar, name="api_venda_cancelar"),
    # Fiscal — NFC-e / NF-e
    path("api/inutilizar-faixa/", pdv.api_inutilizar_faixa, name="api_inutilizar_faixa"),
    path("api/venda/<int:pk>/prontidao-fiscal/", pdv.api_prontidao_fiscal, name="api_prontidao_fiscal"),
    path("api/venda/<int:pk>/emitir-nfce/", pdv.api_emitir_nfce, name="api_emitir_nfce"),
    path("api/venda/<int:pk>/emitir-nfce-contingencia/", pdv.api_emitir_nfce_contingencia, name="api_emitir_nfce_contingencia"),
    path("api/venda/<int:pk>/cancelar-nfce/", pdv.api_cancelar_nfce, name="api_cancelar_nfce"),
    path("api/venda/<int:pk>/preview-nfce/", pdv.api_preview_nfce, name="api_preview_nfce"),
    path("api/venda/<int:pk>/email-nfce/", pdv.api_email_nfce, name="api_email_nfce"),
    path("api/venda/<int:pk>/xml-nfce/", pdv.api_xml_nfce, name="api_xml_nfce"),
    path("api/venda/<int:pk>/emitir-nfe/", pdv.api_emitir_nfe, name="api_emitir_nfe"),
    path("api/venda/<int:pk>/cancelar-venda/", pdv.api_cancelar_venda_historico, name="api_cancelar_venda_historico"),
]
