"""
Peso por tecido e grade (grupo Engenharia).

Tela separada da Ficha Técnica de propósito: este peso NÃO é de um
produto, é do TECIDO + TIPO DE PEÇA + GRADE ("camisa em PV, grade
Adulto"), e vale para qualquer produto que bater com essa combinação --
inclusive item de OP sem produto de catálogo nenhum ligado. Prender isto
a uma ficha obrigaria um cadastro por produto para o mesmo peso.

Mesmo padrão de tela que `views_ficha.py` usa para "Peso por tamanho":
escolhe a grade, a tabela nasce com uma linha por tamanho, e os pesos são
digitados e salvos em lote. A diferença é o "dono" da tabela: aqui é o
par (tecido, tipo de peça), lido de query string em vez de vir de um pk
de URL -- tipo de peça é texto livre, não um cadastro com pk próprio.
"""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.db.models import Prefetch
from django.shortcuts import redirect, render
from django.urls import reverse

from .models import Grade, ItemGrade, PesoTecidoGrade, Tecido
from .services.op2_estrutura import OP2_ESTRUTURA_OPCOES
from .views import ModaBaseView


def _filial(request):
    return request.filial_ativa


def _tipos_peca_conhecidos() -> list[str]:
    """Rótulos de "Tipo de peça" já usados na OP 2.0, pro campo sugerir."""
    return sorted({grupo['label'] for grupo in OP2_ESTRUTURA_OPCOES.values()})


def _combos_cadastrados(filial) -> list[dict]:
    """Os pares (tecido, tipo de peça) que já têm alguma linha de peso."""
    linhas = (
        PesoTecidoGrade.objects.filter(filial=filial)
        .select_related('tecido')
        .values('tecido_id', 'tecido__nome', 'tipo_peca')
        .distinct()
        .order_by('tecido__nome', 'tipo_peca')
    )
    combos = []
    for linha in linhas:
        grades = (
            PesoTecidoGrade.objects.filter(
                filial=filial, tecido_id=linha['tecido_id'], tipo_peca=linha['tipo_peca'],
            )
            .values_list('grade__nome', flat=True).distinct()
        )
        combos.append({
            'tecido_id': linha['tecido_id'],
            'tecido_nome': linha['tecido__nome'],
            'tipo_peca': linha['tipo_peca'],
            'grades': sorted(grades),
        })
    return combos


class PesoTecidoView(ModaBaseView):
    template_name = 'moda/peso_tecido.html'

    def get(self, request):
        filial = _filial(request)
        tecido_id = (request.GET.get('tecido') or '').strip()
        tipo_peca = (request.GET.get('tipo_peca') or '').strip()

        tecido = (
            Tecido.objects.for_filial(filial).filter(pk=tecido_id).first()
            if tecido_id else None
        )

        grupos = []
        grades_sem_peso = []
        if tecido and tipo_peca:
            linhas = (
                PesoTecidoGrade.objects.filter(
                    filial=filial, tecido=tecido, tipo_peca=tipo_peca,
                )
                .select_related('grade', 'tamanho')
                .order_by('grade__nome', 'ordem')
            )
            por_grade: dict[int, dict] = {}
            for linha in linhas:
                grupo = por_grade.setdefault(linha.grade_id, {
                    'grade_id': linha.grade_id, 'grade_nome': linha.grade.nome, 'linhas': [],
                })
                grupo['linhas'].append(linha)
            grupos = sorted(por_grade.values(), key=lambda g: g['grade_nome'])

            ja_pesadas = set(por_grade.keys())
            grades_sem_peso = [
                {'id': g.pk, 'nome': g.nome, 'tipo': g.get_tipo_display(), 'resumo': g.resumo}
                for g in Grade.objects.for_filial(filial).filter(ativo=True).exclude(pk__in=ja_pesadas)
            ]

        return render(request, self.template_name, {
            'title': 'Peso por tecido e grade',
            'tecidos': Tecido.objects.for_filial(filial).filter(ativo=True),
            'tipos_peca_conhecidos': _tipos_peca_conhecidos(),
            'tecido_escolhido': tecido,
            'tipo_peca_escolhido': tipo_peca,
            'grupos': grupos,
            'grades_sem_peso': grades_sem_peso,
            'existe_grade_cadastrada': Grade.objects.for_filial(filial).filter(ativo=True).exists(),
            'combos_cadastrados': _combos_cadastrados(filial),
        })


