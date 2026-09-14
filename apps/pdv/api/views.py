"""
API REST de vendas do PDV.

Autenticacao: sessao do proprio ERP, mesmo padrao/motivo de
apps.produtos.api e apps.estoque.api (arquitetura multi-tenant sem login
JWT tenant-aware ainda). RBAC via `TemPermissaoPDV`
(`usuario.tem_permissao('pdv', acao)`).

Montada em `/api/pdv/`, no mesmo padrao de `/api/estoque/equalizacao/` e
`/api/produtos/`.

ESCOPO (decisao registrada): so venda de balcao/caixa (PDV, baixa
imediata), chamando `VendaPDVService.finalizar_venda` -- NAO o pedido B2B
de `apps.vendas` (reserva -> separacao -> faturamento). Investigacao
prévia mostrou dois dominios de venda bem diferentes, cada um com service
proprio ja testado; reimplementar a regra de negocio aqui (preco com
promocao/kit/tabela, FEFO, conta a receber) divergiria de producao. Esta
view e' uma casca fina: traduz o payload da API pros kwargs que o service
ja aceita, e devolve o resultado formatado.

APRESENTACAO NA VENDA (novidade desta fase -- nenhum fluxo de venda
existente usa isso hoje): cada item pode vir com `apresentacao_id` +
`quantidade_comercial` em vez de `quantidade` direto. A CONVERSAO
acontece aqui, na borda, via `ApresentacaoService` -- `quantidade`
(unidade base) e' o que chega no `VendaPDVService`, que continua sem
saber o que e' uma apresentacao. Efeito colateral aceito: o
`ItemVendaPDV` resultante grava so a unidade base (como sempre gravou) --
a apresentacao/fator usados na hora da venda NAO ficam registrados no
item para auditoria futura. Registrar isso no proprio ItemVendaPDV
(colunas novas, ou snapshot) ficou fora do escopo desta fase.

IDEMPOTENCIA: o campo `VendaPDV.idempotency_key` existe no model desde
antes desta fase mas nunca foi checado em lugar nenhum (nem no fluxo
HTML existente do PDV). Esta e' a PRIMEIRA vez que ele e' usado de
verdade. Header opcional `Idempotency-Key`: se ja existir uma venda com
essa chave, devolve ela (200) em vez de criar outra. Limitacao honesta:
isso cobre o caso comum (cliente reenvia apos timeout de rede, bem depois
da primeira requisicao ja ter terminado) mas NAO e' um lock atomico --
duas requisicoes verdadeiramente simultaneas com a mesma chave ainda
podem, em tese, criar duas vendas antes que a constraint UNIQUE barre a
segunda gravacao da chave (nesse caso a segunda venda fica sem
idempotency_key salva e um erro e' devolvido, mas ela ja existe no banco
com estoque baixado -- precisaria de estorno manual). Mesma familia de
tradeoff documentado no cache do lookup de codigo de barras (Fase 12):
decisao consciente de nao construir um lock distribuido para isto agora.
"""
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError
from drf_spectacular.utils import extend_schema
from rest_framework.authentication import SessionAuthentication
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.services.exceptions import DadosInvalidosError, EstoqueInsuficienteError
from apps.core.services.request_scope import empresa_operacional
from apps.pdv.models import SessaoPDV, VendaPDV
from apps.pdv.services.venda_pdv_service import VendaPDVService
from apps.produtos.api.exceptions import erro_response as _erro
from apps.produtos.api.exceptions import formatar_erros_api
from apps.produtos.models import Produto, ProdutoApresentacao
from apps.produtos.services.apresentacao_service import ApresentacaoService
from apps.produtos.services.conversao import quantizar

from .permissions import TemPermissaoPDV
from .serializers import VendaCreateInputSerializer, VendaPDVSerializer
from .throttling import ThrottleVendaPDV


def _sessao_aberta(request, filial):
    return SessaoPDV.objects.for_filial(filial).filter(usuario=request.user, status='aberto').first()


