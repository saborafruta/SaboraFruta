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
    AprovacaoPedido, ArquivoPedido, ItemGradePedido, ItemPedidoProducao, PedidoProducao,
    Personalizacao, PersonalizacaoIndividual, Posicao, ProdutoModa, Tamanho, VisualItemPedido,
)
from .services.historico import HistoricoService
from .services.grade_pedido import GradePedidoService
from .services.kanban_comercial import status_choices_kanban, status_destino_kanban
from .services.op2_estrutura import OP2_ESTRUTURA_OPCOES, juntar_observacoes_item
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
        dados = request.POST.copy()
        produto_id = dados.get('produto_id')
        dados['produto'] = f'moda:{produto_id}' if produto_id else ''
        grade_total = 0
        for chave, valor in request.POST.items():
            if chave.startswith('grade_'):
                try:
                    grade_total += int(valor or 0)
                except (TypeError, ValueError):
                    messages.error(request, 'Grade: informe apenas números nas quantidades.')
                    return render(request, 'moda/op2_create.html', self._context(request))
        if grade_total:
            dados['quantidade'] = str(grade_total)
        dados['observacoes'] = request.POST.get('item_observacoes') or ''
        form = ItemPedidoProducaoForm(dados, filial=_filial(request))
        if not form.is_valid():
            messages.error(request, 'Produto: ' + '; '.join(
                erro for erros in form.errors.values() for erro in erros
            ))
            return render(request, 'moda/op2_create.html', self._context(request))
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
            item = form.save(commit=False)
            item.pedido = pedido
            item.ordem = 10
            item.status_fluxo = ItemPedidoProducao.StatusFluxo.ORCAMENTO
            item.observacoes = juntar_observacoes_item(
                request.POST.get('item_observacoes') or '', request.POST,
            )
            item.save()

            self._copiar_grade_do_modelo(item)
            quantidades = self._quantidades_grade(request, item)
            if quantidades:
                GradePedidoService.salvar_quantidades(pedido, quantidades)

            if request.POST.get('arte_tecnica') or request.POST.get('arte_local'):
                Personalizacao.objects.create(
                    item=item,
                    tipo=request.POST.get('arte_tipo') or Personalizacao.Tipo.ARTE,
                    tecnica=request.POST.get('arte_tecnica') or Personalizacao.Tecnica.SUBLIMACAO,
                    local=(request.POST.get('arte_local') or '').strip(),
                    observacoes=(request.POST.get('arte_observacoes') or '').strip(),
                )

            pessoa_tamanho = request.POST.get('pessoa_tamanho')
            if pessoa_tamanho and (request.POST.get('pessoa_nome') or request.POST.get('pessoa_numero')):
                tamanho = get_object_or_404(
                    Tamanho.objects.for_filial(_filial(request)).filter(ativo=True),
                    pk=pessoa_tamanho,
                )
                PersonalizacaoIndividual.objects.create(
                    pedido=pedido, item=item, tamanho=tamanho, ordem=10,
                    nome=(request.POST.get('pessoa_nome') or '').strip(),
                    numero=(request.POST.get('pessoa_numero') or '').strip(),
                    observacoes=(request.POST.get('pessoa_observacoes') or '').strip(),
                )

            arquivos = request.FILES.getlist('arquivo')
            for upload in arquivos:
                ArquivoPedido.objects.create(
                    pedido=pedido, arquivo=upload,
                    tipo=request.POST.get('tipo_arquivo') or ArquivoPedido.Tipo.ARTE,
                    descricao=(request.POST.get('descricao_arquivo') or '').strip(),
                    enviado_por=request.user,
                )
            self._salvar_mockups_do_item(request, pedido, item)

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
                    'grade': produto.grade.nome if produto.grade_id else '',
                    'tamanhos': [
                        str(i.tamanho_id) for i in produto.grade.itens.all()
                    ] if produto.grade_id else [],
                }
                for produto in modelos
            },
            'estrutura_opcoes': OP2_ESTRUTURA_OPCOES,
            'tamanhos': Tamanho.objects.for_filial(_filial(request)).filter(
                ativo=True,
            ).order_by('tipo', 'ordem', 'sigla'),
            'tipos_arte': Personalizacao.Tipo.choices,
            'tecnicas_arte': Personalizacao.Tecnica.choices,
            'tipos_arquivo': ArquivoPedido.Tipo.choices,
            'prioridades': PedidoProducao.Prioridade.choices,
        }

    @staticmethod
    def _copiar_grade_do_modelo(item):
        try:
            GradePedidoService.aplicar_grade_do_produto(item)
        except DadosInvalidosError:
            return 0
        return item.grade.count()

    @staticmethod
    def _quantidades_grade(request, item, incluir_zeros=False):
        quantidades = {}
        prefixos = (f'grade_{item.pk}_', 'grade_')
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
            'estrutura_opcoes': OP2_ESTRUTURA_OPCOES,
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
