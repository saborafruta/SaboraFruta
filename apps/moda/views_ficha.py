"""
Telas da Ficha Técnica (grupo Engenharia).

Arquivo separado de `views_cadastros.py` porque a ficha não é um cadastro
de apoio: ela tem tela de detalhe com lista de materiais, cálculo de custo
e anexos. Misturar com as telas de Grade/Cor/Produto engordaria um arquivo
que já é grande sem nenhum ganho.
"""
from django.contrib import messages
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .forms import FichaTecnicaForm, ImagemFichaForm, MaterialFichaForm
from .models import (
    FichaTecnica, Grade, ImagemFicha, ItemGrade, MaterialFicha, PesoTamanhoFicha,
)
from .views import ModaBaseView


def _filial(request):
    return request.filial_ativa


def _ficha_da_filial(request, pk) -> FichaTecnica:
    """
    Busca a ficha já com o que a tela precisa.

    O `select_related` do produto não é enfeite: a tela lê referência,
    modelo, coleção, tecido e grade dele, e sem isto seriam cinco consultas
    por ficha aberta.
    """
    return get_object_or_404(
        FichaTecnica.objects.for_filial(_filial(request)).select_related(
            'produto', 'produto__modelo', 'produto__colecao',
            'produto__tecido', 'produto__grade', 'produto__marca',
        ).prefetch_related('materiais', 'imagens'),
        pk=pk,
    )


def _informacoes(produto) -> list[tuple[str, str]]:
    """
    O bloco de informações da ficha, lido do produto.

    Montado aqui e não no template porque cada linha é um caminho de FK que
    pode ser nulo: no template viraria uma pilha de `{% if %}`, e um deles
    esquecido deixaria a ficha exibindo "None".
    """
    return [
        ('Produto', produto.nome),
        ('Código', produto.codigo),
        ('Referência', produto.referencia),
        ('Modelo', str(produto.modelo) if produto.modelo_id else ''),
        ('Coleção', str(produto.colecao) if produto.colecao_id else ''),
        ('Tecido', str(produto.tecido) if produto.tecido_id else ''),
        ('Composição', produto.composicao),
        # A grade sai com os tamanhos: o nome sozinho não diz se a peça vai
        # até GG ou até XGG, e é isso que a fábrica precisa saber.
        ('Grade', produto.grade.resumo_com_nome if produto.grade_id else ''),
    ]


def _grades_da_filial(filial, escolhida_id=None) -> list[dict]:
    """
    TODAS as grades cadastradas, com os tamanhos de cada uma.

    A ficha mostrava só o nome da grade do produto -- e quando o produto
    não tinha grade, um traço. Um traço não diz se falta cadastrar a grade
    ou se falta ligá-la ao produto, e são problemas diferentes: o primeiro
    resolve-se na tela de Grades, o segundo aqui.

    Os tamanhos vêm junto porque é o que a fábrica lê. "Adulto" não diz se
    a peça sai até GG ou até XGG; "PP | P | M | G | GG | XGG" diz.

    Uma consulta para todas as grades, com os tamanhos pré-carregados: uma
    por grade seria uma consulta por card da tela.
    """
    grades = (
        Grade.objects.for_filial(filial).filter(ativo=True)
        .prefetch_related(
            Prefetch('itens', queryset=ItemGrade.objects.select_related('tamanho')),
        )
    )
    return [
        {
            'id': grade.pk,
            'nome': grade.nome,
            'tipo': grade.get_tipo_display(),
            'resumo': grade.resumo,
            'tamanhos': [t.sigla for t in grade.tamanhos_ordenados()],
            'escolhida': grade.pk == escolhida_id,
        }
        for grade in grades
    ]


