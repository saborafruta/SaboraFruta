"""Workspace da OP 2.0. A tela antiga continua disponível sem alterações."""
from copy import copy
from datetime import date
from decimal import Decimal, InvalidOperation
from io import BytesIO
from urllib.parse import quote
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from django.contrib import messages
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.models import Cliente
from apps.core.services.exceptions import DadosInvalidosError, DomainError
from apps.financeiro.models import ContaBancaria, FormaPagamento

from .forms import ItemPedidoProducaoForm, PersonalizacaoIndividualForm
from .forms_arquivo import ArquivoPedidoForm
from .forms_cliente import ClienteRapidoForm
from .models import (
    AprovacaoPedido, ArquivoPedido, Grade, ItemGradePedido, ItemPedidoProducao,
    OpcaoEstruturaOP2, PedidoProducao, Personalizacao, PersonalizacaoIndividual,
    Posicao, ProdutoModa, Tamanho, VisualItemPedido,
)
from .services.historico import HistoricoService
from .services.financeiro import FinanceiroPedidoService
from .services.grade_pedido import GradePedidoService
from .services.individual import IndividualService
from .services.kanban_comercial import status_choices_kanban, status_destino_kanban
from .services.op2_estrutura import (
    OP2_ESTRUTURA_OPCOES, juntar_observacoes_item, opcoes_estrutura_filial,
    sincronizar_opcoes_padrao,
)
from .services.pedido_pdf import whatsapp_numero
from .views import ModaBaseView


def _filial(request):
    return request.filial_ativa


def _pedido(request, pk):
    return get_object_or_404(
        PedidoProducao.objects.for_filial(_filial(request)).select_related(
            'cliente', 'vendedor', 'filial',
        ), pk=pk,
    )


def _voltar(pedido):
    return redirect(reverse('moda:op2-detail', args=[pedido.pk]))


def _mensagem_op2(pedido, link):
    cliente = pedido.cliente.nome_fantasia or pedido.cliente.razao_social
    entrega = pedido.data_prevista_entrega.strftime('%d/%m/%Y') if pedido.data_prevista_entrega else 'a combinar'
    if pedido.status == PedidoProducao.Status.ORCAMENTO:
        return (
            f'Olá, {cliente}!\n\nSegue o orçamento #{pedido.numero:06d} para sua análise.\n'
            f'Confira os itens, valores e aprove pelo link:\n{link}\n\nPrazo previsto: {entrega}.'
        )
    return (
        f'Olá, {cliente}!\n\nA arte e os dados da OP #{pedido.numero:06d} estão prontos para aprovação.\n'
        f'Confira e responda pelo link:\n{link}\n\nPrazo previsto: {entrega}.'
    )


def _normalizar_tipo_impressao(valor):
    """Converte o rótulo editável da estrutura no valor interno conhecido."""
    texto = (valor or '').strip()
    comparado = texto.casefold()
    for chave, rotulo in ProdutoModa.TipoImpressao.choices:
        if comparado in (str(chave).casefold(), str(rotulo).casefold()):
            return chave
    return texto


def _cliente_json(cliente):
    return {
        'id': str(cliente.pk),
        'nome': cliente.nome_display,
        'documento': cliente.cpf_cnpj or '',
        'contato': cliente.contato_nome or '',
        'telefone': cliente.celular or cliente.telefone or '',
        'texto': ' '.join(filter(None, [
            cliente.nome_display,
            cliente.razao_social,
            cliente.nome_fantasia,
            cliente.cpf_cnpj,
            cliente.contato_nome,
            cliente.celular,
            cliente.telefone,
        ])),
    }


def _observacoes_pedido(request):
    observacoes = (request.POST.get('observacoes') or '').strip()
    extras = []
    nomes = request.POST.getlist('contato_extra_nome')
    telefones = request.POST.getlist('contato_extra_telefone')
    for nome, telefone in zip(nomes, telefones):
        nome = (nome or '').strip()
        telefone = (telefone or '').strip()
        if nome or telefone:
            extras.append(f'- {nome or "Contato"}: {telefone}')
    if extras:
        bloco = 'Contatos extras:\n' + '\n'.join(extras)
        return '\n\n'.join(parte for parte in (observacoes, bloco) if parte)
    return observacoes


def _dados_modal_item(item, estrutura_opcoes):
    """Serializa um item para o mesmo editor completo usado ao adicioná-lo."""
    texto = (item.observacoes or '').strip()
    marcador = 'Estrutura da peça:'
    observacoes = texto
    estrutura_tipo = next(iter(estrutura_opcoes), 'camisa')
    estrutura = {}
    if marcador in texto:
        observacoes, bloco = texto.split(marcador, 1)
        observacoes = observacoes.strip()
        linhas = [linha.strip() for linha in bloco.splitlines() if linha.strip()]
        rotulos = {
            str(grupo.get('label') or slug).strip().casefold(): slug
            for slug, grupo in estrutura_opcoes.items()
        }
        for linha in linhas:
            if ':' not in linha:
                continue
            chave, valor = (parte.strip() for parte in linha.split(':', 1))
            if chave.casefold() == 'tipo de peça':
                estrutura_tipo = rotulos.get(valor.casefold(), estrutura_tipo)
                continue
            campo = '_'.join(chave.casefold().split())
            estrutura[campo] = valor

    arte = item.personalizacoes.first()
    grade_id = str(item.grade_tamanho_id or '')
    quantidades = {
        str(linha.tamanho_id): linha.quantidade for linha in item.grade.all()
    }
    return {
        'item_id': str(item.pk),
        'produto_id': str(item.produto_id or ''),
        'nome': item.nome_exibicao,
        'codigo': item.produto.codigo if item.produto_id else '',
        'info': ' · '.join(filter(None, [
            item.produto.codigo if item.produto_id else '',
            item.referencia,
            item.grade_tamanho.nome if item.grade_tamanho_id else '',
        ])),
        'quantidade': item.quantidade,
        'valor_unitario': str(item.valor_unitario),
        'tipo_impressao': (
            (
                arte.get_tecnica_display() if arte else
                getattr(item.produto, 'get_tipo_impressao_display', lambda: '')()
            ).upper()
        ),
        'estrutura_tipo': estrutura_tipo,
        'estrutura': estrutura,
        'item_observacoes': observacoes,
        'grades': [grade_id] if grade_id else [],
        'gradePorGrade': {grade_id: quantidades} if grade_id else {},
    }


