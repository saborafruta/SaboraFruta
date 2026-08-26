"""
As telas do catálogo da fábrica: matéria-prima, embalagem e acabado.

UMA TELA PARA AS TRÊS CLASSES, com abas. São o mesmo cadastro com
exigências diferentes, e três telas separadas dariam três lugares para
procurar um item cujo tipo a pessoa não lembra — que é o caso comum de quem
acabou de receber uma nota com quinze itens.
"""
import json

from django.contrib import messages
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.core.services.exceptions import DomainError

from .forms_catalogo import (
    ItemCatalogoForm, TipoRapidoForm, UnidadeRapidaForm,
)
from .models import FichaProduto, TipoItem
from .services import CatalogoService
from .views import PolpaBaseView


def _filial(request):
    return request.filial_ativa


class CatalogoListView(PolpaBaseView):
    """O catálogo da fábrica, por classe."""

    area = 'formulacao'

    # A aba padrão vem da rota: o menu tem "Produtos" (acabado) e
    # "Embalagens" apontando para a MESMA tela, e é assim que os dois itens
    # do menu levam ao lugar certo sem duplicar código.
    classe_padrao = ''

    def get(self, request):
        classe = (request.GET.get('classe') or self.classe_padrao or '').strip()
        busca = (request.GET.get('busca') or '').strip()
        tipo = (request.GET.get('tipo') or '').strip()

        fichas = CatalogoService.listar(_filial(request), classe, busca, tipo)
        # `pendencias()` é calculado por item; a lista vem materializada para
        # a tela não repetir a consulta a cada linha.
        linhas = [{'ficha': f, 'pendencias': f.pendencias()} for f in fichas[:300]]

        return render(request, 'polpa/catalogo_list.html', {
            'title': 'Produtos e insumos',
            'linhas': linhas,
            'classe': classe,
            'busca': busca,
            'tipo': tipo,
            'classes': FichaProduto.Classe.choices,
            'tipos': [
                (t.codigo, t.nome)
                for t in TipoItem.por_classe(_filial(request)).get(classe, ())
            ],
            'resumo': CatalogoService.resumo(_filial(request)),
            # PRODUTO DO ERP SEM FICHA existe de verdade (entra por XML de
            # compra ou foi cadastrado antes do vertical). Mostrar a
            # contagem é o que evita a conclusão de que "o produto sumiu".
            'sem_ficha': CatalogoService.sem_ficha(_filial(request)).count(),
            'pode_agir': request.user.tem_permissao('polpa_formulacao', 'criar'),
        })


class ProdutosAcabadosView(CatalogoListView):
    classe_padrao = FichaProduto.Classe.ACABADO


class EmbalagensView(CatalogoListView):
    classe_padrao = FichaProduto.Classe.EMBALAGEM


class MateriasPrimasView(CatalogoListView):
    classe_padrao = FichaProduto.Classe.MATERIA_PRIMA


class TipoAjaxCreateView(PolpaBaseView):
    """
    Cadastra um tipo de item SEM SAIR da ficha, e devolve a opcao pronta.

    A lista dos trinta e cinco cobria polpa, acai e sorvete -- mas cada fabrica
    tem o seu: xarope, cobertura, insumo de limpeza, pote de vidro retornavel.
    Enquanto ela vivia no enum, acrescentar um exigia deploy, e o que acontece
    na pratica e' cadastrar tudo como "Outro ingrediente" -- que e' onde a
    informacao morre.

    O TIPO NASCE NA FILIAL de quem criou. Tipo e' vocabulario da operacao: a
    fabrica de sorvete nao precisa herdar "pote de vidro retornavel" que a de
    polpa inventou, e uma lista comum cresceria com o que ninguem usa.
    """

    area = 'formulacao'
    permissao_acao = 'criar'

    def post(self, request):
        from .models import TipoItem

        filial = _filial(request)
        form = TipoRapidoForm(request.POST, filial=filial)
        if not form.is_valid():
            return JsonResponse(
                {
                    'ok': False,
                    'errors': {
                        campo: [e['message'] for e in mensagens]
                        for campo, mensagens in form.errors.get_json_data().items()
                    },
                },
                status=400,
            )

        # Semeia antes de criar: sem isto, o primeiro tipo proprio de uma filial
        # nova entraria numa lista que ainda nao tem os de sistema, e o codigo
        # dele poderia colidir com um deles na primeira leitura.
        TipoItem.garantir_padroes(filial)
        tipo = TipoItem.objects.create(
            filial=filial,
            codigo=form.codigo(),
            nome=form.cleaned_data['nome'],
            classe=form.cleaned_data['classe'],
            sistema=False,
            ativo=True,
        )
        return JsonResponse({
            'ok': True, 'id': tipo.codigo, 'label': tipo.nome,
            'classe': tipo.classe,
        })


