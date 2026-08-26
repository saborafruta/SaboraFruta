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

from . import (
    views_armazenagem as varm, views_etiqueta as vetq, views_frio as vfrio,
    views_indicadores as vind, views_tempo_real as vtr,
    views, views_catalogo as vcat, views_ordem as vord,
    views_planejamento as vpla, views_processo as vproc,
    views_etapa as veta, views_subproduto as vsub,
    views_receita as vrec_receita, views_recebimento as vrec,
)

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
    # O endereco e' o que o menu ja' aponta: assim que esta rota resolve
    # para uma view que nao e' a `ItemView`, o selo "em construcao" some
    # sozinho do hub -- ele e' descoberto por resolucao, nao por lista.
    path('recebimento/classificacao/', vrec.ClassificacaoFilaView.as_view(), name='recebimento-classificacao'),
    path('recebimento/produtores/', vrec.ProdutoresView.as_view(), name='recebimento-produtores'),
    path('recebimento/recusas/', vrec.RecusasView.as_view(), name='recebimento-recusas'),

    # ── Catálogo da fábrica ─────────────────────────────────────────────
    # Três itens do menu, UMA tela: "Produtos" abre nos acabados,
    # "Embalagens" nos materiais de embalagem, e a lista completa fica em
    # `catalogo-list`. São o mesmo cadastro com exigências diferentes, e
    # telas separadas dariam três lugares para procurar o mesmo item.
    path('formulacao/produtos/', vcat.ProdutosAcabadosView.as_view(), name='produto-acabado-list'),
    path('formulacao/embalagens/', vcat.EmbalagensView.as_view(), name='embalagem-list'),
    path('formulacao/materias-primas/', vcat.MateriasPrimasView.as_view(), name='materia-prima-list'),
    path('catalogo/', vcat.CatalogoListView.as_view(), name='catalogo-list'),
    path('catalogo/novo/', vcat.CatalogoFormView.as_view(), name='catalogo-create'),
    path('catalogo/<int:pk>/editar/', vcat.CatalogoFormView.as_view(), name='catalogo-update'),

    # ── Formulações ─────────────────────────────────────────────────────
    # `receitas` é o endereço do menu; as ações da ficha penduram nele.
    path('formulacao/receitas/', vrec_receita.ReceitaListView.as_view(), name='receita-list'),
    path('formulacao/receitas/nova/', vrec_receita.ReceitaFormView.as_view(), name='receita-create'),
    path('formulacao/receitas/<int:pk>/', vrec_receita.ReceitaDetailView.as_view(), name='receita-detail'),
    path('formulacao/receitas/<int:pk>/editar/', vrec_receita.ReceitaFormView.as_view(), name='receita-update'),
    path('formulacao/receitas/<int:pk>/itens/', vrec_receita.ItemAddView.as_view(), name='receita-item-add'),
    path('formulacao/receitas/<int:pk>/itens/<int:item_pk>/remover/', vrec_receita.ItemRemoveView.as_view(), name='receita-item-remove'),
    path('formulacao/receitas/<int:pk>/etapas/', vrec_receita.EtapaAddView.as_view(), name='receita-etapa-add'),
    path('formulacao/receitas/<int:pk>/etapas/<int:etapa_pk>/remover/', vrec_receita.EtapaRemoveView.as_view(), name='receita-etapa-remove'),
    path('formulacao/receitas/<int:pk>/nova-versao/', vrec_receita.NovaVersaoView.as_view(), name='receita-nova-versao'),
    path('formulacao/receitas/<int:pk>/ativar/', vrec_receita.AtivarView.as_view(), name='receita-ativar'),

    # ── Ordens de produção ──────────────────────────────────────────────
    # O endereco e' o que o menu ja' aponta: assim que resolve para uma view
    # que nao e' a `ItemView`, o selo "em construcao" some sozinho do hub.
    path('producao/batidas/', vord.BatidasView.as_view(), name='batida-list'),
    path('producao/perdas/', vsub.PerdasView.as_view(), name='perda-list'),
    # Endereco que o menu ja' aponta: assim que resolve para uma view que
    # nao e' a `ItemView`, o selo "em construcao" some sozinho do hub.
    path('qualidade/analises/', vord.AnalisesQualidadeView.as_view(), name='qualidade-analises'),
    path('qualidade/laudos/', vord.LaudosView.as_view(), name='qualidade-laudos'),
    path('qualidade/laudos/<int:pk>/pdf/', vord.LaudoPdfView.as_view(), name='qualidade-laudo-pdf'),
    path('qualidade/nao-conformidades/', vord.NaoConformidadesView.as_view(), name='qualidade-nao-conformidades'),
    path('qualidade/rastreabilidade/', vord.RastreabilidadeView.as_view(), name='qualidade-rastreabilidade'),
    path('producao/ordens/', vord.OrdemListView.as_view(), name='ordem-list'),
    path('producao/ordens/nova/', vord.OrdemFormView.as_view(), name='ordem-create'),
    path('producao/ordens/<int:pk>/', vord.OrdemDetailView.as_view(), name='ordem-detail'),
    path('producao/ordens/<int:pk>/mover/', vord.MoverView.as_view(), name='ordem-mover'),
    path('producao/ordens/<int:pk>/concluir/', vord.ConcluirView.as_view(), name='ordem-concluir'),

    # ── Processo produtivo ──────────────────────────────────────────────
    # "Etapas" é a fila do que falta apontar; "Apontamento" é o mesmo quadro
    # mostrando também o que já foi apontado, para conferir e corrigir.
    path('producao/etapas/', vproc.FilaEtapasView.as_view(), name='processo-fila'),
    path('producao/apontamento/', vproc.ApontamentoView.as_view(), name='processo-apontamento'),
    path('producao/ordens/<int:pk>/processo/', vproc.ProcessoDaOrdemView.as_view(), name='processo-ordem'),

    # SUBPRODUTOS NA TELA DA ORDEM: quem sabe que saíram 500 kg de casca
    # é quem estava na linha, olhando aquela ordem.
    path('producao/ordens/<int:pk>/subprodutos/', vsub.subproduto_registrar, name='subproduto-registrar'),
    path('producao/ordens/<int:pk>/subprodutos/<int:subproduto_pk>/excluir/', vsub.subproduto_excluir, name='subproduto-excluir'),
    path('producao/etapas/<int:pk>/apontar/', vproc.ApontarEtapaView.as_view(), name='etapa-apontar'),

    # ── Cadeia de frio ──────────────────────────────────────────────────
    # O estoque de produto acabado: onde cada lote está e quando vence. O
    # saldo continua sendo do ERP -- aqui mora a localização e o prazo.
    path('frio/estoque-frio/', varm.EstoqueFrioView.as_view(), name='estoque-frio'),
    path('frio/estoque-frio/<int:pk>/guardar/', varm.GuardarLoteView.as_view(), name='lote-guardar'),
    path('frio/estoque-frio/<int:pk>/bloquear/', varm.BloquearLoteView.as_view(), name='lote-bloquear'),
    # ── Etiquetas ───────────────────────────────────────────────────────
    # O único pedaço do sistema que sai da fábrica: vai colado no saco que
    # chega ao supermercado, e é o que a fiscalização lê.
    path('frio/etiquetas/', vetq.EtiquetaListView.as_view(), name='etiqueta-list'),
    path('frio/etiquetas/<int:pk>/', vetq.EtiquetaLoteView.as_view(), name='etiqueta-lote'),
    path('frio/etiquetas/<int:pk>/qr.png', vetq.QrLoteView.as_view(), name='etiqueta-qr'),
    path('frio/etiquetas/<int:pk>/barras.svg', vetq.BarrasLoteView.as_view(), name='etiqueta-barras'),

    path('frio/tunel/', vfrio.TunelView.as_view(), name='tunel'),
    path('frio/temperatura/', vfrio.TemperaturaView.as_view(), name='temperatura'),
    path('frio/alertas/', vfrio.AlertasFrioView.as_view(), name='alertas-frio'),
    path('frio/camaras/', varm.CamaraListView.as_view(), name='camara-list'),
    path('frio/camaras/<int:pk>/mapa/', vfrio.MapaCamaraView.as_view(), name='camara-mapa'),
    path('frio/camaras/<int:pk>/posicoes/', vfrio.PosicaoCreateView.as_view(), name='posicao-create'),
    path('frio/lotes/<int:pk>/mover/', vfrio.MoverLoteView.as_view(), name='lote-mover'),
    path('frio/camaras/nova/', varm.CamaraFormView.as_view(), name='camara-create'),
    path('frio/camaras/<int:pk>/editar/', varm.CamaraFormView.as_view(), name='camara-update'),
    # ── Indicadores ─────────────────────────────────────────────────────
    # Nenhum número nasce lá: o painel junta o que já foi registrado por
    # quem fez o trabalho.
    path('indicadores/painel/', vind.PainelView.as_view(), name='painel'),
    # A TELA DE PAREDE: outro dono que o painel industrial -- este e' de
    # quem esta' produzindo agora, e a pergunta e' "estamos no ritmo?".
    path('indicadores/hoje/', vtr.TempoRealView.as_view(), name='tempo-real'),
    path('indicadores/metas/', vtr.MetaListView.as_view(), name='meta-list'),
    path('indicadores/metas/<int:pk>/', vtr.MetaUpdateView.as_view(), name='meta-update'),
    path('indicadores/validade/', varm.ValidadeView.as_view(), name='validade'),

    # ── PPCP ────────────────────────────────────────────────────────────
    # Três olhares sobre a MESMA produção: o que produzir, quando e onde
    # está. Nenhum guarda estado próprio — por isso não podem discordar.
    path('pcp/planejamento/', vpla.PlanejamentoView.as_view(), name='planejamento'),
    path('pcp/planejamento/gerar/', vpla.GerarOrdemView.as_view(), name='planejamento-gerar'),
    path('pcp/calendario/', vpla.CalendarioView.as_view(), name='calendario'),
    path('pcp/calendario/<int:pk>/programar/', vpla.ProgramarView.as_view(), name='ordem-programar'),
    path('pcp/quadro/', vpla.KanbanView.as_view(), name='kanban'),
    path('pcp/recursos/', vpla.RecursoListView.as_view(), name='recurso-list'),

    # AS ETAPAS QUE A CASA CRIA. Fica em PCP, ao lado de linhas e
    # máquinas: é cadastro de como a fábrica é, não operação do dia.
    # O CAMINHO CASA COM O SLUG DO MENU (`pcp/etapas-processo`): e' assim
    # que o hub descobre que a tela existe -- ele resolve a rota do item e
    # checa se caiu no placeholder. Divergir aqui deixaria o selo "em breve"
    # numa tela pronta.
    path('pcp/etapas-processo/', veta.EtapaListView.as_view(), name='etapa-list'),
    path('pcp/etapas-processo/nova/', veta.EtapaFormView.as_view(), name='etapa-nova'),
    path('pcp/etapas-processo/<int:pk>/', veta.EtapaFormView.as_view(), name='etapa-editar'),
    path('pcp/recursos/novo/', vpla.RecursoFormView.as_view(), name='recurso-create'),
    path('pcp/recursos/<int:pk>/editar/', vpla.RecursoFormView.as_view(), name='recurso-update'),

    # ── Cadastro das frutas ─────────────────────────────────────────────
    path('formulacao/rendimento/', vrec.FrutaListView.as_view(), name='fruta-list'),
    path('formulacao/rendimento/nova/', vrec.FrutaFormView.as_view(), name='fruta-create'),
    # Cadastro relampago da fruta, chamado de dentro do romaneio.
    path('frutas/ajax-create/', vrec.FrutaAjaxCreateView.as_view(), name='fruta-ajax-create'),
    path('formulacao/rendimento/<int:pk>/editar/', vrec.FrutaFormView.as_view(), name='fruta-update'),
    path('formulacao/rendimento/<int:pk>/toggle-ativo/', vrec.FrutaToggleAtivoView.as_view(), name='fruta-toggle-ativo'),
    path('formulacao/rendimento/<int:pk>/excluir/', vrec.FrutaDeleteView.as_view(), name='fruta-delete'),
]

urlpatterns = [
    path('', views.HubView.as_view(), name='hub'),
    *ROTAS_PRONTAS,
    path('<slug:grupo_slug>/', views.GrupoView.as_view(), name='grupo'),
    path('<slug:grupo_slug>/<slug:item_slug>/', views.ItemView.as_view(), name='item'),
]
