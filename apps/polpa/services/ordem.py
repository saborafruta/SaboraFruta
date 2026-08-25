"""
As regras da ordem de produção da polpa.

O QUE ESTE SERVIÇO FAZ E O QUE ELE DELEGA:

  · OS ESTADOS são daqui. Planejada, liberada, em produção, pausada, em
    qualidade, produzida e cancelada descrevem esta fábrica; a OP do ERP
    tem cinco status e é atualizada em conjunto, pelo mapa que vive no
    modelo. Duas verdades sobre "onde a OP está" seria o pior resultado
    possível, e por isso nunca se grava uma sem a outra;

  · O CONSUMO DE ESTOQUE é do ERP. `OrdemProducaoService.encerrar` já baixa
    matéria-prima por FEFO, cria o lote do produto acabado, dá entrada no
    estoque e calcula custo e rendimento. Reescrever isso aqui daria uma
    produção que não mexe no saldo -- e saldo que não bate é o fim da
    confiança no sistema inteiro.

O QUE O VERTICAL ACRESCENTA NO ENCERRAMENTO é a VALIDADE do lote, calculada
pelo prazo do produto (seção 1). O ERP aceita a data mas não sabe
calculá-la; enquanto isso, ela era digitada à mão a cada produção.

LIBERAR NÃO TRAVA POR FALTA DE ESTOQUE, e isso é deliberado. A fruta chega
durante o dia: liberar a OP de manhã com a manga ainda no caminhão é o
normal desta indústria, não um erro. O que o sistema faz é MOSTRAR a
necessidade e o que falta, na hora de liberar. A trava de verdade continua
existindo onde ela protege alguma coisa: no encerramento, que é quando o
estoque é de fato consumido — não dá para baixar o que não existe.
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.services.exceptions import DomainError
from apps.estoque.models import Estoque
from apps.polpa.models import FichaProduto, OrdemPolpa, Receita
from apps.producao.models import FichaTecnica, OrdemProducao

ZERO = Decimal('0')
S = OrdemPolpa.Situacao


class OrdemPolpaService:

    # ── Abertura ─────────────────────────────────────────────────────────

    @staticmethod
    @transaction.atomic
    def criar(filial, receita: Receita, dados: dict, usuario=None) -> OrdemPolpa:
        """
        Abre a OP a partir de uma receita.

        SÓ RECEITA ATIVA produz. Uma versão em rascunho é justamente a que
        alguém está mexendo — produzir por ela é produzir por uma fórmula
        que ninguém terminou de decidir.
        """
        if receita.ficha.status != FichaTecnica.Status.ATIVA:
            raise DomainError(
                f'A versão {receita.versao} não está ativa. Ative-a antes de '
                'produzir, ou escolha a versão que está valendo.'
            )

        quantidade = dados.get('quantidade_planejada') or ZERO
        if quantidade <= ZERO:
            raise DomainError('Informe quanto se pretende produzir.')

        ordem = OrdemProducao.objects.create(
            filial=filial,
            numero=OrdemPolpaService._proximo_numero(filial),
            ficha_tecnica=receita.ficha,
            produto_acabado=receita.produto,
            quantidade_planejada=quantidade,
            status=OrdemProducao.Status.RASCUNHO,
            data_inicio_prevista=dados.get('data_inicio_prevista'),
            data_fim_prevista=dados.get('data_fim_prevista'),
            usuario_abertura=usuario,
            observacao=dados.get('observacao') or '',
        )
        op = OrdemPolpa.objects.create(
            filial=filial, ordem=ordem, receita=receita,
            responsavel=dados.get('responsavel') or usuario,
            observacao=dados.get('observacao') or '',
        )

        # AS ETAPAS NASCEM COM A ORDEM. Criadas sob demanda, a OP mostraria
        # só o que já foi tocado -- e "não iniciada" ficaria indistinguível
        # de "não existe", que é justamente o que se quer saber.
        from apps.polpa.services.processo import ProcessoService

        ProcessoService.preparar(op)
        return op

    @staticmethod
    def _proximo_numero(filial) -> str:
        """OP-2026-000001: o ano no número evita reiniciar e confundir."""
        ano = timezone.localdate().year
        prefixo = f'OP-{ano}-'
        ultimo = (
            OrdemProducao.objects
            .filter(filial=filial, numero__startswith=prefixo)
            .order_by('-numero')
            .values_list('numero', flat=True)
            .first()
        )
        sequencial = 1
        if ultimo:
            try:
                sequencial = int(ultimo.rsplit('-', 1)[1]) + 1
            except (ValueError, IndexError):
                sequencial = 1
        return f'{prefixo}{sequencial:06d}'

    # ── Necessidade ──────────────────────────────────────────────────────

    @staticmethod
    def necessidade(op: OrdemPolpa) -> dict:
        """
        Quanto de cada insumo esta OP precisa, e quanto existe.

        CALCULADA, NÃO GRAVADA. A receita está presa na OP pela VERSÃO, e a
        quantidade planejada está na ordem: a necessidade sai das duas a
        qualquer momento e sempre dá o mesmo número. Uma cópia gravada seria
        uma terceira verdade, que envelheceria em silêncio se alguém mudasse
        a quantidade planejada.

        INGREDIENTE E EMBALAGEM SEPARADOS porque quem separa é gente
        diferente: a fruta sai da câmara, o pote sai do almoxarifado.
        """
        ficha = op.ordem.ficha_tecnica
        base = ficha.quantidade_produzida or ZERO
        if base <= ZERO:
            return {'ingredientes': [], 'embalagens': [], 'faltas': [], 'fator': ZERO}

        fator = op.quantidade_planejada / base
        ingredientes, embalagens, faltas = [], [], []

        for item in ficha.itens.select_related(
            'materia_prima', 'materia_prima__unidade_medida',
            'materia_prima__ficha_polpa',
        ):
            produto = item.materia_prima
            necessario = (item.quantidade_com_perda() * fator).quantize(Decimal('0.001'))
            disponivel = OrdemPolpaService._saldo(produto, op.filial)
            falta = max(necessario - disponivel, ZERO)

            linha = {
                'produto': produto,
                'unidade': getattr(produto.unidade_medida, 'sigla', ''),
                'necessario': necessario,
                'disponivel': disponivel,
                'falta': falta,
            }
            ficha_produto = getattr(produto, 'ficha_polpa', None)
            if ficha_produto and ficha_produto.classe == FichaProduto.Classe.EMBALAGEM:
                embalagens.append(linha)
            else:
                ingredientes.append(linha)
            if falta > ZERO:
                faltas.append(linha)

        return {
            'ingredientes': ingredientes,
            'embalagens': embalagens,
            'faltas': faltas,
            'fator': fator,
        }

    @staticmethod
    def _saldo(produto, filial) -> Decimal:
        estoque = Estoque.objects.filter(produto=produto, filial=filial).first()
        if estoque is None:
            return ZERO
        return estoque.quantidade_disponivel or ZERO

    # ── Movimento de estado ──────────────────────────────────────────────

    @classmethod
    @transaction.atomic
    def mover(cls, op: OrdemPolpa, destino: str, usuario=None, dados: dict | None = None):
        """
        Leva a OP para a situação indicada, com as regras de cada passagem.

        A TABELA DE TRANSIÇÕES decide o que é possível; este método cuida do
        que cada passagem GRAVA além do estado. Separar as duas coisas é o
        que evita um `if` novo a cada tela, e é no `if` esquecido que se
        produz sem liberar.
        """
        dados = dados or {}
        if op.encerrada:
            raise DomainError('Esta ordem já foi encerrada.')
        if not op.pode_ir_para(destino):
            atual = op.get_situacao_display()
            raise DomainError(
                f'Uma ordem {atual.lower()} não vai direto para '
                f'"{OrdemPolpa.Situacao(destino).label.lower()}".'
            )

        agora = timezone.now()

        if destino == S.EM_PRODUCAO:
            if op.situacao == S.PAUSADA and op.pausada_em:
                # O TEMPO PARADO É SOMADO, não substituído: uma OP que parou
                # três vezes tem três pausas, e o total é o que explica por
                # que a batida levou o dobro do previsto.
                parada = int((agora - op.pausada_em).total_seconds() // 60)
                op.minutos_parados += max(parada, 0)
                op.pausada_em = None
                op.motivo_pausa = ''
            elif not op.ordem.data_inicio_real:
                op.ordem.data_inicio_real = agora

        elif destino == S.PAUSADA:
            motivo = (dados.get('motivo') or '').strip()
            if not motivo:
                raise DomainError(
                    'Diga por que a linha parou — é o registro que explica o '
                    'tempo perdido no fim do mês.'
                )
            op.pausada_em = agora
            op.motivo_pausa = motivo[:160]

        elif destino == S.QUALIDADE:
            op.enviada_qualidade_em = agora

        elif destino == S.CANCELADA:
            motivo = (dados.get('motivo') or '').strip()
            if not motivo:
                raise DomainError('Informe o motivo do cancelamento.')
            op.observacao = f'{op.observacao}\nCancelada: {motivo}'.strip()

        elif destino == S.PRODUZIDA:
            raise DomainError(
                'Para encerrar como produzida, informe a quantidade — use o '
                'encerramento, não a mudança de situação.'
            )

        if destino == S.QUALIDADE and op.situacao == S.EM_PRODUCAO:
            op.liberada_qualidade_em = None

        op.situacao = destino
        op.ordem.status = OrdemPolpa.STATUS_DO_ERP[destino]
        op.ordem.save()
        op.save()
        return op

    @classmethod
    def liberar(cls, op: OrdemPolpa, usuario=None) -> dict:
        """
        Libera a OP e devolve a necessidade calculada.

        NÃO TRAVA POR FALTA. A fruta chega durante o dia; liberar de manhã
        com a manga ainda no caminhão é o normal desta indústria. O que o
        sistema faz é mostrar o que falta -- e a trava de verdade está no
        encerramento, onde o estoque é consumido de fato.
        """
        cls.mover(op, S.LIBERADA, usuario)
        return cls.necessidade(op)

    # ── Encerramento ─────────────────────────────────────────────────────

    @classmethod
    @transaction.atomic
    def concluir(
        cls, op: OrdemPolpa, usuario, quantidade: Decimal,
        peso_saida: Decimal | None = None, numero_lote: str = '',
    ) -> OrdemPolpa:
        """
        Fecha a produção: consome insumo, cria o lote e dá a validade.

        O CONSUMO E O LOTE SÃO DO SERVIÇO DO ERP -- é ele que baixa por FEFO,
        dá entrada no estoque e calcula custo e rendimento. O que o vertical
        acrescenta é a VALIDADE, que sai do prazo do produto: o ERP aceita a
        data mas não sabe calculá-la, e sem isso ela era digitada à mão a
        cada produção.
        """
        from apps.producao.services.op_service import OrdemProducaoService

        if op.situacao not in (S.EM_PRODUCAO, S.QUALIDADE):
            raise DomainError(
                'Só uma ordem em produção ou em qualidade pode ser concluída.'
            )
        if quantidade is None or quantidade <= ZERO:
            raise DomainError('Informe a quantidade produzida.')

        validade = cls.validade_do_lote(op)

        # A OP do ERP precisa estar "em produção" para o serviço dele
        # aceitar o encerramento -- em qualidade, para o ERP, ela está.
        op.ordem.status = OrdemProducao.Status.EM_PRODUCAO
        op.ordem.save(update_fields=['status'])

        try:
            OrdemProducaoService.encerrar(
                op.ordem, usuario,
                quantidade_produzida=quantidade,
                peso_saida=peso_saida,
                numero_lote_gerado=numero_lote,
                data_validade=validade,
            )
        except Exception as erro:  # noqa: BLE001 — vira mensagem de negócio
            raise DomainError(str(erro)) from erro

        op.ordem.refresh_from_db()
        op.situacao = S.PRODUZIDA
        op.liberada_qualidade_em = timezone.now()
        op.save(update_fields=['situacao', 'liberada_qualidade_em', 'updated_at'])
        return op

    @staticmethod
    def validade_do_lote(op: OrdemPolpa):
        """
        A validade do lote desta OP, pelo prazo do produto.

        `None` quando o produto não tem prazo cadastrado: uma data inventada
        aqui viraria etiqueta impressa, e ninguém saberia que foi inventada.
        A ficha do produto avisa dessa falta desde o cadastro.
        """
        ficha = getattr(op.produto, 'ficha_polpa', None)
        if ficha is None:
            return None
        return ficha.validade_a_partir_de(timezone.localdate())

    # ── Leitura ──────────────────────────────────────────────────────────

    @staticmethod
    def fila(filial, filtros: dict | None = None):
        filtros = filtros or {}
        qs = (
            OrdemPolpa.objects.for_filial(filial)
            .select_related(
                'ordem', 'ordem__produto_acabado', 'ordem__lote_gerado',
                'receita', 'receita__ficha', 'responsavel',
            )
        )
        if filtros.get('situacao'):
            qs = qs.filter(situacao=filtros['situacao'])
        if filtros.get('busca'):
            from django.db.models import Q
            termo = filtros['busca']
            qs = qs.filter(
                Q(ordem__numero__icontains=termo)
                | Q(ordem__produto_acabado__descricao__icontains=termo)
            )
        if filtros.get('abertas'):
            qs = qs.filter(situacao__in=OrdemPolpa.ABERTAS)
        return qs

    @staticmethod
    def painel(filial) -> dict:
        """Quantas ordens em cada situação — o quadro do dia."""
        ordens = list(OrdemPolpa.objects.for_filial(filial).select_related('ordem'))
        return {
            'total': len(ordens),
            'por_situacao': {
                valor: sum(1 for o in ordens if o.situacao == valor)
                for valor, _rotulo in OrdemPolpa.Situacao.choices
            },
            'paradas': sum(1 for o in ordens if o.situacao == S.PAUSADA),
            'atrasadas': sum(1 for o in ordens if o.atrasada),
        }