def _sincronizar_status(pedido):
    """A OP acompanha o item pendente menos avançado, sem esconder atrasos."""
    if pedido.status == PedidoProducao.Status.CANCELADO:
        return
    itens = list(pedido.itens.all())
    if not itens:
        novo = PedidoProducao.Status.ORCAMENTO
    elif all(i.status_fluxo == i.StatusFluxo.ENTREGUE for i in itens):
        novo = PedidoProducao.Status.ENTREGUE
    else:
        pendentes = [i for i in itens if i.status_fluxo != i.StatusFluxo.ENTREGUE]
        ordem = {
            ItemPedidoProducao.StatusFluxo.ORCAMENTO: 0,
            ItemPedidoProducao.StatusFluxo.APROVADO: 1,
            ItemPedidoProducao.StatusFluxo.PRODUCAO: 2,
            ItemPedidoProducao.StatusFluxo.PRONTO: 3,
        }
        menor = min(pendentes, key=lambda item: ordem.get(item.status_fluxo, 0))
        novo = {
            menor.StatusFluxo.ORCAMENTO: PedidoProducao.Status.ORCAMENTO,
            menor.StatusFluxo.APROVADO: PedidoProducao.Status.CONFIRMADO,
            menor.StatusFluxo.PRODUCAO: PedidoProducao.Status.EM_PRODUCAO,
            menor.StatusFluxo.PRONTO: PedidoProducao.Status.PRONTO,
        }[menor.status_fluxo]
    if pedido.status != novo:
        pedido.status = novo
        pedido.save(update_fields=['status', 'updated_at'])