def _grades_sem_peso(filial, ficha) -> list[dict]:
    """
    Grades da filial que a ficha ainda não pesou — o que aparece no
    "acrescentar grade" da tabela de peso.

    Não reaproveita `_grades_da_filial` porque ali "escolhida" é a grade do
    produto (uma só); aqui o corte é outro: quais grades já têm uma tabela
    de peso nesta ficha, que pode ter várias ao mesmo tempo.
    """
    ja_pesadas = set(ficha.pesos_tamanho.values_list('grade_id', flat=True))
    grades = Grade.objects.for_filial(filial).filter(ativo=True).exclude(pk__in=ja_pesadas)
    return [
        {'id': grade.pk, 'nome': grade.nome, 'tipo': grade.get_tipo_display(), 'resumo': grade.resumo}
        for grade in grades
    ]


def _marcar_ultima_alteracao(fichas) -> None:
    """
    Quando cada ficha mudou pela última vez, e por quem.

    UMA consulta para a lista inteira, agrupando o log por registro. Uma
    por card seria uma consulta por linha da tela -- e é justamente o
    tipo de coluna que parece barata e não é.

    Só o cabeçalho da ficha entra aqui. Mexer num material também é
    mexer na ficha, mas essa história completa está no histórico; no card
    o que interessa é "isto foi tocado recentemente?".
    """
    from django.db.models import Max

    from apps.core.models import LogSistema
    from apps.moda.models import FichaTecnica

    if not fichas:
        return

    linhas = (
        LogSistema.objects
        .filter(
            tabela_afetada=FichaTecnica._meta.db_table,
            registro_id__in=[f.pk for f in fichas],
        )
        .values('registro_id')
        .annotate(quando=Max('data_hora'))
    )
    quando_por_ficha = {l['registro_id']: l['quando'] for l in linhas}

    # O nome de quem fez vem numa segunda consulta, só das linhas mais
    # recentes: trazer o usuário no agrupamento exigiria uma subconsulta
    # por ficha, que é o que se está evitando.
    recentes = (
        LogSistema.objects
        .filter(
            tabela_afetada=FichaTecnica._meta.db_table,
            registro_id__in=list(quando_por_ficha),
            data_hora__in=list(quando_por_ficha.values()),
        )
        .select_related('usuario')
    )
    quem = {l.registro_id: getattr(l.usuario, 'nome', '') or 'Sistema' for l in recentes}

    # Pendurado em cada ficha, e não devolvido num dicionário à parte: no
    # template, casar uma lista com um dicionário exigiria um laço dentro do
    # outro para cada card.
    for ficha in fichas:
        quando = quando_por_ficha.get(ficha.pk)
        ficha.ultima_alteracao = (
            {'quando': quando, 'quem': quem.get(ficha.pk, '')} if quando else None
        )


class FichaListView(ModaBaseView):
    """Lista das fichas. Entregue no endereço do menu (engenharia/ficha-tecnica)."""

    def get(self, request):
        fichas = (
            FichaTecnica.objects.for_filial(_filial(request))
            .select_related('produto', 'produto__modelo')
            .prefetch_related('materiais')
        )

        busca = (request.GET.get('q') or '').strip()
        if busca:
            fichas = fichas.filter(produto__nome__icontains=busca) | fichas.filter(
                produto__codigo__icontains=busca
            )

        fichas = list(fichas)
        _marcar_ultima_alteracao(fichas)

        return render(request, 'moda/ficha_list.html', {
            'title': 'Ficha Técnica',
            'fichas': fichas,
            'busca': busca,
            'pode_excluir': request.user.tem_permissao('moda_pcp', 'excluir'),
        })


