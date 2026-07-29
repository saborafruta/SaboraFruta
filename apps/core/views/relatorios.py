"""Central de Relatorios — ponto unico de acesso a todos os relatorios do
sistema, protegido pelo modulo de permissao 'relatorios' (perfil admin e
superuser sempre tem acesso; demais usuarios precisam da permissao liberada
em Gestao > Perfis)."""
from django.views.generic import TemplateView

from apps.core.services.permissions import PermissaoRequiredMixin


class RelatoriosHubView(PermissaoRequiredMixin, TemplateView):
    template_name = 'core/relatorios_hub.html'
    permissao_modulo = 'relatorios'
    permissao_acao = 'ver'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['title'] = 'Relatórios'
        ctx['grupos'] = [
            {
                'titulo': 'Vendas',
                'itens': [
                    {
                        'nome': 'Histórico de Vendas',
                        'descricao': 'Notas/cupons emitidos, com situação fiscal e filtros de período.',
                        'url_name': 'analytics:vendas',
                    },
                    {
                        'nome': 'Curva ABC — Clientes',
                        'descricao': 'Classificação A/B/C dos clientes por receita, com período customizável.',
                        'url_name': 'core:dashboard-curva-abc-relatorio',
                        'query': 'tipo=clientes',
                    },
                    {
                        'nome': 'Curva ABC — Produtos',
                        'descricao': 'Classificação A/B/C dos produtos por receita, com período customizável.',
                        'url_name': 'core:dashboard-curva-abc-relatorio',
                        'query': 'tipo=produtos',
                    },
                ],
            },
            {
                'titulo': 'Delivery',
                'itens': [
                    {
                        'nome': 'Relatório de Delivery',
                        'descricao': 'Pedidos do Kanban de delivery por período, com status de pagamento.',
                        'url_name': 'pdv:delivery_relatorio',
                    },
                ],
            },
            {
                'titulo': 'Financeiro',
                'itens': [
                    {
                        'nome': 'Contas a Receber',
                        'descricao': 'Títulos a receber agrupados por cliente, com a venda vinculada.',
                        'url_name': 'financeiro:receber_relatorio',
                    },
                    {
                        'nome': 'Contas a Pagar',
                        'descricao': 'Títulos a pagar agrupados por fornecedor, com a nota de entrada vinculada.',
                        'url_name': 'financeiro:pagar_relatorio',
                    },
                ],
            },
            {
                'titulo': 'Estoque e Produção',
                'itens': [
                    {
                        'nome': 'Relatórios de Estoque',
                        'descricao': 'Posição de estoque, valor de custo e comparativo entre filiais.',
                        'url_name': 'estoque:relatorio-list',
                    },
                    {
                        'nome': 'Dashboard Operacional',
                        'descricao': 'OPs em andamento, alertas de vencimento e rendimento por linha de produção.',
                        'url_name': 'analytics:operacional',
                    },
                ],
            },
            {
                'titulo': 'CRM',
                'itens': [
                    {
                        'nome': 'Alertas de Recompra',
                        'descricao': 'Clientes com padrão de recompra atrasado ou vencendo, priorizados por score.',
                        'url_name': 'crm:recompra',
                    },
                ],
            },
        ]
        return ctx