class Op2CreateView(ModaBaseView):
    area = 'comercial'
    permissao_acao = 'criar'

    def get(self, request):
        return render(request, 'moda/op2_create.html', self._context(request))

    def post(self, request):
        cliente_id = request.POST.get('cliente')
        if not cliente_id:
            messages.error(request, 'Selecione um cliente na lista antes de salvar a OP.')
            return render(request, 'moda/op2_create.html', self._context(request))
        cliente = get_object_or_404(
            Cliente.objects.for_filial(_filial(request)).filter(ativo=True),
            pk=cliente_id,
        )
        indices = self._indices_itens(request)
        if not indices:
            messages.error(request, 'Adicione ao menos um modelo de produção à OP.')
            return render(request, 'moda/op2_create.html', self._context(request))

        formularios = []
        for indice in indices:
            dados = self._dados_item(request, indice)
            try:
                grade_total = self._total_grade(request, indice)
            except ValueError as erro:
                messages.error(request, str(erro))
                return render(request, 'moda/op2_create.html', self._context(request))
            if grade_total and int(dados.get('quantidade') or 0) != grade_total:
                messages.error(
                    request,
                    f'Item {indice + 1}: a quantidade total deve ser igual à soma da grade ({grade_total}).',
                )
                return render(request, 'moda/op2_create.html', self._context(request))
            form = ItemPedidoProducaoForm(dados, filial=_filial(request))
            if not form.is_valid():
                messages.error(request, f'Item {indice + 1}: ' + '; '.join(
                    erro for erros in form.errors.values() for erro in erros
                ))
                return render(request, 'moda/op2_create.html', self._context(request))
            formularios.append((indice, form))
        try:
            data_prevista_entrega = self._data_entrega(request)
            data_pedido = self._data_pedido(request)
        except ValueError as erro:
            messages.error(request, str(erro))
            return render(request, 'moda/op2_create.html', self._context(request))
        try:
            individuais = self._dados_individuais(request, indices)
        except ValueError as erro:
            messages.error(request, str(erro))
            return render(request, 'moda/op2_create.html', self._context(request))

        with transaction.atomic():
            pedido = PedidoProducao.objects.create(
                filial=_filial(request), cliente=cliente, vendedor=request.user,
                status=PedidoProducao.Status.ORCAMENTO,
                contato_nome=request.POST.get('contato_nome') or cliente.contato_nome or '',
                contato_telefone=(
                    request.POST.get('contato_telefone')
                    or cliente.celular or cliente.telefone or ''
                ),
                data_pedido=data_pedido,
                data_prevista_entrega=data_prevista_entrega,
                prioridade=request.POST.get('prioridade') or PedidoProducao.Prioridade.NORMAL,
                observacoes=_observacoes_pedido(request),
            )
            primeiro_item = None
            itens_por_indice = {}
            for ordem, (indice, form) in enumerate(formularios, start=1):
                item = form.save(commit=False)
                item.pedido = pedido
                item.ordem = ordem * 10
                item.status_fluxo = ItemPedidoProducao.StatusFluxo.ORCAMENTO
                item.grade_tamanho_id = request.POST.get(f'item_{indice}_grade_id') or None
                item.observacoes = juntar_observacoes_item(
                    request.POST.get(f'item_{indice}_item_observacoes') or '',
                    self._post_item(request, indice),
                    opcoes_estrutura_filial(_filial(request)),
                )
                item.save()
                primeiro_item = primeiro_item or item
                itens_por_indice[indice] = item

                self._copiar_grade_do_modelo(item)
                quantidades = self._quantidades_grade(
                    request, item, prefixos=(f'item_{indice}_grade_',),
                )
                if quantidades:
                    GradePedidoService.salvar_quantidades(pedido, quantidades)

                chave_tipo = f'item_{indice}_tipo_impressao'
                tipo_impressao = (
                    request.POST.get(chave_tipo)
                    if chave_tipo in request.POST else (
                        request.POST.get(f'item_{indice}_arte_tecnica')
                        or getattr(item.produto, 'tipo_impressao', '')
                    )
                )
                if tipo_impressao:
                    Personalizacao.objects.create(
                        item=item,
                        tipo=Personalizacao.Tipo.ARTE,
                        tecnica=_normalizar_tipo_impressao(tipo_impressao),
                    )

                self._salvar_mockups_do_item(
                    request, pedido, item, campo_generico=f'item_{indice}_imagens',
                    incluir_legado=False,
                )

            for ordem, pessoa in enumerate(individuais, start=1):
                PersonalizacaoIndividual.objects.create(
                    pedido=pedido,
                    item=itens_por_indice[pessoa['item_idx']],
                    tamanho_id=pessoa['tamanho_id'],
                    nome=pessoa['nome'],
                    numero=pessoa['numero'],
                    ordem=ordem * 10,
                )

            arquivos = request.FILES.getlist('arquivo')
            for upload in arquivos:
                ArquivoPedido.objects.create(
                    pedido=pedido, arquivo=upload,
                    tipo=request.POST.get('tipo_arquivo') or ArquivoPedido.Tipo.ARTE,
                    descricao=(request.POST.get('descricao_arquivo') or '').strip(),
                    enviado_por=request.user,
                )
            if primeiro_item:
                self._salvar_mockups_do_item(request, pedido, primeiro_item)

            destino = request.POST.get('destino') or 'salvar'
            if destino == 'enviar':
                aprovacao, _ = AprovacaoPedido.objects.get_or_create(pedido=pedido)
                aprovacao.liberar(
                    request.user, 'Orçamento salvo e enviado ao cliente pela OP 2.0.',
                )

        messages.success(request, f'Rascunho #{pedido.numero:06d} criado.')
        if destino == 'enviar':
            link = request.build_absolute_uri(
                reverse('moda_publico:pedido', args=[pedido.token_publico])
            )
            numero = whatsapp_numero(pedido)
            if numero:
                return render(request, 'moda/op2_enviar_whatsapp.html', {
                    'op_url': reverse('moda:op2-detail', args=[pedido.pk]),
                    'whatsapp_url': (
                        f'https://wa.me/{numero}?text={quote(_mensagem_op2(pedido, link))}'
                    ),
                })
            return render(request, 'moda/op2_enviar_whatsapp.html', {
                'op_url': reverse('moda:op2-detail', args=[pedido.pk]) + '?whatsapp=1',
                'whatsapp_url': '',
            })
        if destino == 'pdf':
            return redirect('moda:pedido-orcamento-pdf', pk=pedido.pk)
        return _voltar(pedido)

    @staticmethod
    def _context(request):
        tamanhos = list(
            Tamanho.objects.for_filial(_filial(request)).filter(
                ativo=True,
            ).order_by('tipo', 'ordem', 'sigla')
        )
        modelos = list(
            ProdutoModa.objects.for_filial(_filial(request)).filter(
                ativo=True,
            ).select_related('modelo', 'tecido', 'grade').prefetch_related(
                'grade__itens__tamanho',
            ).order_by('nome')
        )
        clientes = list(
            Cliente.objects.for_filial(_filial(request)).filter(
                ativo=True,
            ).order_by('razao_social')
        )
        return {
            'title': 'Nova OP 2.0',
            'clientes': clientes,
            'clientes_json': [_cliente_json(cliente) for cliente in clientes],
            'form_cliente': ClienteRapidoForm(),
            'pode_criar_cliente': request.user.tem_permissao('cadastros', 'criar'),
            'modelos': modelos,
            'modelos_grade': {
                str(produto.pk): {
                    'grade_id': str(produto.grade_id or ''),
                    'grade': produto.grade.nome if produto.grade_id else '',
                    'tipo_impressao': produto.tipo_impressao,
                    'tipo_impressao_label': produto.get_tipo_impressao_display().upper(),
                    'tamanhos': [
                        str(i.tamanho_id) for i in produto.grade.itens.all()
                    ] if produto.grade_id else [],
                }
                for produto in modelos
            },
            'grades_json': [
                {
                    'id': str(grade.pk),
                    'nome': grade.nome,
                    'tipo': grade.get_tipo_display(),
                    'resumo': grade.resumo,
                    'tamanhos': [str(item.tamanho_id) for item in grade.itens.all()],
                }
                for grade in Grade.objects.for_filial(_filial(request)).filter(
                    ativo=True,
                ).prefetch_related('itens__tamanho').order_by('tipo', 'nome')
            ],
            'estrutura_opcoes': opcoes_estrutura_filial(_filial(request)),
            'tamanhos': tamanhos,
            'tamanhos_labels': {
                str(tamanho.pk): tamanho.sigla for tamanho in tamanhos
            },
            'tipos_impressao': ProdutoModa.TipoImpressao.choices,
            'tipos_arquivo': ArquivoPedido.Tipo.choices,
            'prioridades': PedidoProducao.Prioridade.choices,
            'hoje': timezone.localdate(),
        }

    @staticmethod
    def _indices_itens(request):
        indices = set()
        for chave in request.POST:
            if not chave.startswith('item_'):
                continue
            partes = chave.split('_', 2)
            if len(partes) == 3 and partes[1].isdigit():
                indices.add(int(partes[1]))
        return sorted(indices)

    @staticmethod
    def _post_item(request, indice):
        dados = request.POST.copy()
        prefixo = f'item_{indice}_'
        for chave, valor in request.POST.items():
            if chave.startswith(prefixo):
                dados[chave.removeprefix(prefixo)] = valor
        return dados

    @classmethod
    def _dados_item(cls, request, indice):
        dados = cls._post_item(request, indice)
        produto_id = dados.get('produto_id')
        dados['produto'] = f'moda:{produto_id}' if produto_id else ''
        dados['observacoes'] = dados.get('item_observacoes') or ''
        return dados

    @staticmethod
    def _total_grade(request, indice):
        total = 0
        prefixo = f'item_{indice}_grade_'
        for chave, valor in request.POST.items():
            if not chave.startswith(prefixo):
                continue
            if not chave.removeprefix(prefixo).isdigit():
                continue
            try:
                qtd = int(valor or 0)
            except (TypeError, ValueError):
                raise ValueError('Grade: informe apenas números nas quantidades.')
            if qtd < 0:
                raise ValueError('Grade: quantidade não pode ser negativa.')
            total += qtd
        return total

    @staticmethod
    def _dados_individuais(request, indices):
        """Valida a lista de nomes/números ainda no orçamento."""
        linhas = []
        ocupadas = {}
        indice = 0
        permitidos = set(indices)
        while f'individual_{indice}_item_idx' in request.POST:
            item_idx_texto = request.POST.get(f'individual_{indice}_item_idx') or ''
            tamanho_texto = request.POST.get(f'individual_{indice}_tamanho_id') or ''
            nome = (request.POST.get(f'individual_{indice}_nome') or '').strip()
            numero = (request.POST.get(f'individual_{indice}_numero') or '').strip()
            if not item_idx_texto.isdigit() or int(item_idx_texto) not in permitidos:
                raise ValueError('Personalização: selecione um produto válido.')
            if not tamanho_texto.isdigit():
                raise ValueError('Personalização: selecione um tamanho válido.')
            if not nome and not numero:
                raise ValueError('Personalização: informe o nome ou o número.')
            item_idx = int(item_idx_texto)
            tamanho_id = int(tamanho_texto)
            try:
                limite = int(request.POST.get(
                    f'item_{item_idx}_grade_{tamanho_id}', '0',
                ) or 0)
            except (TypeError, ValueError):
                limite = 0
            chave = (item_idx, tamanho_id)
            ocupadas[chave] = ocupadas.get(chave, 0) + 1
            if limite < ocupadas[chave]:
                raise ValueError(
                    'Personalização: esse tamanho já atingiu a quantidade da grade.'
                )
            linhas.append({
                'item_idx': item_idx, 'tamanho_id': tamanho_id,
                'nome': nome[:80], 'numero': numero[:10],
            })
            indice += 1
        return linhas

    @staticmethod
    def _copiar_grade_do_modelo(item):
        try:
            GradePedidoService.aplicar_grade_do_produto(item)
        except DadosInvalidosError:
            return 0
        return item.grade.count()

    @staticmethod
    def _quantidades_grade(request, item, incluir_zeros=False, prefixos=None):
        quantidades = {}
        prefixos = prefixos or (f'grade_{item.pk}_', 'grade_')
        for chave, valor in request.POST.items():
            prefixo = next((p for p in prefixos if chave.startswith(p)), None)
            if not prefixo:
                continue
            tamanho_id = chave.removeprefix(prefixo)
            if not tamanho_id.isdigit():
                continue
            try:
                qtd = int(valor or 0)
            except (TypeError, ValueError):
                raise ValueError('Grade: informe apenas números nas quantidades.')
            if qtd < 0:
                raise ValueError('Grade: quantidade não pode ser negativa.')
            if incluir_zeros or qtd > 0:
                quantidades[(item.pk, int(tamanho_id))] = qtd
        return quantidades

    @staticmethod
    def _data_entrega(request):
        entrega = (request.POST.get('data_prevista_entrega') or '').strip()
        if not entrega:
            return None
        try:
            return date.fromisoformat(entrega)
        except ValueError:
            raise ValueError('Data de entrega inválida.')

    @staticmethod
    def _data_pedido(request):
        valor = (request.POST.get('data_pedido') or '').strip()
        if not valor:
            return timezone.localdate()
        try:
            return date.fromisoformat(valor)
        except ValueError:
            raise ValueError('Data do pedido inválida.')

    @staticmethod
    def _salvar_mockups_do_item(
        request, pedido, item, campo_generico='imagens', incluir_legado=True,
    ):
        for upload in request.FILES.getlist(campo_generico):
            visual = VisualItemPedido.objects.create(
                item=item, posicao=Posicao.FRENTE_CAMISA, imagem=upload,
            )
            ArquivoPedido.objects.create(
                pedido=pedido, arquivo=visual.imagem, tipo=ArquivoPedido.Tipo.ARTE,
                descricao=f'Imagem · {item.nome_exibicao}', enviado_por=request.user,
            )
        if not incluir_legado:
            return
        campos = {
            'mockup_frente_camisa': Posicao.FRENTE_CAMISA,
            'mockup_costas_camisa': Posicao.COSTAS_CAMISA,
        }
        for campo, posicao in campos.items():
            upload = request.FILES.get(campo)
            if not upload:
                continue
            visual = VisualItemPedido.objects.create(
                item=item, posicao=posicao, imagem=upload,
            )
            ArquivoPedido.objects.create(
                pedido=pedido,
                arquivo=visual.imagem,
                tipo=ArquivoPedido.Tipo.ARTE,
                descricao=visual.get_posicao_display(),
                enviado_por=request.user,
            )


