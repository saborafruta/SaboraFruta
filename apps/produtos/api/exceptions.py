"""Formato de erro padronizado da API de produtos.

Escopado so a esta API (via `BaseProdutosAPIView.get_exception_handler`),
NAO trocado globalmente no `REST_FRAMEWORK.EXCEPTION_HANDLER` -- isso
mudaria o formato de erro de `apps.integracoes` (consumida por terceiros,
contrato externo) e `apps.estoque.api` sem necessidade.

Formato: `{"erros": [{"codigo": ..., "mensagem": ..., "campo": ...}]}`.
`codigo` vem do `ErrorDetail.code` do DRF quando disponivel (ex:
'required', 'invalid', 'max_length'); `campo` e' o nome do campo do
serializer ou `None` para erros gerais (permissao, autenticacao, 404).
"""
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler


def erro_response(mensagem, *, codigo='erro', campo=None, status=400):
    """Monta uma Response no mesmo formato de `formatar_erros_api`, pra
    erros levantados direto na view (DomainError/DadosInvalidosError e
    afins, que o exception_handler do DRF nao reconhece por nao serem
    APIException)."""
    return Response({'erros': [{'codigo': codigo, 'mensagem': mensagem, 'campo': campo}]}, status=status)


def _extrair_erros(dados, campo=None):
    erros = []
    if isinstance(dados, dict):
        for chave, valor in dados.items():
            nome_campo = None if chave in ('detail', 'non_field_errors') else chave
            erros.extend(_extrair_erros(valor, campo=nome_campo))
    elif isinstance(dados, list):
        for item in dados:
            erros.extend(_extrair_erros(item, campo=campo))
    else:
        codigo = getattr(dados, 'code', None) or 'erro'
        erros.append({'codigo': codigo, 'mensagem': str(dados), 'campo': campo})
    return erros


def formatar_erros_api(exc, context):
    """Exception handler do DRF, reformatando a resposta padrao em `erros`."""
    response = drf_exception_handler(exc, context)
    if response is None:
        return None
    response.data = {'erros': _extrair_erros(response.data)}
    return response
