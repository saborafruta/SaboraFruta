"""Workspace da OP 2.0. A tela antiga continua disponível sem alterações."""
from copy import copy
from datetime import date

from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.models import Cliente
from apps.core.services.exceptions import DadosInvalidosError

from .forms import ItemPedidoProducaoForm, PersonalizacaoIndividualForm
from .forms_arquivo import ArquivoPedidoForm
from .forms_cliente import ClienteRapidoForm
from .models import (
    AprovacaoPedido, ArquivoPedido, Grade, ItemGradePedido, ItemPedidoProducao,
    OpcaoEstruturaOP2, PedidoProducao, Personalizacao, PersonalizacaoIndividual,
    Posicao, ProdutoModa, Tamanho, VisualItemPedido,
)
from .services.historico import HistoricoService
from .services.grade_pedido import GradePedidoService
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


def _sincronizar_status(pedido):
    """A OP acompanha o item pendente menos avançado, sem esconder atrasos."""
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
        cliente = get_object_or_404(
            Cliente.objects.for_filial(_filial(request)).filter(ativo=True),
            pk=request.POST.get('cliente'),
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
            if grade_total:
                dados['quantidade'] = str(grade_total)
            form = ItemPedidoProducaoForm(dados, filial=_filial(request))
            if not form.is_valid():
                messages.error(request, f'Item {indice + 1}: ' + '; '.join(
                    erro for erros in form.errors.values() for erro in erros
                ))
                return render(request, 'moda/op2_create.html', self._context(request))
            formularios.append((indice, form))
        try:
            data_prevista_entrega = self._data_entrega(request)
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
                data_pedido=timezone.localdate(),
                data_prevista_entrega=data_prevista_entrega,
                prioridade=request.POST.get('prioridade') or PedidoProducao.Prioridade.NORMAL,
                observacoes=_observacoes_pedido(request),
            )
            primeiro_item = None
            for ordem, (indice, form) in enumerate(formularios, start=1):
                item = form.save(commit=False)
                item.pedido = pedido
                item.ordem = ordem * 10
                item.status_fluxo = ItemPedidoProducao.StatusFluxo.ORCAMENTO
                item.grade_tamanho_id = request.POST.get(f'item_{indice}_grade_id') or None
                item.observacoes = juntar_observacoes_item(
                    request.POST.get(f'item_{indice}_item_observacoes') or '',
                    self._post_item(request, indice),
                )
                item.save()
                primeiro_item = primeiro_item or item

                self._copiar_grade_do_modelo(item)
                quantidades = self._quantidades_grade(
                    request, item, prefixos=(f'item_{indice}_grade_',),
                )
                if quantidades:
                    GradePedidoService.salvar_quantidades(pedido, quantidades)

                if (
                    request.POST.get(f'item_{indice}_arte_tipo')
                    or request.POST.get(f'item_{indice}_arte_tecnica')
                    or request.POST.get(f'item_{indice}_arte_local')
                ):
                    Personalizacao.objects.create(
                        item=item,
                        tipo=request.POST.get(f'item_{indice}_arte_tipo') or Personalizacao.Tipo.ARTE,
                        tecnica=(
                            request.POST.get(f'item_{indice}_arte_tecnica')
                            or Personalizacao.Tecnica.SUBLIMACAO
                        ),
                        local=(request.POST.get(f'item_{indice}_arte_local') or '').strip(),
                        observacoes=(
                            request.POST.get(f'item_{indice}_arte_observacoes') or ''
                        ).strip(),
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

        messages.success(request, f'Rascunho #{pedido.numero:06d} criado.')
        return _voltar(pedido)

    @staticmethod
    def _context(request):
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
            'tamanhos': Tamanho.objects.for_filial(_filial(request)).filter(
                ativo=True,
            ).order_by('tipo', 'ordem', 'sigla'),
            'tipos_arte': Personalizacao.Tipo.choices,
            'tecnicas_arte': Personalizacao.Tecnica.choices,
            'tipos_arquivo': ArquivoPedido.Tipo.choices,
            'prioridades': PedidoProducao.Prioridade.choices,
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
            try:
                qtd = int(valor or 0)
            except (TypeError, ValueError):
                raise ValueError('Grade: informe apenas números nas quantidades.')
            if qtd < 0:
                raise ValueError('Grade: quantidade não pode ser negativa.')
            total += qtd
        return total

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
    def _salvar_mockups_do_item(request, pedido, item):
        campos = {
            'mockup_frente_camisa': Posicao.FRENTE_CAMISA,
            'mockup_costas_camisa': Posicao.COSTAS_CAMISA,
        }
        for campo, posicao in campos.items():
            upload = request.FILES.get(campo)
            if not upload:
                continue
            visual, _ = VisualItemPedido.objects.update_or_create(
                item=item,
                posicao=posicao,
                defaults={'imagem': upload},
            )
            ArquivoPedido.objects.create(
                pedido=pedido,
                arquivo=visual.imagem,
                tipo=ArquivoPedido.Tipo.ARTE,
                descricao=visual.get_posicao_display(),
                enviado_por=request.user,
            )


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
        return render(request, 'moda/op2_detail.html', {
            'title': f'OP 2.0 #{pedido.numero:06d}',
            'pedido': pedido,
            'itens': itens,
            'modelos': modelos,
            'modelos_grade': {
                str(produto.pk): {
                    'grade': produto.grade.nome if produto.grade_id else '',
                    'tamanhos': [
                        str(i.tamanho_id) for i in produto.grade.itens.all()
                    ] if produto.grade_id else [],
                }
                for produto in modelos
            },
            'estrutura_opcoes': opcoes_estrutura_filial(_filial(request)),
            'status_choices': status_choices_kanban(),
            'status_atual': status_destino_kanban(pedido.status),
            'item_status_choices': ItemPedidoProducao.StatusFluxo.choices,
            'tipos_arte': Personalizacao.Tipo.choices,
            'tecnicas_arte': Personalizacao.Tecnica.choices,
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
            'posicoes_mockup': [
                (Posicao.FRENTE_CAMISA, Posicao.FRENTE_CAMISA.label),
                (Posicao.COSTAS_CAMISA, Posicao.COSTAS_CAMISA.label),
            ],
        })


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
        except (TypeError, ValueError) as erro:
            messages.error(request, str(erro) or 'Confira os valores informados.')
            return _voltar(pedido)
        return resposta or _voltar(pedido)

    def _acao_cabecalho(self, request, pedido):
        entrega = (request.POST.get('data_prevista_entrega') or '').strip()
        pedido.data_prevista_entrega = date.fromisoformat(entrega) if entrega else None
        pedido.prioridade = request.POST.get('prioridade') or pedido.prioridade
        pedido.observacoes = _observacoes_pedido(request)
        pedido.contato_nome = (request.POST.get('contato_nome') or '').strip()
        pedido.contato_telefone = (request.POST.get('contato_telefone') or '').strip()
        pedido.save()
        messages.success(request, 'Rascunho salvo.')

    def _acao_adicionar_item(self, request, pedido):
        dados = request.POST.copy()
        produto_id = dados.get('produto_id')
        dados['produto'] = f'moda:{produto_id}' if produto_id else ''
        dados['observacoes'] = request.POST.get('observacoes') or ''
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
        item.observacoes = juntar_observacoes_item(item.observacoes, request.POST)
        item.save()
        Op2CreateView._copiar_grade_do_modelo(item)
        Op2CreateView._salvar_mockups_do_item(request, pedido, item)
        if request.POST.get('arte_tecnica') or request.POST.get('arte_local'):
            Personalizacao.objects.create(
                item=item,
                tipo=request.POST.get('arte_tipo') or Personalizacao.Tipo.ARTE,
                tecnica=request.POST.get('arte_tecnica') or Personalizacao.Tecnica.SUBLIMACAO,
                local=(request.POST.get('arte_local') or '').strip(),
                observacoes=(request.POST.get('arte_observacoes') or '').strip(),
            )
        messages.success(request, f'{item.nome_exibicao} adicionado.')

    def _acao_visual_item(self, request, pedido):
        item = get_object_or_404(pedido.itens, pk=request.POST.get('item_id'))
        posicao = request.POST.get('posicao')
        if posicao not in Posicao.values:
            raise ValueError('Posição do mockup inválida.')
        upload = request.FILES.get('imagem')
        if not upload:
            raise ValueError('Selecione uma imagem para o mockup.')
        visual, _ = VisualItemPedido.objects.update_or_create(
            item=item,
            posicao=posicao,
            defaults={'imagem': upload},
        )
        ArquivoPedido.objects.create(
            pedido=pedido,
            arquivo=visual.imagem,
            tipo=ArquivoPedido.Tipo.ARTE,
            descricao=f'Mockup · {item.nome_exibicao} · {visual.get_posicao_display()}',
            enviado_por=request.user,
        )
        messages.success(request, 'Mockup salvo junto aos anexos da OP.')

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
        quantidade = int(request.POST.get('quantidade') or item.quantidade or 1)
        if quantidade < 1:
            raise ValueError('A quantidade precisa ser pelo menos 1.')
        entregue = min(item.quantidade_entregue, quantidade)
        item.quantidade = quantidade
        item.quantidade_entregue = entregue
        item.valor_unitario = request.POST.get('valor_unitario') or 0
        item.referencia = (request.POST.get('referencia') or '').strip()
        item.acabamento = (request.POST.get('acabamento') or '').strip()
        item.observacoes = (request.POST.get('observacoes') or '').strip()
        item.save(update_fields=[
            'quantidade', 'quantidade_entregue', 'valor_unitario', 'referencia',
            'acabamento', 'observacoes',
        ])
        _sincronizar_status(pedido)
        messages.success(request, 'Produto atualizado.')

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
        messages.success(request, f'Status alterado para {permitidos[novo]}.')

    def _acao_item_fluxo(self, request, pedido):
        item = get_object_or_404(pedido.itens, pk=request.POST.get('item_id'))
        status = request.POST.get('status_fluxo')
        if status not in ItemPedidoProducao.StatusFluxo.values:
            raise ValueError('Status do produto inválido.')
        entregue = int(request.POST.get('quantidade_entregue') or 0)
        if entregue < 0 or entregue > item.quantidade:
            raise ValueError('A quantidade entregue deve ficar entre zero e o total do produto.')
        item.quantidade_entregue = entregue
        item.status_fluxo = (
            ItemPedidoProducao.StatusFluxo.ENTREGUE
            if entregue == item.quantidade else status
        )
        item.save(update_fields=['quantidade_entregue', 'status_fluxo'])
        _sincronizar_status(pedido)
        messages.success(request, f'Conferência de {item.nome_exibicao} atualizada.')

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
        try:
            if acao == 'criar':
                self._criar(request)
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
        return redirect(reverse('moda:op2-estrutura-opcoes'))

    @staticmethod
    def _base_query(request):
        return OpcaoEstruturaOP2.objects.for_filial(_filial(request))

    def _opcao(self, request):
        return get_object_or_404(self._base_query(request), pk=request.POST.get('opcao_id'))

    def _criar(self, request):
        tipo_peca = (request.POST.get('tipo_peca') or '').strip()
        tipo_label = (request.POST.get('tipo_label') or '').strip()
        campo = (request.POST.get('campo') or '').strip()
        valor = (request.POST.get('valor') or '').strip()
        if not tipo_peca or not tipo_label or not campo or not valor:
            raise ValueError('Informe tipo, nome do tipo, campo e opção.')
        OpcaoEstruturaOP2.objects.create(
            filial=_filial(request), tipo_peca=tipo_peca, tipo_label=tipo_label,
            campo=campo, valor=valor, ordem=int(request.POST.get('ordem') or 0),
            ativo=True,
        )
        messages.success(request, 'Opção cadastrada.')

    def _editar(self, request):
        opcao = self._opcao(request)
        valor = (request.POST.get('valor') or '').strip()
        if not valor:
            raise ValueError('A opção não pode ficar vazia.')
        opcao.tipo_label = (request.POST.get('tipo_label') or opcao.tipo_label).strip()
        opcao.campo = (request.POST.get('campo') or opcao.campo).strip()
        opcao.valor = valor
        opcao.ordem = int(request.POST.get('ordem') or 0)
        opcao.save(update_fields=['tipo_label', 'campo', 'valor', 'ordem', 'updated_at'])
        messages.success(request, 'Opção atualizada.')

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
            })
            tipo['campos'].setdefault(opcao.campo, []).append(opcao)
        return {
            'title': 'Opções da OP 2.0',
            'tipos': tipos.values(),
            'opcoes': opcoes,
            'padrao': OP2_ESTRUTURA_OPCOES,
        }
