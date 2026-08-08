"""
Secoes do menu que podem ser ligadas/desligadas por filial (tela
"gestao/modulos/"). A chave e' o mesmo nome usado no Alpine `secoes.<chave>`
do sidebar (`apps/core/templates/core/_sidebar.html`) -- e' uma granularidade
mais grossa que `Permissao.Modulo` (que e' por app, nao por secao do menu).
"""

SECOES_MODULOS = [
    ('cadastros', 'Cadastros', 'Clientes, fornecedores e produtos.'),
    ('operacoes', 'Operações', 'Estoque, compras, produção e lotes.'),
    ('financeiro', 'Financeiro', 'Contas a pagar/receber, fluxo de caixa, conciliação e DRE.'),
    ('logistica', 'Logística', 'Romaneios, coletas, manifestos, CT-e/MDF-e.'),
    ('avancado', 'Avançado', 'PDV, fiscal, qualidade e relatórios (Analytics).'),
    ('food_service', 'Food Service', 'Mesas, comandas, cozinha (KDS) e cardápio digital.'),
]

# Prefixos de URL cobertos por cada secao -- usado pelo FilialMiddleware pra
# bloquear acesso direto a uma secao desativada (nao so escondida do menu).
PREFIXOS_POR_MODULO = {
    'cadastros': ('/cadastros/', '/produtos/'),
    'operacoes': ('/estoque/', '/compras/', '/producao/', '/lotes/'),
    'financeiro': ('/financeiro/',),
    'logistica': ('/logistica/',),
    'avancado': ('/pdv/', '/fiscal/', '/qualidade/', '/analytics/'),
    'food_service': ('/food-service/',),
}