class Op2ModeloRapidoView(ModaBaseView):
    """Cria o modelo mínimo sem tirar o usuário do orçamento em edição."""

    area = 'comercial'
    permissao_acao = 'criar'

    def post(self, request):
        nome = (request.POST.get('nome') or '').strip()
        if not nome:
            return JsonResponse(
                {'ok': False, 'erro': 'Informe o nome do modelo.'}, status=400,
            )
        produto = ProdutoModa.objects.create(
            filial=_filial(request),
            codigo=f'OP2-{uuid4().hex[:10].upper()}',
            nome=nome,
            status=ProdutoModa.Status.ATIVO,
            ativo=True,
        )
        return JsonResponse({
            'ok': True,
            'modelo': {
                'id': str(produto.pk), 'nome': produto.nome,
                'codigo': produto.codigo, 'info': produto.codigo,
            },
        })


class Op2DetailView(ModaBaseView):
    area = 'comercial'

    def get(self, request, pk):
        pedido = _pedido(request, pk)
        itens = list(pedido.itens.select_related(
            'produto', 'modelo', 'cor', 'tecido', 'grade_tamanho',
        ).prefetch_related(
            'grade__tamanho', 'personalizacoes', 'visuais__mockup', 'individuais__tamanho',
        ))
        modelos = list(
            ProdutoModa.objects.for_filial(_filial(request)).filter(
                ativo=True,
            ).select_related('modelo', 'tecido', 'grade').prefetch_related(
                'grade__itens__tamanho',
            ).order_by('nome')
        )
        link = request.build_absolute_uri(
            reverse('moda_publico:pedido', args=[pedido.token_publico])
        )
        eventos = HistoricoService.do_pedido(pedido)
        estrutura_opcoes = opcoes_estrutura_filial(_filial(request))
        grades = list(
            Grade.objects.for_filial(_filial(request)).filter(ativo=True)
            .prefetch_related('itens__tamanho').order_by('tipo', 'nome')
        )
        tamanhos = list(
            Tamanho.objects.for_filial(_filial(request)).filter(ativo=True)
            .order_by('tipo', 'ordem', 'sigla')
        )
        formas = list(
            FormaPagamento.objects.filter(
                Q(filial=_filial(request)) | Q(filial__isnull=True),
                empresa=_filial(request).empresa, ativo=True,
            ).select_related('conta_bancaria_padrao').order_by('descricao')
        )
        contas_bancarias = list(
            ContaBancaria.objects.for_filial(_filial(request)).filter(ativo=True)
            .order_by('descricao', 'banco_nome')
        )
        aprovacao = getattr(pedido, 'aprovacao', None)
        vencimento_financeiro = (
            pedido.data_prevista_entrega
            or pedido.data_pedido
        )
        return render(request, 'moda/op2_detail.html', {
            'title': f'OP 2.0 #{pedido.numero:06d}',
            'pedido': pedido,
            'itens': itens,
            'modelos': modelos,
            'modelos_grade': {
                str(produto.pk): {
                    'grade_id': str(produto.grade_id or ''),
                    'grade': produto.grade.nome if produto.grade_id else '',
                    'tipo_impressao': produto.tipo_impressao,
                    'tipo_impressao_label': produto.get_tipo_impressao_display().upper(),
                    'tamanhos': [
                        str(i.tamanho_id) for i in produto.grade.itens.all()
                    ] if produto.grade_id else [],
                }
                for produto in modelos
            },
            'grades_json': [
                {
                    'id': str(grade.pk),
                    'nome': grade.nome,
                    'tipo': grade.get_tipo_display(),
                    'resumo': grade.resumo,
                    'tamanhos': [str(item.tamanho_id) for item in grade.itens.all()],
                }
                for grade in grades
            ],
            'tamanhos_labels': {
                str(tamanho.pk): tamanho.sigla for tamanho in tamanhos
            },
            'itens_modal_json': {
                str(item.pk): _dados_modal_item(item, estrutura_opcoes)
                for item in itens
            },
            'estrutura_opcoes': estrutura_opcoes,
            'estrutura_tipo_padrao': next(iter(estrutura_opcoes), 'camisa'),
            'status_choices': status_choices_kanban(),
            'status_atual': status_destino_kanban(pedido.status),
            'tipos_impressao': ProdutoModa.TipoImpressao.choices,
            'form_item': ItemPedidoProducaoForm(filial=_filial(request)),
            'form_individual': PersonalizacaoIndividualForm(
                filial=_filial(request), pedido=pedido,
            ),
            'form_arquivo': ArquivoPedidoForm(),
            'arquivos': pedido.arquivos.select_related('enviado_por'),
            'eventos': list(reversed(eventos[-50:])),
            'link_publico': link,
            'whatsapp_numero': whatsapp_numero(pedido),
            'mensagem_whatsapp': _mensagem_op2(pedido, link),
            'pdf_url': reverse(
                'moda:pedido-orcamento-pdf' if pedido.status == PedidoProducao.Status.ORCAMENTO
                else 'moda:pedido-pdf', args=[pedido.pk],
            ),
            'total_entregue': sum(i.quantidade_entregue for i in itens),
            'total_pendente': sum(i.quantidade_pendente for i in itens),
            'vagas_individuais': IndividualService.vagas(pedido),
            'abrir_whatsapp': request.GET.get('whatsapp') == '1',
            'aprovacao': aprovacao,
            'formas_pagamento': formas,
            'contas_bancarias': contas_bancarias,
            'contas_por_forma_pagamento': {
                str(forma.pk): str(forma.conta_bancaria_padrao_id or '')
                for forma in formas
            },
            'contas_financeiras': FinanceiroPedidoService.contas_do_pedido(pedido),
            'vencimento_financeiro': vencimento_financeiro,
            'posicoes_mockup': [
                (Posicao.FRENTE_CAMISA, Posicao.FRENTE_CAMISA.label),
                (Posicao.COSTAS_CAMISA, Posicao.COSTAS_CAMISA.label),
            ],
        })