class FichaFormView(ModaBaseView):
    """Cria e edita o cabeçalho da ficha."""

    permissao_acao = 'editar'

    def get(self, request, pk=None):
        ficha = _ficha_da_filial(request, pk) if pk else None
        return self._render(request, FichaTecnicaForm(
            instance=ficha, filial=_filial(request),
        ), ficha)

    def post(self, request, pk=None):
        ficha = _ficha_da_filial(request, pk) if pk else None
        form = FichaTecnicaForm(
            request.POST, request.FILES, instance=ficha, filial=_filial(request),
        )

        if not form.is_valid():
            return self._render(request, form, ficha)

        nova = form.save(commit=False)
        # A filial vem da tela, não do formulário: aceitar do POST deixaria
        # gravar ficha na filial de outra unidade.
        nova.filial = _filial(request)
        nova.save()

        messages.success(request, f'Ficha de {nova.produto.nome} salva.')
        return redirect(reverse('moda:ficha-detail', args=[nova.pk]))

    @staticmethod
    def _render(request, form, ficha):
        from apps.moda.models import ProdutoModa
        from apps.moda.services.importar_produtos import ImportarProdutosService

        filial = _filial(request)
        disponiveis = getattr(form, 'produtos_disponiveis', [])

        # Quando não há produto para escolher, a tela precisa dizer POR
        # QUÊ. Um select vazio sem explicação é o que trava a pessoa: ela
        # não sabe se falta cadastrar produto ou se todos já têm ficha.
        total_produtos = ProdutoModa.objects.filter(filial=filial).count()
        # SEMPRE contado, e não só quando a confecção está vazia: o campo
        # de produto lê os dois catálogos agora, então é o total dos dois
        # que decide se há o que escolher.
        do_erp = len(ImportarProdutosService.disponiveis(filial))

        return render(request, 'moda/ficha_form.html', {
            'title': 'Editar ficha técnica' if ficha else 'Nova ficha técnica',
            'form': form,
            'ficha': ficha,
            'produtos': disponiveis,
            # O que cada produto traz junto, para a tela mostrar assim
            # que alguém escolhe. Vai por `json_script` e não interpolado
            # no atributo: nome de produto tem aspas e acento, e no
            # atributo quebraria o HTML na primeira aspa.
            'produtos_json': {
                str(p.pk): {
                    'modelo': str(p.modelo) if p.modelo_id else '',
                    'colecao': str(p.colecao) if p.colecao_id else '',
                    'tecido': str(p.tecido) if p.tecido_id else '',
                    'grade': str(p.grade) if p.grade_id else '',
                }
                for p in disponiveis
            },
            'total_produtos': total_produtos,
            'ja_com_ficha': total_produtos - len(disponiveis),
            'do_erp': do_erp,
        })


class FichaDetailView(ModaBaseView):
    """A ficha completa: informações, materiais e o custo que sai deles."""

    def get(self, request, pk):
        ficha = _ficha_da_filial(request, pk)
        return render(request, 'moda/ficha_detail.html', self.contexto(request, ficha))

    @staticmethod
    def contexto(request, ficha, form_material=None) -> dict:
        return {
            'title': f'Ficha {ficha.produto.codigo}',
            'ficha': ficha,
            'produto': ficha.produto,
            'informacoes': _informacoes(ficha.produto),
            'materiais': ficha.materiais.all(),
            'imagens': ficha.imagens.all(),
            'custo_por_tipo': ficha.custo_por_tipo,
            'form_material': form_material or MaterialFichaForm(),
            'form_imagem': ImagemFichaForm(),
            'grades': _grades_da_filial(
                _filial(request), ficha.produto.grade_id,
            ),
            'grades_sem_peso': _grades_sem_peso(_filial(request), ficha),
        }


class FichaGradeView(ModaBaseView):
    """
    Escolhe a grade da peça sem sair da ficha.

    A grade é campo do PRODUTO, e continua sendo -- isto aqui grava lá, não
    numa cópia na ficha. O que muda é o caminho: quem está especificando a
    peça percebe que falta grade aqui, e mandá-lo à tela do produto e de
    volta só para marcar um radio é atrito que faz a ficha descer sem grade.
    """

    permissao_acao = 'editar'

    def post(self, request, pk):
        ficha = _ficha_da_filial(request, pk)
        produto = ficha.produto
        escolhida = (request.POST.get('grade') or '').strip()

        if not escolhida:
            produto.grade = None
            produto.save(update_fields=['grade'])
            messages.success(request, 'Grade retirada do produto.')
            return redirect(reverse('moda:ficha-detail', args=[ficha.pk]))

        grade = get_object_or_404(
            Grade.objects.for_filial(_filial(request)), pk=escolhida,
        )
        produto.grade = grade
        produto.save(update_fields=['grade'])
        messages.success(
            request,
            f'Grade {grade.nome} ({grade.resumo}) aplicada ao produto {produto.codigo}.',
        )
        return redirect(reverse('moda:ficha-detail', args=[ficha.pk]))