class UnidadeAjaxCreateView(PolpaBaseView):
    """
    Cadastra a unidade SEM SAIR da ficha, e devolve a opcao pronta.

    A tela ja' avisava que sem unidade o item nao salva e dizia onde resolver --
    "Cadastros > Produtos > Unidades". Mas mandar embora no meio do formulario
    e' perder o que ja' foi digitado: volta-se para uma ficha em branco, e a
    segunda tentativa e' a que nao acontece.

    O VINCULO DE FILIAL VEM JUNTO. A unidade e' da EMPRESA, mas a ficha lista
    por `for_filial`, que passa pelo vinculo. Gravar so' a unidade a faria
    nascer invisivel: o cadastro funciona, o select continua vazio, e quem
    clicou conclui que o botao esta' quebrado.
    """

    area = 'formulacao'
    permissao_acao = 'criar'

    def post(self, request):
        from apps.produtos.models import UnidadeMedida, UnidadeMedidaFilial

        filial = _filial(request)
        form = UnidadeRapidaForm(request.POST, empresa=request.user.empresa)
        if not form.is_valid():
            return JsonResponse(
                {
                    'ok': False,
                    'errors': {
                        campo: [e['message'] for e in mensagens]
                        for campo, mensagens in form.errors.get_json_data().items()
                    },
                },
                status=400,
            )

        with transaction.atomic():
            unidade = UnidadeMedida.objects.create(
                empresa=request.user.empresa,
                sigla=form.cleaned_data['sigla'],
                descricao=form.cleaned_data['descricao'],
                tipo=form.cleaned_data.get('tipo') or '',
            )
            UnidadeMedidaFilial.objects.create(
                unidade=unidade, filial=filial, ativo=True,
            )
        return JsonResponse({'ok': True, 'id': unidade.pk, 'label': str(unidade)})


class CatalogoFormView(PolpaBaseView):
    """Cadastra ou corrige um item — produto do ERP e ficha, num ato só."""

    area = 'formulacao'
    permissao_acao = 'criar'

    def get(self, request, pk=None):
        ficha = self._buscar(request, pk) if pk else None
        form = ItemCatalogoForm(
            filial=_filial(request), ficha=ficha,
            initial={'tipo': request.GET.get('tipo') or ''} if not ficha else None,
        )
        return self._tela(request, form, ficha)

    def post(self, request, pk=None):
        ficha = self._buscar(request, pk) if pk else None
        form = ItemCatalogoForm(request.POST, filial=_filial(request), ficha=ficha)
        if not form.is_valid():
            return self._tela(request, form, ficha)

        try:
            salva = CatalogoService.salvar(_filial(request), form.cleaned_data, ficha)
        except DomainError as erro:
            messages.error(request, str(erro))
            return self._tela(request, form, ficha)

        pendencias = salva.pendencias()
        if pendencias:
            # GRAVA E AVISA. O item incompleto é útil (dá para comprar e
            # produzir com ele); o que não pode é a falta passar em silêncio
            # e aparecer só quando o lote sair sem validade.
            messages.warning(
                request, f'{salva.produto.descricao} gravado. ' + ' '.join(pendencias),
            )
        else:
            messages.success(request, f'{salva.produto.descricao} gravado.')
        return redirect(reverse('polpa:catalogo-list') + f'?classe={salva.classe}')

    @staticmethod
    def _buscar(request, pk):
        return get_object_or_404(
            FichaProduto.objects.for_filial(_filial(request)).select_related('produto'),
            pk=pk,
        )

    @staticmethod
    def _tela(request, form, ficha):
        from apps.produtos.models import UnidadeMedida

        from .models import TipoItem

        agrupados = TipoItem.por_classe(_filial(request))
        return render(request, 'polpa/catalogo_form.html', {
            # As tres classes alimentam o modal de tipo novo.
            'classes': FichaProduto.Classe.choices,
            'title': str(ficha.produto) if ficha else 'Novo item',
            'form': form,
            'ficha': ficha,
            # OS GRUPOS SAEM DA TABELA. Antes vinham do enum; agora incluem
            # tambem o que a fabrica criou, e por isso a lista tem de ser
            # montada por filial.
            'tipos_por_classe': [
                (FichaProduto.Classe(classe).label,
                 [(t.codigo, t.nome) for t in tipos])
                for classe, tipos in agrupados.items() if tipos
            ],
            # AS LISTAS VÃO PARA O JAVASCRIPT EM JSON, saindo da MESMA
            # consulta dos grupos acima: a tela mostra os blocos conforme a
            # classe do tipo escolhido, e uma segunda lista escrita no
            # template discordaria dela na primeira mudança.
            **{
                f'tipos_{chave}': json.dumps(
                    [t.codigo for t in agrupados.get(chave, ())]
                )
                for chave in ('materia_prima', 'embalagem', 'acabado')
            },
            # SEM UNIDADE CADASTRADA o item não salva, e o select vazio não
            # explica por quê. A tela diz, e diz onde resolver.
            'tem_unidade': UnidadeMedida.objects.for_filial(_filial(request)).exists(),
        })