class Op2AnexosZipView(ModaBaseView):
    area = 'comercial'

    def get(self, request, pk):
        pedido = _pedido(request, pk)
        buffer = BytesIO()
        usados = set()

        def adicionar(zip_file, campo, pasta, nome):
            if not campo or not getattr(campo, 'name', ''):
                return
            chave = campo.name
            if chave in usados:
                return
            usados.add(chave)
            base = (nome or campo.name.rsplit('/', 1)[-1]).replace('\\', '-').replace('/', '-')
            caminho = f'{pasta}/{base}'
            sufixo = 2
            while caminho in zip_file.namelist():
                raiz, ponto, extensao = base.rpartition('.')
                caminho = f'{pasta}/{raiz or base}-{sufixo}{ponto}{extensao}'
                sufixo += 1
            try:
                campo.open('rb')
                zip_file.writestr(caminho, campo.read())
                campo.close()
            except (OSError, ValueError):
                return

        with ZipFile(buffer, 'w', ZIP_DEFLATED) as pacote:
            for anexo in pedido.arquivos.all():
                adicionar(pacote, anexo.arquivo, 'anexos', anexo.nome_arquivo)
            for item in pedido.itens.prefetch_related('visuais'):
                for indice, visual in enumerate(item.visuais.all(), start=1):
                    campo = visual.imagem or (
                        visual.mockup.imagem if visual.mockup_id else None
                    )
                    extensao = campo.name.rsplit('.', 1)[-1] if campo and '.' in campo.name else 'png'
                    nome = f'{item.nome_exibicao}-imagem-{indice}.{extensao}'
                    adicionar(pacote, campo, 'fotos', nome)

        resposta = HttpResponse(buffer.getvalue(), content_type='application/zip')
        resposta['Content-Disposition'] = (
            f'attachment; filename="anexos-op-{pedido.numero:06d}.zip"'
        )
        return resposta