class FichaPesoGradeAddView(ModaBaseView):
    """
    Acrescenta a tabela de peso de uma grade à ficha.

    Cria uma linha por tamanho da grade, com peso em branco -- é o que faz
    a tabela aparecer vazia, esperando ser preenchida, em vez de a grade
    simplesmente não estar lá até alguém digitar o primeiro peso.
    """

    permissao_acao = 'editar'

    def post(self, request, pk):
        ficha = _ficha_da_filial(request, pk)
        grade_pk = (request.POST.get('grade') or '').strip()
        if not grade_pk:
            messages.error(request, 'Escolha uma grade.')
            return redirect(reverse('moda:ficha-detail', args=[ficha.pk]))

        grade = get_object_or_404(
            Grade.objects.for_filial(_filial(request)).prefetch_related(
                Prefetch('itens', queryset=ItemGrade.objects.select_related('tamanho')),
            ),
            pk=grade_pk,
        )
        criadas = 0
        for item in grade.itens.all():
            _, criou = PesoTamanhoFicha.objects.get_or_create(
                ficha=ficha, grade=grade, tamanho=item.tamanho,
                defaults={'ordem': item.ordem},
            )
            criadas += criou

        if criadas:
            messages.success(request, f'Grade {grade.nome} acrescentada — {criadas} tamanho(s) para pesar.')
        else:
            messages.info(request, f'Grade {grade.nome} já estava na tabela de peso.')
        return redirect(reverse('moda:ficha-detail', args=[ficha.pk]))


class FichaPesoGradeRemoverView(ModaBaseView):
    """Tira uma grade inteira da tabela de peso."""

    permissao_acao = 'editar'

    def post(self, request, pk, grade_pk):
        ficha = _ficha_da_filial(request, pk)
        linhas = PesoTamanhoFicha.objects.filter(ficha=ficha, grade_id=grade_pk)
        nome = linhas.first().grade.nome if linhas.exists() else None
        apagadas, _ = linhas.delete()

        if nome:
            messages.success(request, f'Grade {nome} removida da tabela de peso.')
        else:
            messages.info(request, 'Essa grade não estava na tabela de peso.')
        return redirect(reverse('moda:ficha-detail', args=[ficha.pk]))


class FichaPesoSalvarView(ModaBaseView):
    """
    Grava os pesos digitados na tabela, linha a linha.

    Mesmo padrão do `MaterialUpdateView`: um campo por linha (`peso_<id>`),
    e só o que mudou entra no `bulk_update`.
    """

    permissao_acao = 'editar'

    def post(self, request, pk):
        from decimal import Decimal, InvalidOperation

        ficha = _ficha_da_filial(request, pk)
        linhas = {l.pk: l for l in ficha.pesos_tamanho.all()}
        alteradas = []

        for chave, bruto in request.POST.items():
            if not chave.startswith('peso_'):
                continue
            try:
                linha = linhas[int(chave[len('peso_'):])]
            except (ValueError, KeyError):
                continue

            bruto = (bruto or '').strip()
            if not bruto:
                valor = None
            else:
                try:
                    valor = Decimal(bruto.replace(',', '.'))
                except InvalidOperation:
                    continue
                if valor < 0:
                    continue

            if linha.peso_g != valor:
                linha.peso_g = valor
                alteradas.append(linha)

        if alteradas:
            PesoTamanhoFicha.objects.bulk_update(alteradas, ['peso_g'])
            messages.success(request, f'{len(alteradas)} peso(s) atualizado(s).')
        else:
            messages.info(request, 'Nada mudou.')

        return redirect(reverse('moda:ficha-detail', args=[ficha.pk]))