def _converter_apresentacao_para_base(item, empresa):
    """Se o item veio com apresentacao_id+quantidade_comercial, resolve a
    apresentacao e devolve o item com `quantidade` em unidade base. Levanta
    DadosInvalidosError se a apresentacao nao existir/nao pertencer ao
    produto informado."""
    apresentacao_id = item.get('apresentacao_id')
    if not apresentacao_id:
        return item

    quantidade_comercial = item.get('quantidade_comercial')
    if quantidade_comercial in (None, ''):
        raise DadosInvalidosError('Informe quantidade_comercial junto com apresentacao_id.')

    try:
        apresentacao = ProdutoApresentacao.objects.select_related('unidade', 'produto').get(
            pk=apresentacao_id, produto_id=item['produto_id'], produto__in=Produto.objects.for_empresa(empresa),
            ativo=True,
        )
    except ProdutoApresentacao.DoesNotExist:
        raise DadosInvalidosError(
            f'Apresentacao {apresentacao_id} nao encontrada para o produto {item["produto_id"]}.',
        )
    if not apresentacao.permite_venda:
        raise DadosInvalidosError(f'A apresentacao "{apresentacao.descricao}" nao pode ser vendida.')

    try:
        quantidade_base = ApresentacaoService.converter(apresentacao, quantidade_comercial, para='base')
    except (InvalidOperation, ValueError):
        raise DadosInvalidosError(f'quantidade_comercial invalida: {quantidade_comercial!r}.')

    novo_item = {k: v for k, v in item.items() if k not in ('apresentacao_id', 'quantidade_comercial')}
    novo_item['quantidade'] = str(quantidade_base)
    # Regra 5.8 (preco por apresentacao e' independente, nunca
    # preco_unitario_base * fator): manda o preco da apresentacao como
    # `preco_manual`, o unico jeito que VendaPDVService aceita um preco
    # explicito -- senao ele resolveria o preco pela PrecoService usando
    # o preco do produto na unidade BASE multiplicado pela quantidade_base
    # convertida, ignorando que a caixa pode ter preco proprio (desconto de
    # atacado, por exemplo). So aplica o default quando o chamador nao
    # mandou o proprio preco_manual.
    #
    # ATENCAO: `preco_manual`, assim como todo `valor_unitario` do sistema,
    # e' SEMPRE por unidade BASE (o service multiplica preco x quantidade,
    # e quantidade aqui ja' esta' em base) -- por isso divide pelo
    # fator_conversao. Mandar `apresentacao.preco_venda` direto cobraria
    # o preco da caixa inteira por CADA unidade base (10x a mais no
    # exemplo de uma caixa com fator 10).
    if novo_item.get('preco_manual') in (None, ''):
        preco_por_unidade_base = quantizar(apresentacao.preco_venda / apresentacao.fator_conversao, 4)
        novo_item['preco_manual'] = str(preco_por_unidade_base)
    return novo_item


class VendaCreateView(APIView):
    """POST /api/pdv/vendas/"""

    authentication_classes = [SessionAuthentication]
    permission_classes = [TemPermissaoPDV]
    permissao_acao = 'criar'
    throttle_classes = [ThrottleVendaPDV]

    def get_exception_handler(self):
        return formatar_erros_api

    @extend_schema(request=VendaCreateInputSerializer, responses={201: VendaPDVSerializer, 200: VendaPDVSerializer})
    def post(self, request):
        filial = getattr(request, 'filial_ativa', None)
        if filial is None:
            return _erro('Nenhuma filial ativa selecionada nesta sessao.')

        chave_idempotencia = request.headers.get('Idempotency-Key', '').strip()
        if chave_idempotencia:
            venda_existente = VendaPDV.objects.filter(idempotency_key=chave_idempotencia).first()
            if venda_existente:
                return Response(VendaPDVSerializer(venda_existente).data, status=200)

        sessao = _sessao_aberta(request, filial)
        if sessao is None:
            return _erro('Nenhuma sessao de caixa aberta para este usuario nesta filial.')

        entrada = VendaCreateInputSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        dados = entrada.validated_data
        empresa = empresa_operacional(request)

        try:
            itens = [_converter_apresentacao_para_base(item, empresa) for item in dados['itens']]
        except DadosInvalidosError as exc:
            return _erro(str(exc), codigo='apresentacao_invalida', campo='itens')

        try:
            venda = VendaPDVService.finalizar_venda(
                sessao=sessao,
                filial=filial,
                usuario=request.user,
                itens=itens,
                pagamentos=list(dados.get('pagamentos') or []),
                cliente_id=dados.get('cliente_id'),
                desconto=Decimal(str(dados.get('desconto') or '0')),
                acrescimo=Decimal(str(dados.get('acrescimo') or '0')),
                observacao=dados.get('observacao') or '',
                bonificacao=dados.get('bonificacao') or False,
                forcar_estoque_negativo=dados.get('forcar_estoque_negativo', True),
                request=request,
            )
        except EstoqueInsuficienteError as exc:
            return _erro(str(exc), codigo='estoque_insuficiente', campo=None)
        except DadosInvalidosError as exc:
            return _erro(str(exc))

        if chave_idempotencia:
            venda.idempotency_key = chave_idempotencia
            try:
                venda.save(update_fields=['idempotency_key'])
            except IntegrityError:
                # Corrida real: outra requisicao com a mesma chave venceu
                # entre a checagem la em cima e este save. A venda que
                # acabamos de criar e' de verdade (estoque ja baixado) --
                # ver docstring do modulo sobre esse limite conhecido.
                return _erro(
                    'Requisicao concorrente com a mesma Idempotency-Key. '
                    f'Venda {venda.numero_venda} foi criada; verifique manualmente possivel duplicidade.',
                    codigo='idempotencia_concorrente', status=409,
                )

        return Response(VendaPDVSerializer(venda).data, status=201)