class PesoTecidoGradeAddView(ModaBaseView):
    """Acrescenta a tabela de peso de uma grade, para um tecido + tipo de peça."""

    permissao_acao = 'editar'

    def post(self, request):
        filial = _filial(request)
        tecido_id = (request.POST.get('tecido') or '').strip()
        tipo_peca = (request.POST.get('tipo_peca') or '').strip()
        grade_pk = (request.POST.get('grade') or '').strip()
        destino = f"{reverse('moda:peso-tecido')}?tecido={tecido_id}&tipo_peca={tipo_peca}"

        if not tecido_id or not tipo_peca or not grade_pk:
            messages.error(request, 'Escolha o tecido, o tipo de peça e a grade.')
            return redirect(destino)

        tecido = Tecido.objects.for_filial(filial).filter(pk=tecido_id).first()
        grade = Grade.objects.for_filial(filial).prefetch_related(
            Prefetch('itens', queryset=ItemGrade.objects.select_related('tamanho')),
        ).filter(pk=grade_pk).first()
        if not tecido or not grade:
            messages.error(request, 'Tecido ou grade não encontrados.')
            return redirect(destino)

        criadas = 0
        for item in grade.itens.all():
            _, criou = PesoTecidoGrade.objects.get_or_create(
                filial=filial, tecido=tecido, tipo_peca=tipo_peca,
                grade=grade, tamanho=item.tamanho,
                defaults={'ordem': item.ordem},
            )
            criadas += criou

        if criadas:
            messages.success(request, f'Grade {grade.nome} acrescentada — {criadas} tamanho(s) para pesar.')
        else:
            messages.info(request, f'Grade {grade.nome} já estava na tabela.')
        return redirect(destino)


class PesoTecidoGradeRemoverView(ModaBaseView):
    """Tira uma grade inteira da tabela de peso deste tecido + tipo de peça."""

    permissao_acao = 'editar'

    def post(self, request, grade_pk):
        filial = _filial(request)
        tecido_id = (request.POST.get('tecido') or '').strip()
        tipo_peca = (request.POST.get('tipo_peca') or '').strip()
        destino = f"{reverse('moda:peso-tecido')}?tecido={tecido_id}&tipo_peca={tipo_peca}"

        linhas = PesoTecidoGrade.objects.filter(
            filial=filial, tecido_id=tecido_id, tipo_peca=tipo_peca, grade_id=grade_pk,
        )
        nome = linhas.first().grade.nome if linhas.exists() else None
        linhas.delete()

        if nome:
            messages.success(request, f'Grade {nome} removida da tabela de peso.')
        else:
            messages.info(request, 'Essa grade não estava na tabela.')
        return redirect(destino)


class PesoTecidoSalvarView(ModaBaseView):
    """Grava os pesos digitados na tabela, linha a linha."""

    permissao_acao = 'editar'

    def post(self, request):
        filial = _filial(request)
        tecido_id = (request.POST.get('tecido') or '').strip()
        tipo_peca = (request.POST.get('tipo_peca') or '').strip()
        destino = f"{reverse('moda:peso-tecido')}?tecido={tecido_id}&tipo_peca={tipo_peca}"

        linhas = {
            l.pk: l for l in PesoTecidoGrade.objects.filter(
                filial=filial, tecido_id=tecido_id, tipo_peca=tipo_peca,
            )
        }
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
            PesoTecidoGrade.objects.bulk_update(alteradas, ['peso_g'])
            messages.success(request, f'{len(alteradas)} peso(s) atualizado(s).')
        else:
            messages.info(request, 'Nada mudou.')
        return redirect(destino)
