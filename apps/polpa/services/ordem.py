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

import logging
from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.core.services.exceptions import DomainError
from apps.estoque.models import Estoque
from apps.polpa.models import FichaProduto, OrdemPolpa, Receita, ReservaInsumo
from apps.producao.models import FichaTecnica, OrdemProducao

logger = logging.getLogger(__name__)

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

        # O QUE JÁ ESTÁ SEPARADO, por insumo. Sem esta coluna a tela mostra
        # "em estoque" já descontado da própria reserva desta ordem, e quem
        # olha conclui que o material sumiu -- quando ele está justamente
        # guardado para esta batida.
        reservado: dict[int, Decimal] = defaultdict(lambda: ZERO)
        for reserva in ReservaInsumo.all_objects.filter(
            ordem=op, status=ReservaInsumo.Status.ATIVA,
        ):
            reservado[reserva.produto_id] += reserva.quantidade

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
                'reservado': reservado[produto.pk],
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

    # ── Reserva de matéria-prima ─────────────────────────────────────────

    @classmethod
    @transaction.atomic
    def reservar_insumos(cls, op: OrdemPolpa, usuario=None) -> list:
        """
        Separa o insumo desta ordem. Chamado quando a batida começa.

        LÊ A `necessidade()`, e não a ficha de novo. Ela já sabe aplicar o
        fator da quantidade planejada e a perda prevista de cada linha;
        recalcular aqui daria uma segunda definição da mesma conta, e no dia
        em que as duas divergissem a reserva separaria uma quantidade e a
        produção cobraria outra.

        RESERVA O QUE DÁ, e não tudo ou nada. Faltar o pote não é motivo para
        deixar a fruta solta na câmara -- e o que falta continua aparecendo em
        `necessidade()['faltas']`, que é a tela que existe para isso.

        NÃO RESERVA O QUE NÃO EXISTE. Reserva maior que o saldo é uma promessa
        que a câmara não cumpre, e ela sumiria do disponível de todas as
        outras batidas do dia.

        Idempotente pelo carimbo: despausar não separa tudo de novo.
        """
        from apps.estoque.services.movimentacao_service import MovimentacaoService

        necessidade = cls.necessidade(op)
        linhas = necessidade['ingredientes'] + necessidade['embalagens']

        ja_reservado: dict[int, Decimal] = defaultdict(lambda: ZERO)
        for reserva in ReservaInsumo.all_objects.filter(
            ordem=op, status=ReservaInsumo.Status.ATIVA,
        ):
            ja_reservado[reserva.produto_id] += reserva.quantidade

        criadas = []
        for linha in linhas:
            produto = linha['produto']
            falta = linha['necessario'] - ja_reservado[produto.pk]
            if falta <= ZERO:
                continue

            fatia = min(falta, cls._saldo(produto, op.filial))
            if fatia <= ZERO:
                continue

            MovimentacaoService.reservar_estoque(
                produto_id=produto.pk,
                filial_id=op.filial_id,
                quantidade=fatia,
            )
            criadas.append(ReservaInsumo.objects.create(
                filial=op.filial, ordem=op, produto=produto,
                quantidade=fatia, criado_por=usuario,
                observacao='Separado no início da batida.',
            ))
            ja_reservado[produto.pk] += fatia

        return criadas

    @classmethod
    def reservar_ao_iniciar(cls, op: OrdemPolpa, usuario=None) -> list:
        """
        A separação do início da batida, com carimbo e sem poder estourar.

        NÃO PROPAGA ERRO. Isto roda quando alguém põe a ordem em produção: um
        problema de estoque não pode impedir a fábrica de registrar que a
        linha ligou. A batida vai acontecer de qualquer jeito -- o que se
        perde ao travar é o registro dela, e é o registro que explica o dia
        depois.

        O carimbo só é posto quando a separação rodou até o fim. Falhou, fica
        sem carimbo, e a próxima entrada em produção tenta de novo.
        """
        if op.insumos_reservados_em:
            return []
        try:
            criadas = cls.reservar_insumos(op, usuario)
        except Exception:  # noqa: BLE001
            logger.exception(
                'Falha ao reservar insumo da ordem de polpa %s', op.pk,
            )
            return []

        op.insumos_reservados_em = timezone.now()
        op.save(update_fields=['insumos_reservados_em', 'updated_at'])
        return criadas

    @staticmethod
    def liberar_reservas(op: OrdemPolpa, status: str) -> int:
        """
        Desfaz as reservas ativas desta ordem. Devolve quantas.

        CHAMADA NA CONCLUSÃO E NO CANCELAMENTO, por razões diferentes:

          · CONCLUIU -- `OrdemProducaoService.encerrar` acabou de baixar o
            insumo de verdade, por FEFO. Se a reserva continuasse de pé, o
            mesmo material estaria reservado E consumido, e o disponível
            ficaria negativo sem nada errado ter acontecido. Não é limpeza:
            é a outra metade da conta;

          · CANCELOU -- a fruta volta a ser de todo mundo. Reserva de uma
            batida que não vai acontecer some do disponível para sempre, e
            ninguém desconfia de um número que só encolhe.

        `status` diz qual das duas foi, porque a diferença importa seis meses
        depois: consumida virou produto, cancelada voltou para a câmara.
        """
        from apps.estoque.services.movimentacao_service import MovimentacaoService

        reservas = list(ReservaInsumo.all_objects.filter(
            ordem=op, status=ReservaInsumo.Status.ATIVA,
        ))
        for reserva in reservas:
            MovimentacaoService.liberar_reserva(
                produto_id=reserva.produto_id,
                filial_id=reserva.filial_id,
                quantidade=reserva.quantidade,
                tolerar_ausente=True,
            )
            reserva.status = status
            reserva.save(update_fields=['status'])
        return len(reservas)

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
            # A FRUTA VOLTA A SER DE TODO MUNDO. Reserva de uma batida que
            # não vai acontecer some do disponível para sempre, e ninguém
            # desconfia de um número que só encolhe.
            cls.liberar_reservas(op, ReservaInsumo.Status.CANCELADA)

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

        # SEPARAR A MATÉRIA-PRIMA QUANDO A BATIDA COMEÇA. Depois do save, para
        # a ordem já estar em produção quando a reserva é gravada -- e nunca
        # antes, porque reserva feita para uma transição que ainda pode falhar
        # seguraria fruta de uma batida que não começou.
        if destino == S.EM_PRODUCAO:
            cls.reservar_ao_iniciar(op, usuario)

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

        # A RESERVA MORRE ANTES DE O CONSUMO NASCER, e a ordem importa. O
        # `encerrar` abaixo baixa o insumo de verdade; se a reserva ainda
        # estivesse de pé, o mesmo material ficaria reservado E consumido, e o
        # disponível iria a negativo sem nada errado ter acontecido. Liberar
        # depois deixaria essa janela aberta dentro da própria transação.
        cls.liberar_reservas(op, ReservaInsumo.Status.CONSUMIDA)

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

        # O RENDIMENTO DA BATIDA VIRA ALERTA, e não linha de log. É o
        # indicador que a fábrica de fruta cobra todo dia, e até agora o aviso
        # ia para onde ninguém olha.
        cls.avisar_rendimento(op)

        return op

    # ── Alerta de rendimento ─────────────────────────────────────────────

    @classmethod
    def avisar_rendimento(cls, op: OrdemPolpa) -> bool:
        """
        Toca o sino quando a batida rendeu abaixo do piso da receita.

        O AVISO EXISTIA E NÃO CHEGAVA A NINGUÉM. `op_service` já comparava o
        rendimento com um mínimo — mas escrevia `logger.warning`, e log é onde
        ninguém olha. Um indicador que a fábrica cobra todo dia precisa ir para
        onde a fábrica olha.

        O PISO VEM DA RECEITA, não de uma constante. Manga não rende como
        acerola, e um número fixo no código faria metade dos produtos alertar
        sempre e a outra metade nunca.

        É EVENTO, e não condição: o rendimento de uma batida encerrada não muda
        mais. Por isso não entra em varredura nem se desliga sozinho — ele
        conta um fato que aconteceu, e some quando alguém o lê.

        NÃO ESTOURA. Roda dentro do encerramento da ordem: falhar aqui
        impediria fechar uma batida que já aconteceu, e o que se perde ao
        travar é o registro dela.
        """
        from apps.core.models import Notificacao
        from apps.polpa.services.processo import ProcessoService

        try:
            resumo = ProcessoService.resumo(op)
            if not resumo.get('rendimento_abaixo'):
                return False

            real = resumo['rendimento']
            esperado = resumo['rendimento_esperado']
            entrada = resumo.get('entrada')
            saida = resumo.get('saida')
            perda = resumo.get('perda_total')

            detalhe = f'esperado {esperado}%'
            if entrada is not None and saida is not None:
                detalhe = (
                    f'{entrada} entraram, {saida} saíram, '
                    f'{perda} de perda · {detalhe}'
                )

            Notificacao.objects.update_or_create(
                filial=op.filial,
                tipo=Notificacao.Tipo.POLPA_RENDIMENTO_BAIXO,
                referencia_tipo='polpa_ordem',
                referencia_id=str(op.pk),
                defaults={
                    'titulo': f'{op.numero}: rendimento de {real}%',
                    'mensagem': detalhe[:500],
                    'url': reverse('polpa:ordem-detail', args=[op.pk]),
                    'ativa': True,
                },
            )
            return True
        except Exception:  # noqa: BLE001
            logger.exception(
                'Falha ao avisar rendimento da ordem de polpa %s', op.pk,
            )
            return False

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
