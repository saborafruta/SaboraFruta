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

from . import (
    views, views_apoio as va, views_cadastros as vc, views_ficha as vf,
    views_corte as vco, views_encaixe as ve, views_fluxo as vx,
    views_ordem as vo, views_pcp as vp,
    views_expedicao as vex, views_kanban as vk, views_necessidade as vn,
    views_alertas as val, views_aprovacao as vap,
    views_dashboard as vd, views_historico as vh, views_qualidade as vq, views_qr as vqr,
    views_roteiro as vr, views_terminal as vt, views_wip as vw,
)

app_name = 'moda'

# Telas já implementadas — declaradas ANTES do catch-all, senão o
# placeholder as engoliria. O endereço é o mesmo que o menu já aponta.
ROTAS_PRONTAS: list = [
    path('comercial/pedidos/', vc.PedidoListView.as_view(), name='pedido-list'),
    path('comercial/pedidos/novo/', vc.PedidoFormView.as_view(), name='pedido-create'),
    path('comercial/pedidos/<int:pk>/', vc.PedidoDetailView.as_view(), name='pedido-detail'),
    path('comercial/pedidos/<int:pk>/editar/', vc.PedidoFormView.as_view(), name='pedido-update'),
    path('comercial/pedidos/<int:pk>/pdf/', vc.PedidoPdfView.as_view(), name='pedido-pdf'),
    path('comercial/pedidos/<int:pk>/historico/', vh.HistoricoPedidoView.as_view(), name='pedido-historico'),
    path('comercial/pedidos/<int:pk>/fluxo/', vap.FluxoPedidoView.as_view(), name='pedido-fluxo'),
    path('comercial/pedidos/<int:pk>/aprovacao/', vap.AprovacaoPedidoView.as_view(), name='pedido-aprovacao'),
    path('comercial/pedidos/<int:pk>/aprovacao/liberar/', vap.LiberarPedidoView.as_view(), name='pedido-liberar'),
    path('comercial/pedidos/<int:pk>/finalizar/', vc.PedidoFinalizarView.as_view(), name='pedido-finalizar'),
    path('comercial/pedidos/<int:pk>/status/', vc.PedidoStatusView.as_view(), name='pedido-status'),
    path('comercial/pedidos/<int:pk>/valores/', vc.PedidoValoresView.as_view(), name='pedido-valores'),
    path('comercial/pedidos/<int:pk>/financeiro/', vc.PedidoFinanceiroGerarView.as_view(), name='pedido-financeiro-gerar'),
    path('comercial/pedidos/<int:pk>/financeiro/cancelar/', vc.PedidoFinanceiroCancelarView.as_view(), name='pedido-financeiro-cancelar'),
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
    path('comercial/pedidos/<int:pk>/pessoas/', vc.IndividualFormView.as_view(), name='pedido-individual-add'),
    path('comercial/pedidos/<int:pk>/pessoas/<int:individual_pk>/', vc.IndividualFormView.as_view(), name='pedido-individual-update'),
    path('comercial/pedidos/<int:pk>/pessoas/<int:individual_pk>/remover/', vc.IndividualDeleteView.as_view(), name='pedido-individual-delete'),
    path('comercial/pedidos/<int:pk>/pessoas/importar/', vc.IndividualImportarView.as_view(), name='pedido-individual-importar'),

    # Necessidade de materiais. Endereco do menu de PCP.
    path('pcp/necessidade-materiais/', vn.NecessidadeView.as_view(), name='necessidade'),
    path('pcp/necessidade-materiais/reservar/', vn.ReservarView.as_view(), name='necessidade-reservar'),
    path('pcp/necessidade-materiais/reservas/<int:pk>/cancelar/', vn.CancelarReservaView.as_view(), name='necessidade-reserva-cancelar'),
    path('pcp/necessidade-materiais/requisicao/', vn.RequisicaoGerarView.as_view(), name='requisicao-gerar'),
    path('pcp/necessidade-materiais/requisicao/<int:pk>/', vn.RequisicaoDetailView.as_view(), name='requisicao-detail'),
    path('pcp/necessidade-materiais/requisicao/<int:pk>/status/', vn.RequisicaoStatusView.as_view(), name='requisicao-status'),

    # Kanban. Endereco do menu de Producao; a movimentacao responde JSON
    # para o arrasto nao recarregar a pagina a cada solta.
    path('producao/kanban/', vk.KanbanView.as_view(), name='kanban'),
    path('producao/kanban/<int:pk>/mover/', vk.KanbanMoverView.as_view(), name='kanban-mover'),

    # WIP. Fica no grupo Relatorios, que e' o endereco que o menu aponta.
    # Indicadores. O endereco tem que ser o que o MENU aponta
    # (`indicadores/<item>`): o WIP estava em `relatorios/wip/` e o link do
    # menu caia no placeholder de 'em construcao' -- a tela existia e nao
    # tinha como chegar nela. O endereco antigo continua respondendo para
    # nao quebrar link salvo.
    path('indicadores/dashboard/', vd.DashboardView.as_view(), name='dashboard'),
    path('indicadores/alertas/', val.AlertasView.as_view(), name='alertas'),
    path('indicadores/wip/', vw.WipView.as_view(), name='wip'),
    path('relatorios/wip/', vw.WipView.as_view(), name='wip-antigo'),

    # Encaixe. Cadastro proprio: o mesmo risco serve a todos os enfestos
    # daquele modelo, entao nao pendura no corte.
    path('producao/encaixe/', ve.EncaixeListView.as_view(), name='encaixe-list'),
    path('producao/encaixe/novo/', ve.EncaixeFormView.as_view(), name='encaixe-create'),
    path('producao/encaixe/<int:pk>/', ve.EncaixeDetailView.as_view(), name='encaixe-detail'),
    path('producao/encaixe/<int:pk>/editar/', ve.EncaixeFormView.as_view(), name='encaixe-update'),

    # Controle de corte. A lista fica no endereço do menu (producao/corte).
    path('producao/corte/', vco.CorteListView.as_view(), name='corte-list'),
    path('producao/corte/novo/', vco.CorteFormView.as_view(), name='corte-create'),
    path('producao/corte/<int:pk>/', vco.CorteDetailView.as_view(), name='corte-detail'),
    path('producao/corte/<int:pk>/editar/', vco.CorteFormView.as_view(), name='corte-update'),
    path('producao/corte/<int:pk>/grade/', vco.CorteGradeView.as_view(), name='corte-grade'),
    path('producao/corte/<int:pk>/remover/', vco.CorteDeleteView.as_view(), name='corte-delete'),

    # Fluxo de produção. O painel fica no grupo Produção; o fluxo de cada
    # ordem pendura na OP, que é a quem as etapas pertencem.
    path('producao/fluxo/', vx.PainelFluxoView.as_view(), name='fluxo-painel'),

    # Expedicao. Grupo proprio no menu; `separacao` e' o endereco de
    # entrada porque e' o primeiro item do grupo.
    path('expedicao/separacao/', vex.ExpedicaoListView.as_view(), name='expedicao-list'),
    path('expedicao/buscar/', vex.ExpedicaoBuscarView.as_view(), name='expedicao-buscar'),
    path('expedicao/ordens/<int:pk>/', vex.ExpedicaoCriarView.as_view(), name='expedicao-criar'),
    path('expedicao/<int:pk>/', vex.ExpedicaoDetailView.as_view(), name='expedicao-detail'),
    path('expedicao/<int:pk>/conferir/', vex.ExpedicaoConferirView.as_view(), name='expedicao-conferir'),
    path('expedicao/<int:pk>/avancar/', vex.ExpedicaoAvancarView.as_view(), name='expedicao-avancar'),
    path('expedicao/<int:pk>/cancelar/', vex.ExpedicaoCancelarView.as_view(), name='expedicao-cancelar'),
    path('expedicao/<int:pk>/volumes/', vex.VolumeCriarView.as_view(), name='expedicao-volume-criar'),
    path('expedicao/<int:pk>/volumes/<int:volume_pk>/remover/', vex.VolumeRemoverView.as_view(), name='expedicao-volume-remover'),
    path('expedicao/<int:pk>/volumes/<int:volume_pk>/etiqueta/', vex.EtiquetaView.as_view(), name='expedicao-etiqueta'),

    # Qualidade. Endereco do menu; declarado antes do catch-all dos
    # terminais, senao `producao/<slug>/` engoliria e devolveria 404.
    path('producao/qualidade/', vq.QualidadeListView.as_view(), name='qualidade-list'),
    path('producao/qualidade/ordens/<int:pk>/', vq.InspecaoCriarView.as_view(), name='inspecao-criar'),
    path('producao/qualidade/<int:pk>/', vq.InspecaoDetailView.as_view(), name='inspecao-detail'),
    path('producao/qualidade/<int:pk>/checklist/', vq.InspecaoChecklistView.as_view(), name='inspecao-checklist'),
    path('producao/qualidade/<int:pk>/decidir/', vq.InspecaoDecidirView.as_view(), name='inspecao-decidir'),
    path('producao/qualidade/<int:pk>/aplicar/', vq.InspecaoAplicarView.as_view(), name='inspecao-aplicar'),

    # QR Code. O endereco que o QR guarda e' `/q/<codigo>/`, fora daqui --
    # estas rotas sao as do lado de dentro: gerar o desenho, imprimir a
    # etiqueta e buscar por codigo digitado ou lido por leitor de bancada.
    path('escanear/', vqr.QrEscanearView.as_view(), name='qr-escanear'),
    path('qr/<str:codigo>/imagem.png', vqr.QrImagemView.as_view(), name='qr-imagem'),
    path('qr/<str:codigo>/etiqueta/', vqr.QrEtiquetaView.as_view(), name='qr-etiqueta'),

    # Terminais de setor. Ficam DEPOIS de todas as rotas especificas de
    # /producao/ porque `<slug:slug>` casa com qualquer coisa: declarado
    # antes, engoliria corte, encaixe, fluxo e kanban de uma vez.
    # O corte tem endereco proprio (/apontamento/) porque /producao/corte/
    # ja e' o controle de enfesto, que e' outra tela.
    path('producao/corte/apontamento/', vt.TerminalView.as_view(), {'slug': 'corte'}, name='terminal-corte'),
    path('producao/<slug:slug>/apontar/<int:pk>/', vt.TerminalApontarView.as_view(), name='terminal-apontar'),
    path('producao/<slug:slug>/', vt.TerminalView.as_view(), name='terminal'),
    path('pcp/ordens-producao/<int:pk>/fluxo/', vx.FluxoOrdemView.as_view(), name='fluxo-ordem'),
    path('pcp/ordens-producao/<int:pk>/fluxo/<int:etapa_pk>/', vx.ApontarView.as_view(), name='fluxo-apontar'),

    # Cadastros de apoio. A LISTA nao tem rota propria: ela e' entregue
    # pela rota do menu (`moda:item`), pra o endereco nao mudar quando a
    # tela sai do placeholder. So criar/editar precisam de rota.
    path('<slug:grupo>/<slug:slug>/novo/', va.CadastroApoioFormView.as_view(), name='apoio-create'),
    path('<slug:grupo>/<slug:slug>/<int:pk>/editar/', va.CadastroApoioFormView.as_view(), name='apoio-update'),

    # Ficha técnica. A lista fica no endereço do menu
    # (/moda/engenharia/ficha-tecnica/), declarada antes do catch-all para
    # o placeholder não engolir — mesmo motivo das rotas de pedido.
    path('engenharia/ficha-tecnica/', vf.FichaListView.as_view(), name='ficha-list'),
    path('engenharia/ficha-tecnica/nova/', vf.FichaFormView.as_view(), name='ficha-create'),
    path('engenharia/ficha-tecnica/<int:pk>/', vf.FichaDetailView.as_view(), name='ficha-detail'),
    path('engenharia/ficha-tecnica/<int:pk>/editar/', vf.FichaFormView.as_view(), name='ficha-update'),
    path('engenharia/ficha-tecnica/<int:pk>/materiais/', vf.MaterialCreateView.as_view(), name='ficha-material-add'),
    path('engenharia/ficha-tecnica/<int:pk>/materiais/salvar/', vf.MaterialUpdateView.as_view(), name='ficha-material-salvar'),
    path('engenharia/ficha-tecnica/<int:pk>/materiais/<int:material_pk>/remover/', vf.MaterialDeleteView.as_view(), name='ficha-material-delete'),
    path('engenharia/ficha-tecnica/<int:pk>/imagens/', vf.ImagemCreateView.as_view(), name='ficha-imagem-add'),
    path('engenharia/ficha-tecnica/<int:pk>/imagens/<int:imagem_pk>/remover/', vf.ImagemDeleteView.as_view(), name='ficha-imagem-delete'),

    # Roteiro de produção. A lista fica no endereço do menu
    # (/moda/engenharia/sequencia-producao/), pelo mesmo motivo da ficha.
    path('engenharia/sequencia-producao/', vr.RoteiroListView.as_view(), name='roteiro-list'),
    path('engenharia/sequencia-producao/novo/', vr.RoteiroFormView.as_view(), name='roteiro-create'),
    path('engenharia/sequencia-producao/<int:pk>/', vr.RoteiroDetailView.as_view(), name='roteiro-detail'),
    path('engenharia/sequencia-producao/<int:pk>/editar/', vr.RoteiroFormView.as_view(), name='roteiro-update'),
    path('engenharia/sequencia-producao/<int:pk>/copiar/', vr.RoteiroCopiarView.as_view(), name='roteiro-copiar'),
    path('engenharia/sequencia-producao/<int:pk>/etapas/', vr.EtapaCreateView.as_view(), name='roteiro-etapa-add'),
    path('engenharia/sequencia-producao/<int:pk>/etapas/salvar/', vr.EtapaUpdateView.as_view(), name='roteiro-etapa-salvar'),
    path('engenharia/sequencia-producao/<int:pk>/etapas/<int:etapa_pk>/remover/', vr.EtapaDeleteView.as_view(), name='roteiro-etapa-delete'),

    # PCP. Cada rota fica no endereço que o menu já aponta, antes do
    # catch-all — mesmo motivo das telas de Engenharia.
    path('pcp/ordens-producao/', vo.OrdemListView.as_view(), name='ordem-list'),
    path('pcp/ordens-producao/<int:pk>/', vo.OrdemDetailView.as_view(), name='ordem-detail'),
    path('pcp/ordens-producao/<int:pk>/editar/', vo.OrdemEditarView.as_view(), name='ordem-editar'),
    path('pcp/ordens-producao/<int:pk>/status/', vo.OrdemStatusView.as_view(), name='ordem-status'),
    path('pcp/ordens-producao/<int:pk>/ficha.pdf', vo.FichaProducaoPdfView.as_view(), name='ordem-ficha-pdf'),
    path('pcp/ordens-producao/<int:pk>/historico/', vh.HistoricoOrdemView.as_view(), name='ordem-historico'),
    path('comercial/pedidos/<int:pedido_pk>/ordens/', vo.OrdemGerarView.as_view(), name='ordem-gerar'),

    path('pcp/planejamento/', vp.PlanejamentoView.as_view(), name='pcp-planejamento'),
    path('pcp/capacidade/', vp.CapacidadeView.as_view(), name='pcp-capacidade'),
    path('pcp/capacidade/<int:pk>/', vp.CapacidadeUpdateView.as_view(), name='pcp-capacidade-update'),
    path('pcp/capacidade/<int:pk>/remover/', vp.CapacidadeDeleteView.as_view(), name='pcp-capacidade-delete'),
    path('pcp/programacao/', vp.ProgramacaoView.as_view(), name='pcp-programacao'),
    path('pcp/priorizacao/', vp.PriorizacaoView.as_view(), name='pcp-priorizacao'),
    path('pcp/acompanhamento/', vp.AcompanhamentoView.as_view(), name='pcp-acompanhamento'),

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