class MaterialCreateView(ModaBaseView):
    """Acrescenta um material à ficha."""

    permissao_acao = 'editar'

    def post(self, request, pk):
        ficha = _ficha_da_filial(request, pk)
        form = MaterialFichaForm(request.POST)

        if not form.is_valid():
            # Erro volta na própria ficha, com o formulário preenchido —
            # reabrir vazio faria digitar consumo e custo de novo.
            return render(
                request, 'moda/ficha_detail.html',
                FichaDetailView.contexto(request, ficha, form_material=form),
            )

        material = form.save(commit=False)
        material.ficha = ficha
        # Entra no fim da lista. `count()` e não `len()`: só o número
        # interessa, e a lista inteira já não está em memória aqui.
        material.ordem = ficha.materiais.count()
        material.save()

        messages.success(request, f'{material.get_tipo_display()} acrescentado.')
        return redirect(reverse('moda:ficha-detail', args=[ficha.pk]))


class MaterialUpdateView(ModaBaseView):
    """
    Altera consumo, perda e custo direto na tabela.

    Só esses três, e não o material inteiro: são os que se ajustam olhando o
    custo fechar. Trocar tipo ou descrição é caso de remover e acrescentar,
    e um formulário completo por linha deixaria a tabela ilegível.
    """

    permissao_acao = 'editar'

    CAMPOS = ('consumo', 'perda', 'custo_unitario')

    def post(self, request, pk):
        from decimal import Decimal, InvalidOperation

        ficha = _ficha_da_filial(request, pk)
        materiais = {m.pk: m for m in ficha.materiais.all()}
        alterados = []

        for chave, bruto in request.POST.items():
            partes = chave.split('_', 1)
            if len(partes) != 2 or partes[0] != 'mat':
                continue
            id_campo = partes[1].split('-')
            if len(id_campo) != 2:
                continue
            try:
                material = materiais[int(id_campo[0])]
                campo = id_campo[1]
                if campo not in self.CAMPOS:
                    continue
                valor = Decimal((bruto or '0').replace(',', '.'))
            except (ValueError, KeyError, InvalidOperation):
                continue

            if valor < 0 or (campo == 'perda' and valor > 100):
                continue
            if getattr(material, campo) != valor:
                setattr(material, campo, valor)
                if material not in alterados:
                    alterados.append(material)

        if alterados:
            MaterialFicha.objects.bulk_update(alterados, list(self.CAMPOS))
            messages.success(request, f'{len(alterados)} material(is) atualizado(s).')
        else:
            messages.info(request, 'Nada mudou.')

        return redirect(reverse('moda:ficha-detail', args=[ficha.pk]))


class MaterialDeleteView(ModaBaseView):
    permissao_acao = 'editar'

    def post(self, request, pk, material_pk):
        ficha = _ficha_da_filial(request, pk)
        material = get_object_or_404(MaterialFicha, pk=material_pk, ficha=ficha)
        rotulo = material.get_tipo_display()
        material.delete()
        messages.success(request, f'{rotulo} removido da ficha.')
        return redirect(reverse('moda:ficha-detail', args=[ficha.pk]))


class ImagemCreateView(ModaBaseView):
    permissao_acao = 'editar'

    def post(self, request, pk):
        ficha = _ficha_da_filial(request, pk)
        form = ImagemFichaForm(request.POST, request.FILES)

        if not form.is_valid():
            messages.error(request, 'Não foi possível anexar a imagem. Envie um arquivo de imagem válido.')
            return redirect(reverse('moda:ficha-detail', args=[ficha.pk]))

        imagem = form.save(commit=False)
        imagem.ficha = ficha
        imagem.ordem = ficha.imagens.count()
        imagem.save()

        messages.success(request, 'Imagem anexada.')
        return redirect(reverse('moda:ficha-detail', args=[ficha.pk]))


class ImagemDeleteView(ModaBaseView):
    permissao_acao = 'editar'

    def post(self, request, pk, imagem_pk):
        ficha = _ficha_da_filial(request, pk)
        get_object_or_404(ImagemFicha, pk=imagem_pk, ficha=ficha).delete()
        messages.success(request, 'Imagem removida.')
        return redirect(reverse('moda:ficha-detail', args=[ficha.pk]))