class Op2ActionView(ModaBaseView):
    area = 'comercial'
    permissao_acao = 'editar'

    def post(self, request, pk):
        pedido = _pedido(request, pk)
        acao = request.POST.get('acao') or ''
        handler = getattr(self, f'_acao_{acao}', None)
        if handler is None:
            messages.error(request, 'Ação inválida.')
            return _voltar(pedido)
        try:
            with transaction.atomic():
                resposta = handler(request, pedido)
        except (TypeError, ValueError, DomainError) as erro:
            messages.error(request, str(erro) or 'Confira os valores informados.')
            return _voltar(pedido)
        return resposta or _voltar(pedido)

    def _acao_cabecalho(self, request, pedido):
        pedido.data_pedido = date.fromisoformat(request.POST.get('data_pedido'))
        entrega = (request.POST.get('data_prevista_entrega') or '').strip()
        pedido.data_prevista_entrega = date.fromisoformat(entrega) if entrega else None
        pedido.prioridade = request.POST.get('prioridade') or pedido.prioridade
        pedido.observacoes = _observacoes_pedido(request)
        pedido.contato_nome = (request.POST.get('contato_nome') or '').strip()
        pedido.contato_telefone = (request.POST.get('contato_telefone') or '').strip()
        pedido.save()
        messages.success(request, 'Rascunho salvo.')

    def _acao_financeiro(self, request, pedido):
        def dinheiro(nome):
            texto = (request.POST.get(nome) or '0').strip().replace(' ', '')
            if ',' in texto:
                texto = texto.replace('.', '').replace(',', '.')
            try:
                return Decimal(texto).quantize(Decimal('0.01'))
            except (InvalidOperation, ValueError):
                raise ValueError('Confira os valores financeiros informados.')

        valor_total = dinheiro('valor_total')
        entrada = dinheiro('entrada')
        if valor_total <= 0:
            raise ValueError('O valor total precisa ser maior que zero.')
        if entrada < 0 or entrada > valor_total:
            raise ValueError('O adiantamento deve ficar entre zero e o valor total.')
        forma = get_object_or_404(
            FormaPagamento.objects.filter(
                Q(filial=_filial(request)) | Q(filial__isnull=True),
                empresa=_filial(request).empresa, ativo=True,
            ), pk=request.POST.get('forma_pagamento'),
        )
        conta = get_object_or_404(
            ContaBancaria.objects.for_filial(_filial(request)).filter(ativo=True),
            pk=request.POST.get('conta_bancaria'),
        )
        vencimento_texto = request.POST.get('data_vencimento') or ''
        vencimento = date.fromisoformat(vencimento_texto)
        parcelas = int(request.POST.get('parcelas') or 1)
        if parcelas < 1 or parcelas > 120:
            raise ValueError('A quantidade de parcelas deve ficar entre 1 e 120.')

        base = pedido.subtotal + (pedido.frete or Decimal('0'))
        pedido.desconto = max(base - valor_total, Decimal('0'))
        pedido.acrescimo = max(valor_total - base, Decimal('0'))
        pedido.entrada = entrada
        pedido.forma_pagamento = forma
        pedido.conta_bancaria_entrada = conta
        pedido.save(update_fields=[
            'desconto', 'acrescimo', 'entrada', 'forma_pagamento',
            'conta_bancaria_entrada', 'updated_at',
        ])
        contas = FinanceiroPedidoService.gerar(
            pedido, request.user, vencimento_saldo=vencimento,
            parcelas_saldo=parcelas,
        )
        messages.success(
            request,
            f'Financeiro gerado: {len(contas)} lançamento(s), com R$ {entrada:.2f} recebido.',
        )

    def _acao_adicionar_item(self, request, pedido):
        grades = self._grades_selecionadas(request)
        quantidades_por_grade = {
            grade.pk: self._quantidades_grade_modal(request, grade)
            for grade in grades
        }
        total_geral = sum(
            sum(quantidades.values()) for quantidades in quantidades_por_grade.values()
        )
        if grades and request.POST.get('quantidade') and int(request.POST['quantidade']) != total_geral:
            raise ValueError(
                f'A quantidade total deve ser igual à soma das grades ({total_geral}).'
            )
        alvos = grades or [None]
        criados = []
        for grade in alvos:
            dados = request.POST.copy()
            produto_id = dados.get('produto_id')
            dados['produto'] = f'moda:{produto_id}' if produto_id else ''
            dados['observacoes'] = request.POST.get('item_observacoes') or ''
            quantidades = quantidades_por_grade.get(grade.pk, {}) if grade else {}
            if grade:
                total_grade = sum(quantidades.values())
                dados['quantidade'] = str(total_grade)
            form = ItemPedidoProducaoForm(dados, filial=_filial(request))
            if not form.is_valid():
                raise ValueError('Produto: ' + '; '.join(
                    erro for erros in form.errors.values() for erro in erros
                ))
            item = form.save(commit=False)
            item.pedido = pedido
            item.ordem = (pedido.itens.count() + 1) * 10
            item.status_fluxo = (
                ItemPedidoProducao.StatusFluxo.ORCAMENTO
                if pedido.status == PedidoProducao.Status.ORCAMENTO
                else ItemPedidoProducao.StatusFluxo.APROVADO
            )
            item.grade_tamanho = grade
            item.observacoes = juntar_observacoes_item(
                item.observacoes, request.POST, opcoes_estrutura_filial(_filial(request)),
            )
            item.save()
            if grade:
                self._substituir_grade(item, grade, quantidades)
            self._salvar_personalizacao(request, item)
            Op2CreateView._salvar_mockups_do_item(request, pedido, item)
            criados.append(item)
        GradePedidoService.recalcular_pedido(pedido)
        if len(criados) == 1:
            messages.success(request, f'{criados[0].nome_exibicao} adicionado.')
        else:
            messages.success(request, f'{len(criados)} itens adicionados, um para cada grade.')

    def _acao_visual_item(self, request, pedido):
        item = get_object_or_404(pedido.itens, pk=request.POST.get('item_id'))
        uploads = request.FILES.getlist('imagens') or request.FILES.getlist('imagem')
        if not uploads:
            raise ValueError('Selecione ao menos uma imagem.')
        for upload in uploads:
            visual = VisualItemPedido.objects.create(
                item=item, posicao=Posicao.FRENTE_CAMISA, imagem=upload,
            )
            ArquivoPedido.objects.create(
                pedido=pedido, arquivo=visual.imagem,
                tipo=ArquivoPedido.Tipo.ARTE,
                descricao=f'Imagem · {item.nome_exibicao}', enviado_por=request.user,
            )
        messages.success(request, f'{len(uploads)} imagem(ns) adicionada(s) ao produto.')

    def _acao_remover_visual(self, request, pedido):
        visual = get_object_or_404(
            VisualItemPedido.objects.filter(item__pedido=pedido),
            pk=request.POST.get('visual_id'),
        )
        nome = visual.imagem.name if visual.imagem else ''
        storage = visual.imagem.storage if visual.imagem else None
        if nome:
            pedido.arquivos.filter(arquivo=nome).delete()
        visual.delete()
        if nome and storage:
            storage.delete(nome)
        messages.success(request, 'Imagem removida da OP.')

    def _acao_cancelar(self, request, pedido):
        if pedido.status == PedidoProducao.Status.CANCELADO:
            messages.info(request, 'Esta OP já está cancelada.')
            return
        pedido.status = PedidoProducao.Status.CANCELADO
        pedido.save(update_fields=['status', 'updated_at'])
        messages.success(request, 'OP cancelada.')

    def _acao_grade_item(self, request, pedido):
        item = get_object_or_404(pedido.itens, pk=request.POST.get('item_id'))
        quantidades = Op2CreateView._quantidades_grade(request, item, incluir_zeros=True)
        if not quantidades:
            Op2CreateView._copiar_grade_do_modelo(item)
            messages.info(request, 'Grade do modelo carregada para o produto.')
            return
        total = GradePedidoService.salvar_quantidades(pedido, quantidades)
        messages.success(request, f'Grade atualizada. Total do pedido: {total} peça(s).')

    def _acao_remover_item(self, request, pedido):
        item = get_object_or_404(pedido.itens, pk=request.POST.get('item_id'))
        item.delete()
        _sincronizar_status(pedido)
        messages.success(request, 'Produto removido.')

    def _acao_editar_item(self, request, pedido):
        item = get_object_or_404(pedido.itens, pk=request.POST.get('item_id'))
        produto_id = request.POST.get('produto_id')
        if produto_id:
            item.produto = get_object_or_404(
                ProdutoModa.objects.for_filial(_filial(request)).filter(ativo=True),
                pk=produto_id,
            )
        grades = self._grades_selecionadas(request)
        if len(grades) > 1:
            raise ValueError('Ao editar, mantenha somente uma grade por produto.')
        grade = grades[0] if grades else None
        quantidades = self._quantidades_grade_modal(request, grade) if grade else {}
        quantidade = (
            sum(quantidades.values()) if grade
            else int(request.POST.get('quantidade') or item.quantidade or 1)
        )
        if grade and request.POST.get('quantidade') and int(request.POST['quantidade']) != quantidade:
            raise ValueError(
                f'A quantidade total deve ser igual à soma da grade ({quantidade}).'
            )
        if quantidade < 1:
            raise ValueError('A quantidade precisa ser pelo menos 1.')
        entregue = min(item.quantidade_entregue, quantidade)
        item.quantidade = quantidade
        item.quantidade_entregue = entregue
        item.valor_unitario = request.POST.get('valor_unitario') or 0
        if 'referencia' in request.POST:
            item.referencia = (request.POST.get('referencia') or '').strip()
        if 'acabamento' in request.POST:
            item.acabamento = (request.POST.get('acabamento') or '').strip()
        item.grade_tamanho = grade
        item.observacoes = juntar_observacoes_item(
            request.POST.get('item_observacoes') or '', request.POST,
            opcoes_estrutura_filial(_filial(request)),
        )
        item.save(update_fields=[
            'produto', 'grade_tamanho', 'quantidade', 'quantidade_entregue',
            'valor_unitario', 'referencia', 'acabamento', 'observacoes',
        ])
        if grade:
            self._substituir_grade(item, grade, quantidades)
        else:
            item.grade.all().delete()
        self._salvar_personalizacao(request, item)
        GradePedidoService.recalcular_pedido(pedido)
        _sincronizar_status(pedido)
        messages.success(request, 'Produto atualizado.')

    @staticmethod
    def _salvar_personalizacao(request, item):
        arte = item.personalizacoes.first()
        tipo_impressao = (
            request.POST.get('tipo_impressao')
            if 'tipo_impressao' in request.POST else (
                request.POST.get('arte_tecnica')
                or getattr(item.produto, 'tipo_impressao', '')
            )
        )
        if not tipo_impressao:
            if arte:
                arte.delete()
            return
        arte = arte or Personalizacao(item=item)
        arte.tipo = Personalizacao.Tipo.ARTE
        arte.tecnica = _normalizar_tipo_impressao(tipo_impressao)
        arte.local = ''
        arte.observacoes = ''
        arte.save()

    @staticmethod
    def _grades_selecionadas(request):
        ids = list(dict.fromkeys(
            valor for valor in request.POST.getlist('grades') if str(valor).isdigit()
        ))
        if not ids:
            return []
        grades = {
            str(grade.pk): grade
            for grade in Grade.objects.for_filial(_filial(request)).filter(
                ativo=True, pk__in=ids,
            ).prefetch_related('itens__tamanho')
        }
        if len(grades) != len(ids):
            raise ValueError('Uma das grades selecionadas não está disponível.')
        return [grades[grade_id] for grade_id in ids]

    @staticmethod
    def _quantidades_grade_modal(request, grade):
        prefixo = f'grade_{grade.pk}_'
        quantidades = {}
        for linha in grade.itens.all():
            valor = request.POST.get(f'{prefixo}{linha.tamanho_id}', '0')
            try:
                quantidade = int(valor or 0)
            except (TypeError, ValueError):
                raise ValueError('Grade: informe apenas números nas quantidades.')
            if quantidade < 0:
                raise ValueError('Grade: quantidade não pode ser negativa.')
            quantidades[linha.tamanho_id] = quantidade
        if not quantidades or sum(quantidades.values()) < 1:
            raise ValueError(f'Informe ao menos uma peça na grade {grade.nome}.')
        return quantidades

    @staticmethod
    def _substituir_grade(item, grade, quantidades):
        item.grade.all().delete()
        ItemGradePedido.objects.bulk_create([
            ItemGradePedido(item=item, tamanho_id=tamanho_id, quantidade=quantidade)
            for tamanho_id, quantidade in quantidades.items()
        ])
        item.grade_tamanho = grade
        item.quantidade = sum(quantidades.values())
        item.save(update_fields=['grade_tamanho', 'quantidade'])

    def _acao_status(self, request, pedido):
        novo = request.POST.get('status')
        permitidos = dict(status_choices_kanban())
        if novo not in permitidos:
            raise ValueError('Status inválido.')
        pedido.status = novo
        pedido.save(update_fields=['status', 'updated_at'])
        mapa = {
            PedidoProducao.Status.ORCAMENTO: ItemPedidoProducao.StatusFluxo.ORCAMENTO,
            PedidoProducao.Status.CONFIRMADO: ItemPedidoProducao.StatusFluxo.APROVADO,
            PedidoProducao.Status.LIBERADO_PRODUCAO: ItemPedidoProducao.StatusFluxo.PRODUCAO,
            PedidoProducao.Status.PRONTO: ItemPedidoProducao.StatusFluxo.PRONTO,
            PedidoProducao.Status.ENTREGUE: ItemPedidoProducao.StatusFluxo.ENTREGUE,
        }
        if novo in mapa:
            pedido.itens.exclude(status_fluxo=ItemPedidoProducao.StatusFluxo.ENTREGUE).update(
                status_fluxo=mapa[novo],
            )
        if novo == PedidoProducao.Status.ENTREGUE:
            for item in pedido.itens.only('pk', 'quantidade', 'quantidade_entregue'):
                if item.quantidade_entregue != item.quantidade:
                    item.quantidade_entregue = item.quantidade
                    item.save(update_fields=['quantidade_entregue'])
        messages.success(request, f'Status alterado para {permitidos[novo]}.')

    def _acao_aprovar(self, request, pedido):
        pedido.status = PedidoProducao.Status.CONFIRMADO
        pedido.save(update_fields=['status', 'updated_at'])
        pedido.itens.update(status_fluxo=ItemPedidoProducao.StatusFluxo.APROVADO)
        messages.success(request, 'Pedido aprovado pelo usuário.')

    def _acao_enviar_whatsapp(self, request, pedido):
        aprovacao, _ = AprovacaoPedido.objects.get_or_create(pedido=pedido)
        if not aprovacao.liberado:
            aprovacao.liberar(request.user, 'Liberado pela OP 2.0 para envio ao cliente.')
        elif not aprovacao.aguardando_cliente:
            aprovacao.reenviar(request.user, 'Nova rodada enviada pela OP 2.0.')
        if pedido.status != PedidoProducao.Status.ORCAMENTO:
            pedido.status = PedidoProducao.Status.AGUARDANDO_APROVACAO
            pedido.save(update_fields=['status', 'updated_at'])

    def _acao_anexo(self, request, pedido):
        form = ArquivoPedidoForm(request.POST, request.FILES)
        arquivos = request.FILES.getlist('arquivo')
        if not arquivos:
            raise ValueError('Selecione ao menos um arquivo.')
        for upload in arquivos:
            ArquivoPedido.objects.create(
                pedido=pedido, arquivo=upload,
                tipo=request.POST.get('tipo') or ArquivoPedido.Tipo.ARTE,
                descricao=(request.POST.get('descricao') or '').strip(),
                enviado_por=request.user,
            )
        messages.success(request, f'{len(arquivos)} arquivo(s) anexado(s).')


    def _acao_individual(self, request, pedido):
        form = PersonalizacaoIndividualForm(
            request.POST, filial=_filial(request), pedido=pedido,
        )
        if not form.is_valid():
            raise ValueError('Grade: ' + '; '.join(
                erro for erros in form.errors.values() for erro in erros
            ))
        individual = form.save(commit=False)
        individual.pedido = pedido
        individual.ordem = (pedido.individuais.count() + 1) * 10
        individual.save()
        messages.success(request, 'Tamanho e personalização adicionados.')

    def _acao_telefone(self, request, pedido):
        telefone = ''.join(c for c in request.POST.get('telefone', '') if c.isdigit())
        if not telefone:
            raise ValueError('Informe o WhatsApp.')
        pedido.contato_telefone = telefone
        pedido.save(update_fields=['contato_telefone', 'updated_at'])
        if request.POST.get('salvar_cliente') == '1':
            pedido.cliente.celular = telefone
            pedido.cliente.save(update_fields=['celular', 'updated_at'])
        messages.success(request, 'WhatsApp atualizado.')

    def _acao_duplicar(self, request, pedido):
        novo = PedidoProducao.objects.create(
            filial=pedido.filial, cliente=pedido.cliente, vendedor=request.user,
            contato_nome=pedido.contato_nome, contato_telefone=pedido.contato_telefone,
            data_pedido=timezone.localdate(), data_prevista_entrega=None,
            prioridade=pedido.prioridade, status=PedidoProducao.Status.ORCAMENTO,
            observacoes=pedido.observacoes, desconto=pedido.desconto,
            acrescimo=pedido.acrescimo, frete=pedido.frete,
        )
        mapa = {}
        for original in pedido.itens.all():
            item = copy(original)
            item.pk = None
            item.pedido = novo
            item.status_fluxo = ItemPedidoProducao.StatusFluxo.ORCAMENTO
            item.quantidade_entregue = 0
            item.save()
            mapa[original.pk] = item
            for grade in original.grade.all():
                ItemGradePedido.objects.create(
                    item=item, tamanho=grade.tamanho, quantidade=grade.quantidade,
                )
            for arte in original.personalizacoes.all():
                copia = copy(arte); copia.pk = None; copia.item = item; copia.save()
            for visual in original.visuais.all():
                copia = copy(visual); copia.pk = None; copia.item = item; copia.save()
        for pessoa in pedido.individuais.all():
            copia = copy(pessoa)
            copia.pk = None; copia.pedido = novo; copia.item = mapa[pessoa.item_id]; copia.save()
        messages.success(request, f'OP duplicada como rascunho #{novo.numero:06d}.')
        return _voltar(novo)


