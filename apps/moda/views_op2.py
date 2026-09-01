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
from django.db.models.deletion import ProtectedError
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
    Posicao, ProdutoModa, RegistroCriacaoArte, Tamanho, VisualItemPedido,
)
from .services.historico import HistoricoService
from .services.financeiro import FinanceiroPedidoService
from .services.grade_pedido import GradePedidoService
from .services.individual import IndividualService
from .services.item_groups import GRADE_CORES, agrupar_itens_op
from .services.kanban_comercial import status_choices_kanban, status_destino_kanban
from .services.op2_estrutura import (
    OP2_ESTRUTURA_OPCOES, campo_multisselecao, juntar_observacoes_item,
    opcoes_estrutura_filial, sincronizar_opcoes_padrao, validar_estrutura_item,
    validar_valor_unitario, valores_estrutura_campo,
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


def _clientes_adicionais(request, cliente_principal):
    """Resolve os clientes extras no mesmo escopo da OP e elimina duplicatas."""
    ids = []
    vistos = {str(cliente_principal.pk)}
    for valor in request.POST.getlist('clientes_adicionais'):
        valor = (valor or '').strip()
        if valor and valor not in vistos:
            vistos.add(valor)
            ids.append(valor)
    clientes = list(
        Cliente.objects.for_filial(_filial(request)).filter(
            ativo=True, pk__in=ids,
        )
    )
    if len(clientes) != len(ids):
        raise ValueError('Um dos clientes adicionais não está disponível nesta filial.')
    por_id = {str(cliente.pk): cliente for cliente in clientes}
    return [por_id[pk] for pk in ids]


def _separar_contatos_observacoes(texto):
    """Recupera o bloco de contatos já usado pela OP, sem mudar o banco."""
    texto = (texto or '').strip()
    livre, separador, bloco = texto.rpartition('Contatos extras:\n')
    if not separador:
        return texto, []
    contatos = []
    for linha in bloco.splitlines():
        if not linha.startswith('- ') or ':' not in linha:
            return texto, []
        nome, _, telefone = linha[2:].partition(':')
        contatos.append({'nome': nome.strip(), 'telefone': telefone.strip()})
    return livre.rstrip(), contatos


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


def _previsao_pagamento(request, total_esperado=None, *, obrigatoria=False):
    """Lê a divisão prevista do orçamento sem consultar o módulo financeiro."""
    rotulos = dict(PedidoProducao.FormaPagamentoPrevista.choices)
    linhas = []
    indices = sorted({
        int(chave.split('_')[1])
        for chave in request.POST
        if chave.startswith('pagamento_')
        and len(chave.split('_')) >= 3
        and chave.split('_')[1].isdigit()
    })
    for indice in indices:
        forma = (request.POST.get(f'pagamento_{indice}_forma') or '').strip()
        valor_texto = (
            request.POST.get(f'pagamento_{indice}_valor') or ''
        ).strip().replace(' ', '')
        if not forma and not valor_texto:
            continue
        if not forma:
            raise ValueError(
                'Pagamento previsto: selecione a forma de pagamento do orçamento.'
            )
        if forma not in rotulos:
            raise ValueError('Pagamento previsto: escolha uma forma válida.')
        try:
            valor = (
                Decimal(valor_texto.replace('.', '').replace(',', '.'))
                if ',' in valor_texto
                else Decimal(valor_texto)
            )
            valor = valor.quantize(Decimal('0.01'))
        except (InvalidOperation, ValueError):
            raise ValueError('Pagamento previsto: confira os valores informados.')
        if valor <= 0:
            raise ValueError('Pagamento previsto: cada valor deve ser maior que zero.')
        linhas.append({'forma': forma, 'valor': f'{valor:.2f}'})

    if obrigatoria and not linhas:
        raise ValueError(
            'Pagamento previsto: selecione a forma de pagamento do orçamento.'
        )

    if linhas and total_esperado is not None:
        total = sum((Decimal(linha['valor']) for linha in linhas), Decimal('0'))
        esperado = Decimal(total_esperado).quantize(Decimal('0.01'))
        if total != esperado:
            raise ValueError(
                'Pagamento previsto: a soma das formas deve ser igual ao total '
                f'do orçamento (R$ {esperado:.2f}).'
            )
    return linhas


def _dados_modal_item(item, estrutura_opcoes):
    """Serializa um item para o mesmo editor completo usado ao adicioná-lo."""
    texto = (item.observacoes or '').strip()
    marcador = 'Estrutura da peça:'
    observacoes = texto
    estrutura_tipo = next(iter(estrutura_opcoes), 'camisa')
    estrutura = {}
    cor_personalizada = ''
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
            estrutura[campo] = (
                [parte.strip() for parte in valor.split(' + ') if parte.strip()]
                if campo_multisselecao(campo) else valor
            )

        cores = estrutura_opcoes.get(estrutura_tipo, {}).get('campos', {}).get('cor', [])
        cor = estrutura.get('cor', '')
        if cor and cor not in cores:
            cor_personalizada = cor
            estrutura['cor'] = 'COR PERSONALIZADA'

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
        'valor_unitario': str(item.valor_unitario) if item.valor_unitario else '',
        'tipo_impressao': estrutura.pop('tipo_impressao', None) or [(
            arte.get_tecnica_display() if arte else
            getattr(item.produto, 'get_tipo_impressao_display', lambda: '')()
        ).upper()],
        'estrutura_tipo': estrutura_tipo,
        'estrutura': estrutura,
        'cor_personalizada': cor_personalizada,
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
        try:
            clientes_adicionais = _clientes_adicionais(request, cliente)
        except ValueError as erro:
            messages.error(request, str(erro))
            return render(request, 'moda/op2_create.html', self._context(request))
        indices = self._indices_itens(request)
        if not indices:
            messages.error(request, 'Adicione ao menos um modelo de produção à OP.')
            return render(request, 'moda/op2_create.html', self._context(request))

        formularios = []
        estrutura_opcoes = opcoes_estrutura_filial(_filial(request))
        for indice in indices:
            dados = self._dados_item(request, indice)
            try:
                validar_estrutura_item(dados, estrutura_opcoes)
                dados['valor_unitario'] = str(validar_valor_unitario(dados.get('valor_unitario')))
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
        total_orcamento = sum(
            (
                form.cleaned_data['quantidade']
                * (form.cleaned_data.get('valor_unitario') or Decimal('0'))
            )
            for _, form in formularios
        )
        try:
            previsao_pagamento = _previsao_pagamento(
                request, total_orcamento, obrigatoria=True,
            )
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
                previsao_pagamento=previsao_pagamento,
            )
            texto_criacao = (request.POST.get('informacoes_criacao') or '').strip()
            if texto_criacao:
                RegistroCriacaoArte.objects.create(
                    pedido=pedido, texto=texto_criacao, criado_por=request.user,
                )
            pedido.clientes_adicionais.set(clientes_adicionais)
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
                tipos_estrutura = valores_estrutura_campo(
                    self._post_item(request, indice), 'tipo_impressao',
                )
                tipo_impressao = ' + '.join(tipos_estrutura) or tipo_impressao
                if tipo_impressao and tipo_impressao != 'N/A':
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
            'pode_editar_cliente': request.user.tem_permissao('cadastros', 'editar'),
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
            'grade_cores_json': GRADE_CORES,
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
            'formas_pagamento_previstas': PedidoProducao.FormaPagamentoPrevista.choices,
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
        for chave, valores in request.POST.lists():
            if chave.startswith(prefixo):
                dados.setlist(chave.removeprefix(prefixo), valores)
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
            'grade__tamanho', 'personalizacoes', 'visuais__mockup',
            'individuais__tamanho', 'ordens',
        ))
        grupos_itens = agrupar_itens_op(itens)
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
        pode_ver_financeiro = request.user.tem_permissao('financeiro', 'ver')
        pode_quitar_financeiro = (
            pode_ver_financeiro
            and request.user.tem_permissao('financeiro', 'editar')
        )
        contas_financeiras = []
        if pode_ver_financeiro:
            contas_financeiras = list(
                FinanceiroPedidoService.contas_do_pedido(pedido)
                .select_related('forma_pagamento', 'conta_bancaria')
                .prefetch_related(
                    'pagamentos__forma_pagamento',
                    'pagamentos__conta_bancaria',
                    'pagamentos__usuario',
                )
            )
        resumo_financeiro = {
            'valor_titulos': sum(
                (conta.valor_final for conta in contas_financeiras), Decimal('0')
            ),
            'valor_recebido': sum(
                (conta.valor_pago for conta in contas_financeiras), Decimal('0')
            ),
            'valor_aberto': sum(
                (conta.valor_saldo for conta in contas_financeiras), Decimal('0')
            ),
        }
        status_pagamento = None
        if pode_ver_financeiro and pedido.financeiro_gerado:
            status_pagamento = FinanceiroPedidoService.situacao_pagamento(
                **resumo_financeiro,
            )
        observacoes_livres, contatos = _separar_contatos_observacoes(pedido.observacoes)
        return render(request, 'moda/op2_detail.html', {
            'title': f'OP 2.0 #{pedido.numero:06d}',
            'pedido': pedido,
            'cliente_atual_json': _cliente_json(pedido.cliente),
            'observacoes_livres': observacoes_livres,
            'contatos_json': contatos,
            'clientes_adicionais_json': [
                _cliente_json(cliente) for cliente in pedido.clientes_adicionais.all()
            ],
            'clientes_pedido': [pedido.cliente, *pedido.clientes_adicionais.all()],
            'clientes_pedido_json': [
                _cliente_json(cliente)
                for cliente in [pedido.cliente, *pedido.clientes_adicionais.all()]
            ],
            'pode_editar_cliente': request.user.tem_permissao('cadastros', 'editar'),
            'itens': itens,
            'pode_criar_cliente': request.user.tem_permissao('cadastros', 'criar'),
            'form_cliente': ClienteRapidoForm(),
            'grupos_itens': grupos_itens,
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
            'historico_criacao': pedido.historico_criacao.select_related('criado_por'),
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
            'formas_pagamento_previstas': PedidoProducao.FormaPagamentoPrevista.choices,
            'contas_bancarias': contas_bancarias,
            'contas_por_forma_pagamento': {
                str(forma.pk): str(forma.conta_bancaria_padrao_id or '')
                for forma in formas
            },
            'contas_financeiras': contas_financeiras,
            'resumo_financeiro': resumo_financeiro,
            'status_pagamento': status_pagamento,
            'pode_ver_financeiro': pode_ver_financeiro,
            'pode_quitar_financeiro': pode_quitar_financeiro,
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
            if acao == 'descricao_visual' and request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'ok': False, 'erro': str(erro)}, status=400)
            messages.error(request, str(erro) or 'Confira os valores informados.')
            return _voltar(pedido)
        return resposta or _voltar(pedido)

    def _acao_criacao(self, request, pedido):
        texto = (request.POST.get('informacoes_criacao') or '').strip()
        if not texto:
            raise ValueError('Escreva uma informação sobre a criação da arte.')
        RegistroCriacaoArte.objects.create(
            pedido=pedido, texto=texto, criado_por=request.user,
        )
        messages.success(request, 'Informação adicionada à linha do tempo da criação.')

    def _acao_editar_criacao(self, request, pedido):
        registro = get_object_or_404(
            pedido.historico_criacao, pk=request.POST.get('registro_id'),
        )
        texto = (request.POST.get('informacoes_criacao') or '').strip()
        if not texto:
            raise ValueError('A informação da criação não pode ficar vazia.')
        registro.texto = texto
        registro.save(update_fields=['texto'])
        messages.success(request, 'Informação da criação atualizada.')

    def _acao_remover_criacao(self, request, pedido):
        registro = get_object_or_404(
            pedido.historico_criacao, pk=request.POST.get('registro_id'),
        )
        registro.delete()
        messages.success(request, 'Informação removida da linha do tempo.')

    def _acao_descricao_visual(self, request, pedido):
        visual = get_object_or_404(
            VisualItemPedido.objects.filter(item__pedido=pedido),
            pk=request.POST.get('visual_id'),
        )
        descricao = (request.POST.get('descricao') or '').strip()
        limite = VisualItemPedido._meta.get_field('observacoes').max_length
        if len(descricao) > limite:
            raise ValueError(f'A descrição da imagem deve ter até {limite} caracteres.')
        visual.observacoes = descricao
        visual.save(update_fields=['observacoes'])
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'ok': True, 'descricao': descricao})
        messages.success(request, 'Descrição da imagem salva. Os PDFs já usarão este texto.')

    def _acao_cabecalho(self, request, pedido):
        cliente_id = request.POST.get('cliente')
        if not cliente_id:
            raise ValueError('Selecione o cliente da OP.')
        cliente = get_object_or_404(
            Cliente.objects.for_filial(_filial(request)).filter(
                Q(ativo=True) | Q(pk=pedido.cliente_id),
            ),
            pk=cliente_id,
        )
        clientes_adicionais = _clientes_adicionais(request, cliente)
        cliente_anterior_id = pedido.cliente_id
        pedido.cliente = cliente
        pedido.data_pedido = date.fromisoformat(request.POST.get('data_pedido'))
        entrega = (request.POST.get('data_prevista_entrega') or '').strip()
        pedido.data_prevista_entrega = date.fromisoformat(entrega) if entrega else None
        pedido.prioridade = request.POST.get('prioridade') or pedido.prioridade
        pedido.observacoes = _observacoes_pedido(request)
        pedido.contato_nome = (request.POST.get('contato_nome') or '').strip()
        pedido.contato_telefone = (request.POST.get('contato_telefone') or '').strip()
        pedido.save()
        pedido.clientes_adicionais.set(clientes_adicionais)
        if cliente.pk != cliente_anterior_id:
            messages.success(request, f'Cliente alterado para "{cliente.nome_display}".')
        else:
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

        clientes_pedido = [pedido.cliente, *pedido.clientes_adicionais.all()]
        clientes_por_id = {str(cliente.pk): cliente for cliente in clientes_pedido}

        def responsaveis(campo, rotulo):
            valor = (request.POST.get(campo) or '').strip()
            if valor == 'todos':
                return clientes_pedido
            cliente = clientes_por_id.get(valor)
            if cliente is None:
                raise ValueError(f'{rotulo}: selecione um cliente da OP ou divida entre todos.')
            return [cliente]

        pagadores_entrada = responsaveis('responsavel_entrada', 'Pagamento recebido')
        devedores_saldo = responsaveis('responsavel_saldo', 'Valor fiado')

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
            parcelas_saldo=parcelas, pagadores_entrada=pagadores_entrada,
            devedores_saldo=devedores_saldo,
        )
        messages.success(
            request,
            f'Financeiro gerado: {len(contas)} lançamento(s), com R$ {entrada:.2f} recebido.',
        )

    def _acao_adicionar_item(self, request, pedido):
        validar_estrutura_item(request.POST, opcoes_estrutura_filial(_filial(request)))
        valor_unitario = validar_valor_unitario(request.POST.get('valor_unitario'))
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
            dados['valor_unitario'] = str(valor_unitario)
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
            messages.success(
                request,
                f'Produto adicionado com {len(criados)} grades agrupadas.',
            )

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
        ordens = list(item.ordens.only('numero'))
        if ordens:
            numeros = ', '.join(ordem.numero for ordem in ordens[:3])
            raise DomainError(
                f'Este produto não pode ser excluído porque já gerou a ordem '
                f'de produção {numeros}. O vínculo é preservado para manter o '
                f'histórico da fábrica.'
            )
        try:
            item.delete()
        except ProtectedError as erro:
            raise DomainError(
                'Este produto já possui movimentações vinculadas e não pode ser '
                'excluído. O vínculo foi preservado para manter o histórico.'
            ) from erro
        _sincronizar_status(pedido)
        messages.success(request, 'Produto removido.')

    def _acao_editar_item(self, request, pedido):
        validar_estrutura_item(request.POST, opcoes_estrutura_filial(_filial(request)))
        valor_unitario = validar_valor_unitario(request.POST.get('valor_unitario'))
        item = get_object_or_404(pedido.itens, pk=request.POST.get('item_id'))
        produto_original_id = item.produto_id
        descricao_original = item.descricao
        produto_id = request.POST.get('produto_id')
        if produto_id:
            item.produto = get_object_or_404(
                ProdutoModa.objects.for_filial(_filial(request)).filter(ativo=True),
                pk=produto_id,
            )
        grades = self._grades_selecionadas(request)
        quantidades_por_grade = {
            grade.pk: self._quantidades_grade_modal(request, grade)
            for grade in grades
        }
        total_geral = sum(
            sum(quantidades.values()) for quantidades in quantidades_por_grade.values()
        )
        grade = next(
            (grade for grade in grades if grade.pk == item.grade_tamanho_id),
            grades[0] if grades else None,
        )
        quantidades = quantidades_por_grade.get(grade.pk, {}) if grade else {}
        quantidade = (
            sum(quantidades.values()) if grade
            else int(request.POST.get('quantidade') or item.quantidade or 1)
        )
        if grades and request.POST.get('quantidade') and int(request.POST['quantidade']) != total_geral:
            raise ValueError(
                f'A quantidade total deve ser igual à soma das grades ({total_geral}).'
            )
        if quantidade < 1:
            raise ValueError('A quantidade precisa ser pelo menos 1.')
        entregue = min(item.quantidade_entregue, quantidade)
        item.quantidade = quantidade
        item.quantidade_entregue = entregue
        item.valor_unitario = valor_unitario
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
        self._sincronizar_dados_compartilhados(
            pedido, item, produto_original_id, descricao_original,
        )
        if grade:
            self._substituir_grade(item, grade, quantidades)
        else:
            item.grade.all().delete()
        self._salvar_personalizacao(request, item)
        adicionais = []
        for grade_adicional in grades:
            if grade and grade_adicional.pk == grade.pk:
                continue
            novo_item = copy(item)
            novo_item.pk = None
            novo_item.grade_tamanho = grade_adicional
            novo_item.quantidade = sum(
                quantidades_por_grade[grade_adicional.pk].values()
            )
            novo_item.quantidade_entregue = 0
            novo_item.ordem = (pedido.itens.count() + 1) * 10
            novo_item.save()
            self._substituir_grade(
                novo_item, grade_adicional,
                quantidades_por_grade[grade_adicional.pk],
            )
            self._salvar_personalizacao(request, novo_item)
            adicionais.append(novo_item)
        GradePedidoService.recalcular_pedido(pedido)
        _sincronizar_status(pedido)
        if adicionais:
            messages.success(
                request,
                f'Produto atualizado com {len(grades)} grades agrupadas.',
            )
        else:
            messages.success(request, 'Produto atualizado.')

    @staticmethod
    def _sincronizar_dados_compartilhados(
        pedido, item, produto_original_id, descricao_original,
    ):
        """Mantém estrutura e preço iguais; grade e personalização ficam livres."""
        irmaos = pedido.itens.exclude(pk=item.pk)
        if produto_original_id:
            irmaos = irmaos.filter(produto_id=produto_original_id)
        else:
            irmaos = irmaos.filter(
                produto__isnull=True, descricao__iexact=descricao_original,
            )
        irmaos.update(
            produto_id=item.produto_id,
            descricao=item.descricao,
            referencia=item.referencia,
            modelo_id=item.modelo_id,
            cor_id=item.cor_id,
            tecido_id=item.tecido_id,
            gola=item.gola,
            manga=item.manga,
            acabamento=item.acabamento,
            valor_unitario=item.valor_unitario,
            observacoes=item.observacoes,
        )

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
        tipos_estrutura = valores_estrutura_campo(request.POST, 'tipo_impressao')
        tipo_impressao = ' + '.join(tipos_estrutura) or tipo_impressao
        if tipo_impressao == 'N/A':
            # Uma escolha de tipo não deve apagar arte ou observações já anexadas.
            if arte:
                arte.tecnica = 'N/A'
                arte.save(update_fields=['tecnica'])
            return
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
        if (pedido.status != PedidoProducao.Status.ORCAMENTO
                and aprovacao.aguardando_cliente):
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

    def _acao_editar_anexo(self, request, pedido):
        anexo = get_object_or_404(
            pedido.arquivos, pk=request.POST.get('arquivo_id'),
        )
        descricao = (request.POST.get('descricao') or '').strip()
        limite = ArquivoPedido._meta.get_field('descricao').max_length
        if len(descricao) > limite:
            raise ValueError(f'A observação do anexo deve ter até {limite} caracteres.')
        anexo.descricao = descricao
        anexo.save(update_fields=['descricao'])
        messages.success(request, 'Observação do anexo atualizada.')

    def _acao_remover_anexo(self, request, pedido):
        anexo = get_object_or_404(
            pedido.arquivos, pk=request.POST.get('arquivo_id'),
        )
        nome = anexo.descricao or anexo.nome_arquivo
        anexo.delete()
        messages.success(request, f'“{nome}” removido dos anexos da OP.')


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

    def _acao_individuais(self, request, pedido):
        item = get_object_or_404(pedido.itens, pk=request.POST.get('item'))
        indices = sorted({
            int(chave.split('_')[1])
            for chave in request.POST
            if chave.startswith('individual_')
            and len(chave.split('_')) >= 3
            and chave.split('_')[1].isdigit()
        })
        if not indices:
            raise ValueError('Adicione ao menos um nome ou número.')
        proxima_ordem = pedido.individuais.count() * 10
        salvos = 0
        for indice in indices:
            dados = {
                'item': item.pk,
                'tamanho': request.POST.get(f'individual_{indice}_tamanho'),
                'nome': request.POST.get(f'individual_{indice}_nome'),
                'numero': request.POST.get(f'individual_{indice}_numero'),
            }
            form = PersonalizacaoIndividualForm(
                dados, filial=_filial(request), pedido=pedido,
            )
            if not form.is_valid():
                raise ValueError(f'Linha {indice + 1}: ' + '; '.join(
                    erro for erros in form.errors.values() for erro in erros
                ))
            individual = form.save(commit=False)
            individual.pedido = pedido
            proxima_ordem += 10
            individual.ordem = proxima_ordem
            individual.save()
            salvos += 1
        messages.success(request, f'{salvos} personalização(ões) adicionada(s).')

    def _acao_editar_individual(self, request, pedido):
        individual = get_object_or_404(
            pedido.individuais.select_related('item', 'tamanho'),
            pk=request.POST.get('individual_id'),
        )
        dados = {
            'item': individual.item_id,
            'tamanho': request.POST.get('tamanho'),
            'nome': request.POST.get('nome'),
            'numero': request.POST.get('numero'),
            'observacoes': individual.observacoes,
        }
        form = PersonalizacaoIndividualForm(
            dados, instance=individual, filial=_filial(request), pedido=pedido,
        )
        if not form.is_valid():
            raise ValueError('Personalização: ' + '; '.join(
                erro for erros in form.errors.values() for erro in erros
            ))
        form.save()
        messages.success(request, 'Nome, número e tamanho atualizados.')

    def _acao_previsao_pagamento(self, request, pedido):
        pedido.previsao_pagamento = _previsao_pagamento(
            request, pedido.valor_total,
        )
        pedido.save(update_fields=['previsao_pagamento', 'updated_at'])
        messages.success(request, 'Previsão de pagamento atualizada no orçamento.')

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
            previsao_pagamento=pedido.previsao_pagamento,
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
        RegistroCriacaoArte.objects.bulk_create([
            RegistroCriacaoArte(
                pedido=novo, texto=registro.texto,
                criado_por_id=registro.criado_por_id, criado_em=registro.criado_em,
            )
            for registro in pedido.historico_criacao.all()
        ])
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