class ImportarProdutosView(ModaBaseView):
    """
    Traz produtos do catálogo do ERP para a confecção.

    É a resposta para o caso mais comum de tela travada: a empresa já
    cadastrou o produto no ERP e o select da ficha aparece vazio, porque
    o produto de MODA -- o que tem modelo, tecido e grade -- ainda não
    existe. Em vez de mandar redigitar tudo, traz o que já está lá.
    """

    permissao_acao = 'criar'

    def get(self, request):
        from apps.moda.services.importar_produtos import ImportarProdutosService

        return render(request, 'moda/importar_produtos.html', {
            'title': 'Trazer produtos do ERP',
            'produtos': ImportarProdutosService.disponiveis(_filial(request)),
        })

    def post(self, request):
        from apps.core.services.exceptions import DomainError
        from apps.moda.services.importar_produtos import ImportarProdutosService

        ids = [
            int(v) for v in request.POST.getlist('produtos') if v.isdigit()
        ]
        try:
            criados = ImportarProdutosService.importar(
                _filial(request), ids, request.user,
            )
        except DomainError as erro:
            messages.error(request, str(erro))
            return redirect(reverse('moda:produtos-importar'))

        messages.success(
            request,
            f'{len(criados)} produto(s) trazidos do ERP. Complete modelo, '
            f'tecido e grade — a ficha técnica lê esses campos de lá.',
        )
        return redirect(reverse('moda:produto-list'))

class FichaHistoricoView(ModaBaseView):
    """
    Quem mexeu na ficha, quando, e o que mudou.

    A ficha define custo e consumo da peça. Saber que material entrou,
    saiu ou teve o consumo alterado -- e por quem -- é o que permite
    explicar por que o custo do produto mudou de um mês para o outro.
    """

    def get(self, request, pk):
        from apps.moda.services.historico import HistoricoService

        ficha = _ficha_da_filial(request, pk)
        eventos = HistoricoService.da_ficha(ficha)
        return render(request, 'moda/historico.html', {
            'title': f'Histórico da ficha {ficha.produto.codigo}',
            'raiz': f'Ficha {ficha.produto.codigo} — {ficha.produto.nome}',
            'subtitulo': f'Versão {ficha.versao} · {ficha.get_status_display()}',
            'voltar': ('moda:ficha-detail', ficha.pk),
            'eventos': eventos,
            'marcos': [e for e in eventos if e.marco],
        })


class FichaDeleteView(ModaBaseView):
    """
    Apaga a ficha — com a trava que evita o estrago.

    A ORDEM DE PRODUÇÃO LÊ A FICHA, não copia: apagá-la enquanto há OP
    aberta deixaria a fábrica sem lista de material no meio do trabalho, e
    o custo da ordem viraria zero sem ninguém decidir isso.

    Para tirar a ficha de circulação sem perder a história, o caminho é
    marcá-la como OBSOLETA — e a mensagem de bloqueio diz isso, porque é
    quase sempre o que a pessoa queria.
    """

    permissao_acao = 'excluir'

    def post(self, request, pk):
        from apps.moda.models import OrdemProducao

        ficha = _ficha_da_filial(request, pk)
        produto = ficha.produto

        abertas = (
            OrdemProducao.objects.for_filial(_filial(request))
            .filter(item__produto=produto)
            .exclude(status__in=OrdemProducao.STATUS_ENCERRADOS)
            .count()
        )
        if abertas:
            messages.error(
                request,
                f'{produto.nome} tem {abertas} ordem(ns) de produção em '
                f'aberto, e elas leem esta ficha. Marque a ficha como '
                f'obsoleta em vez de apagar.',
            )
            return redirect(reverse('moda:ficha-detail', args=[ficha.pk]))

        materiais = ficha.materiais.count()
        ficha.delete()
        messages.success(
            request,
            f'Ficha de {produto.nome} apagada, com {materiais} material(is). '
            f'O produto continua cadastrado.',
        )
        return redirect(reverse('moda:ficha-list'))