class Op2EstruturaOpcaoView(ModaBaseView):
    area = 'produtos'
    permissao_acao = 'editar'

    def get(self, request):
        sincronizar_opcoes_padrao(_filial(request))
        return render(request, 'moda/op2_estrutura_opcoes.html', self._context(request))

    def post(self, request):
        sincronizar_opcoes_padrao(_filial(request))
        acao = request.POST.get('acao') or ''
        tipo_atual = (
            request.POST.get('tipo_atual')
            or request.POST.get('tipo_peca')
            or ''
        ).strip()
        try:
            if acao == 'criar':
                self._criar(request)
            elif acao == 'editar_tipo':
                self._editar_tipo(request)
            elif acao == 'editar':
                self._editar(request)
            elif acao == 'inativar':
                self._ativo(request, False)
            elif acao == 'ativar':
                self._ativo(request, True)
            elif acao == 'remover':
                self._remover(request)
            else:
                raise ValueError('Ação inválida.')
        except ValueError as erro:
            messages.error(request, str(erro))
        destino = reverse('moda:op2-tipos-peca')
        if tipo_atual:
            destino = f'{destino}?tipo={tipo_atual}'
        return redirect(destino)

    @staticmethod
    def _base_query(request):
        return OpcaoEstruturaOP2.objects.for_filial(_filial(request))

    def _opcao(self, request):
        return get_object_or_404(self._base_query(request), pk=request.POST.get('opcao_id'))

    def _criar(self, request):
        tipo_peca = (request.POST.get('tipo_peca') or '').strip()
        tipo_label = (request.POST.get('tipo_label') or '').strip()
        campo = self._normalizar_campo(request.POST.get('campo'))
        valor = (
            request.POST.get('opcao_texto')
            or request.POST.get('valor')
            or ''
        ).strip()
        if not tipo_peca or not tipo_label or not campo or not valor:
            raise ValueError('Informe tipo, nome do tipo, campo e opção.')
        try:
            OpcaoEstruturaOP2.objects.create(
                filial=_filial(request), tipo_peca=tipo_peca, tipo_label=tipo_label,
                campo=campo, valor=valor, ordem=int(request.POST.get('ordem') or 0),
                ativo=True,
            )
        except IntegrityError:
            raise ValueError('Essa opção já existe para este tipo de peça e campo.')
        messages.success(request, 'Opção cadastrada.')

    def _editar_tipo(self, request):
        tipo_peca = (request.POST.get('tipo_peca') or '').strip()
        tipo_label = (request.POST.get('tipo_label') or '').strip()
        if not tipo_peca or not tipo_label:
            raise ValueError('Informe o nome do tipo de peça.')
        atualizadas = self._base_query(request).filter(
            tipo_peca=tipo_peca,
        ).update(tipo_label=tipo_label, updated_at=timezone.now())
        if not atualizadas:
            raise ValueError('Tipo de peça não encontrado.')
        messages.success(request, f'Tipo “{tipo_label}” atualizado.')

    def _editar(self, request):
        opcao = self._opcao(request)
        valor = (
            request.POST.get('opcao_texto')
            or request.POST.get('valor')
            or ''
        ).strip()
        if not valor:
            raise ValueError('A opção não pode ficar vazia.')
        opcao.tipo_label = (request.POST.get('tipo_label') or opcao.tipo_label).strip()
        opcao.campo = self._normalizar_campo(
            request.POST.get('campo') or opcao.campo
        )
        opcao.valor = valor
        opcao.ordem = int(request.POST.get('ordem') or 0)
        try:
            opcao.save(update_fields=['tipo_label', 'campo', 'valor', 'ordem', 'updated_at'])
        except IntegrityError:
            raise ValueError('Essa opção já existe neste campo.')
        messages.success(request, 'Opção atualizada.')

    @staticmethod
    def _normalizar_campo(campo):
        return '_'.join((campo or '').strip().lower().split())

    def _ativo(self, request, ativo):
        opcao = self._opcao(request)
        opcao.ativo = ativo
        opcao.save(update_fields=['ativo', 'updated_at'])
        messages.success(request, 'Opção ativada.' if ativo else 'Opção inativada.')

    def _remover(self, request):
        opcao = self._opcao(request)
        opcao.delete()
        messages.success(request, 'Opção removida.')

    @staticmethod
    def _context(request):
        opcoes = list(
            OpcaoEstruturaOP2.objects.for_filial(_filial(request))
            .order_by('tipo_label', 'campo', 'ordem', 'valor')
        )
        tipos = {}
        for opcao in opcoes:
            tipo = tipos.setdefault(opcao.tipo_peca, {
                'slug': opcao.tipo_peca,
                'label': opcao.tipo_label,
                'campos': {},
                'total': 0,
                'ativos': 0,
            })
            tipo['campos'].setdefault(opcao.campo, []).append(opcao)
            tipo['total'] += 1
            tipo['ativos'] += int(opcao.ativo)
        for tipo in tipos.values():
            campos = tipo['campos']
            if 'tipo_impressao' in campos:
                impressoes = campos.pop('tipo_impressao')
                tipo['campos'] = {'tipo_impressao': impressoes, **campos}
        lista_tipos = list(tipos.values())
        slug_selecionado = (request.GET.get('tipo') or '').strip()
        tipo_selecionado = next(
            (tipo for tipo in lista_tipos if tipo['slug'] == slug_selecionado),
            lista_tipos[0] if lista_tipos else None,
        )
        for tipo in lista_tipos:
            tipo['selecionado'] = tipo is tipo_selecionado
        return {
            'title': 'Tipos de peça',
            'tipos': lista_tipos,
            'tipo_selecionado': tipo_selecionado,
            'campos_disponiveis': (
                list(tipo_selecionado['campos']) if tipo_selecionado else []
            ),
            'opcoes': opcoes,
            'padrao': OP2_ESTRUTURA_OPCOES,
        }
