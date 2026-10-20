import csv

from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import render
from django.views import View

from apps.core.services.permissions import PermissaoRequiredMixin
from apps.estoque.services.produtos_sem_giro_service import ProdutosSemGiroService


class ProdutosSemGiroRelatorioView(PermissaoRequiredMixin, View):
    permissao_modulo = 'estoque'
    permissao_acao = 'ver'
    template_name = 'estoque/relatorios/produtos_sem_giro.html'

    def dispatch(self, request, *args, **kwargs):
        self.permissao_acao = 'exportar' if request.GET.get('export') == 'csv' else 'ver'
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        filial = request.filial_ativa
        faixa = request.GET.get('faixa', '')
        busca = request.GET.get('q', '').strip()

        todos = ProdutosSemGiroService.analisar(filial)
        resumo = ProdutosSemGiroService.resumo(todos)
        itens = ProdutosSemGiroService.filtrar(todos, faixa=faixa, busca=busca)

        if request.GET.get('export') == 'csv':
            return self._exportar_csv(itens, filial)

        page_obj = Paginator(itens, 50).get_page(request.GET.get('page'))
        querydict = request.GET.copy()
        querydict.pop('page', None)
        querydict.pop('export', None)

        return render(request, self.template_name, {
            'filial': filial,
            'resumo': resumo,
            'itens': page_obj.object_list,
            'page_obj': page_obj,
            'faixa': faixa if faixa in {'60_89', '90_mais', 'nunca'} else '',
            'busca': busca,
            'total_filtrado': len(itens),
            'page_querystring': querydict.urlencode(),
            'export_querystring': querydict.urlencode(),
        })

    @staticmethod
    def _exportar_csv(itens, filial):
        response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
        response['Content-Disposition'] = 'attachment; filename="produtos-sem-giro.csv"'
        response.write('\ufeff')
        writer = csv.writer(response, delimiter=';')
        writer.writerow([
            'Filial', 'Código', 'Produto', 'Categoria', 'Estoque atual',
            'Última venda', 'Dias sem venda', 'Faixa', 'Canal da última venda',
            'Valor em custo',
        ])
        nome_filial = filial.nome_fantasia or filial.razao_social
        for item in itens:
            writer.writerow([
                nome_filial,
                item['codigo'],
                item['descricao'],
                item['categoria'],
                str(item['estoque_atual']).replace('.', ','),
                item['ultima_venda'].strftime('%d/%m/%Y') if item['ultima_venda'] else 'Nunca vendido',
                item['dias_sem_venda'] if item['dias_sem_venda'] is not None else '',
                item['faixa_label'],
                item['canal_ultima_venda'],
                str(item['valor_custo']).replace('.', ','),
            ])
        return response
