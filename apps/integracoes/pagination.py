from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class PaginacaoIntegracao(PageNumberPagination):
    page_size = 50
    page_size_query_param = 'por_pagina'
    max_page_size = 200

    def get_paginated_response(self, data):
        return Response({
            'dados': data,
            'paginacao': {
                'pagina': self.page.number,
                'por_pagina': self.get_page_size(self.request),
                'total': self.page.paginator.count,
                'paginas': self.page.paginator.num_pages,
                'proxima': self.get_next_link(),
                'anterior': self.get_previous_link(),
            },
        })
