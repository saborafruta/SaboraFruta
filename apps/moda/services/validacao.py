"""
As onze validações que precedem a liberação para a produção.

O QUE ESTÁ EM JOGO: depois que a OP desce, a fábrica corta tecido. Um erro
que passa daqui não volta com um `Ctrl+Z` — volta com rolo cortado errado,
faccionista parado e prazo perdido. Por isso EMITIR A ORDEM continua sendo
trava de verdade: é o ato que gasta material.

MOVER O PEDIDO PARA PRODUÇÃO, NÃO. Arrastar o cartão ou trocar o status é
dizer onde o pedido está, e a confecção real chega nesse ponto com coisa
pendente todo dia — a ficha sai depois, o preço fecha depois. Travar o
registro do fato não fazia a pendência sumir: fazia o quadro mentir sobre
onde o pedido estava, e quem precisava andar aprendia a contornar. Agora
passa, e a lista do que falta vai junto, escrita.

O contraste com o painel dos 23 passos continua: lá é mapa, aqui é a lista
do que falta — e o que ela alimenta (aviso ou cancela) é decidido por quem
chama, conforme o estrago do ato.

BLOQUEIO x AVISO. Nem todo problema impede: "materiais disponíveis OU compra
autorizada" é uma condição com dois caminhos, e quem já emitiu o pedido de
compra decidiu produzir contando com a chegada. Esse caso vira AVISO — a
fábrica precisa saber que o tecido não está na prateleira, mas travar seria
inventar uma regra que ninguém pediu.

CADA PROBLEMA DIZ ONDE RESOLVER. Uma trava que só diz "não pode" transfere o
trabalho de descobrir o porquê para quem já está com pressa — e é assim que
alguém acha um jeito de contornar o sistema.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from apps.core.services.exceptions import DomainError

ZERO = Decimal('0')

OK = 'ok'
BLOQUEIO = 'bloqueio'
AVISO = 'aviso'


@dataclass
class Checagem:
    chave: str
    label: str
    situacao: str = OK
    motivo: str = ''
    onde_resolver: str = ''
    # Itens do pedido que falharam, quando o problema é por produto.
    detalhes: list[str] = field(default_factory=list)

    @property
    def bloqueia(self) -> bool:
        return self.situacao == BLOQUEIO


class ValidacaoProducao:

    # ── Entrada ──────────────────────────────────────────────────────────

    @classmethod
    def checar(cls, pedido) -> list[Checagem]:
        """
        As onze, na ordem em que a confecção as resolve.

        Todas rodam SEMPRE, mesmo depois da primeira falhar: quem está
        liberando quer a lista inteira do que falta, não descobrir um
        problema por vez a cada tentativa.
        """
        itens = list(pedido.itens.select_related('produto').all())
        return [
            cls._cliente(pedido),
            cls._aprovacao(pedido),
            cls._grade(itens),
            cls._quantidade(pedido, itens),
            cls._arte(itens),
            cls._ficha(itens),
            cls._roteiro(itens),
            cls._materiais(pedido, itens),
            cls._entrega(pedido),
            cls._valores(pedido, itens),
            cls._pagamento(pedido),
        ]

    @classmethod
    def resumo(cls, pedido) -> dict:
        checagens = cls.checar(pedido)
        bloqueios = [c for c in checagens if c.bloqueia]
        return {
            'checagens': checagens,
            'bloqueios': bloqueios,
            'avisos': [c for c in checagens if c.situacao == AVISO],
            'liberado': not bloqueios,
        }

    @classmethod
    def exigir(cls, pedido) -> None:
        """
        Trava a liberação, com o motivo por extenso.

        A mensagem lista TODOS os impedimentos numa linha só porque é o que
        cabe num `messages.error` — a tela do pedido mostra a lista completa,
        com onde resolver cada um.
        """
        bloqueios = [c for c in cls.checar(pedido) if c.bloqueia]
        if not bloqueios:
            return

        motivos = ' '.join(f'{c.label}: {c.motivo}' for c in bloqueios)
        raise DomainError(
            f'Não dá para liberar a produção — {len(bloqueios)} '
            f'pendência(s). {motivos}'
        )

    @classmethod
    def pendencias(cls, pedido) -> list[str]:
        """
        O que falta, em frases prontas — sem travar nada.

        MESMA FONTE DE `exigir`. Quem só avisa e quem barra leem a mesma
        checagem: uma segunda lista "das pendências brandas" divergiria da
        primeira no dia em que alguém acrescentasse uma validação, e as duas
        telas passariam a cobrar coisas diferentes do mesmo pedido.
        """
        return [
            f'{c.label}: {c.motivo}'
            for c in cls.checar(pedido) if c.bloqueia
        ]

    # ── 1. Cliente ───────────────────────────────────────────────────────

    @staticmethod
    def _cliente(pedido) -> Checagem:
        """
        Cliente cadastrado E faturável.

        Checar só a existência seria teatro: a chave estrangeira já garante
        que há um cliente. O que trava de verdade lá na frente é cliente
        inativo ou sem documento — o financeiro não emite a conta, e aí a
        peça já está pronta.
        """
        cliente = pedido.cliente
        if not getattr(cliente, 'ativo', True):
            return Checagem(
                'cliente', 'Cliente cadastrado', BLOQUEIO,
                f'{cliente} está inativo no cadastro.',
                'Cadastros › Clientes',
            )
        if not (getattr(cliente, 'cpf_cnpj', '') or '').strip():
            return Checagem(
                'cliente', 'Cliente cadastrado', BLOQUEIO,
                f'{cliente} está sem CPF/CNPJ, e o financeiro não fecha sem ele.',
                'Cadastros › Clientes',
            )
        return Checagem('cliente', 'Cliente cadastrado', OK)

    # ── 2. Pedido aprovado ───────────────────────────────────────────────

    @staticmethod
    def _aprovacao(pedido) -> Checagem:
        aprovacao = getattr(pedido, 'aprovacao', None)

        if aprovacao is None or not aprovacao.liberado:
            return Checagem(
                'aprovacao', 'Pedido aprovado', BLOQUEIO,
                'O pedido ainda não foi liberado internamente.',
                'Pedido › Aprovação',
            )
        if aprovacao.pediu_ajuste:
            return Checagem(
                'aprovacao', 'Pedido aprovado', BLOQUEIO,
                'O cliente pediu ajuste e ainda não aprovou a versão nova.',
                'Pedido › Aprovação',
            )
        if not aprovacao.aprovado_pelo_cliente:
            # Cortar tecido sem o aceite é o erro que faz refazer o lote.
            return Checagem(
                'aprovacao', 'Pedido aprovado', BLOQUEIO,
                'O cliente ainda não aprovou pelo link.',
                'Pedido › Aprovação',
            )
        return Checagem('aprovacao', 'Pedido aprovado', OK)

    # ── 3. Grade ─────────────────────────────────────────────────────────

    @staticmethod
    def _grade(itens) -> Checagem:
        if not itens:
            return Checagem(
                'grade', 'Grade preenchida', BLOQUEIO,
                'O pedido não tem produtos.', 'Pedido › Produtos',
            )
        sem = [i.nome_exibicao for i in itens if not i.grade.exists()]
        if sem:
            return Checagem(
                'grade', 'Grade preenchida', BLOQUEIO,
                f'{len(sem)} produto(s) sem grade — o corte não sabe quantas '
                f'peças de cada tamanho.',
                'Pedido › Grade', sem,
            )
        return Checagem('grade', 'Grade preenchida', OK)

    # ── 4. Quantidade ────────────────────────────────────────────────────

    @staticmethod
    def _quantidade(pedido, itens) -> Checagem:
        """
        A soma da grade tem de bater com a quantidade do item.

        A tela deriva a quantidade da grade, então a divergência só aparece
        em item importado ou editado por fora — e é justamente aí que ela
        passa despercebida.
        """
        divergentes = []
        for item in itens:
            grade = list(item.grade.all())
            if not grade:
                continue
            soma = sum(g.quantidade for g in grade)
            if soma != item.quantidade:
                divergentes.append(
                    f'{item.nome_exibicao}: grade soma {soma}, '
                    f'item diz {item.quantidade}'
                )
        if divergentes:
            return Checagem(
                'quantidade', 'Quantidade total correta', BLOQUEIO,
                'A soma da grade não bate com a quantidade do produto.',
                'Pedido › Grade', divergentes,
            )
        if not pedido.quantidade_total:
            return Checagem(
                'quantidade', 'Quantidade total correta', BLOQUEIO,
                'O pedido está com quantidade total zero.',
                'Pedido › Grade',
            )
        return Checagem('quantidade', 'Quantidade total correta', OK)

    # ── 5. Arte ──────────────────────────────────────────────────────────

    @staticmethod
    def _arte(itens) -> Checagem:
        """
        "Quando necessária" é a parte que importa.

        Peça lisa não tem arte e não pode travar por isso. O que trava é a
        peça que DECLARA personalização e não tem arquivo nem visual: a
        técnica está definida, e a fábrica não tem o que aplicar.
        """
        sem_arquivo = []
        for item in itens:
            personalizacoes = list(item.personalizacoes.all())
            if not personalizacoes:
                continue
            tem_arquivo = any(p.arquivo for p in personalizacoes)
            tem_visual = any(v.tem_imagem for v in item.visuais.all())
            if not tem_arquivo and not tem_visual:
                sem_arquivo.append(item.nome_exibicao)

        if sem_arquivo:
            return Checagem(
                'arte', 'Arte aprovada, quando necessária', BLOQUEIO,
                f'{len(sem_arquivo)} produto(s) têm personalização declarada '
                f'mas nenhum arquivo ou visual anexado.',
                'Pedido › Arte', sem_arquivo,
            )
        return Checagem('arte', 'Arte aprovada, quando necessária', OK)

    # ── 6 e 7. Ficha e roteiro ───────────────────────────────────────────

    @staticmethod
    def _ficha(itens) -> Checagem:
        sem = []
        for item in itens:
            produto = item.produto
            if produto is None:
                sem.append(f'{item.nome_exibicao} (sem produto de catálogo)')
                continue
            ficha = getattr(produto, 'ficha', None)
            if ficha is None:
                sem.append(item.nome_exibicao)
            elif not ficha.materiais.exists():
                sem.append(f'{item.nome_exibicao} (ficha sem materiais)')

        if sem:
            return Checagem(
                'ficha', 'Ficha técnica definida', BLOQUEIO,
                'Sem ficha não há material a separar nem custo a apurar.',
                'Engenharia › Ficha Técnica', sem,
            )
        return Checagem('ficha', 'Ficha técnica definida', OK)

    @staticmethod
    def _roteiro(itens) -> Checagem:
        sem = []
        for item in itens:
            produto = item.produto
            if produto is None:
                sem.append(f'{item.nome_exibicao} (sem produto de catálogo)')
                continue
            roteiro = getattr(produto, 'roteiro', None)
            if roteiro is None:
                sem.append(item.nome_exibicao)
            elif not roteiro.etapas.exists():
                sem.append(f'{item.nome_exibicao} (roteiro sem operações)')

        if sem:
            return Checagem(
                'roteiro', 'Roteiro de produção definido', BLOQUEIO,
                'Sem roteiro a peça entra na fábrica sem sequência de '
                'operações, e o PCP não consegue calcular carga.',
                'Engenharia › Sequência de Produção', sem,
            )
        return Checagem('roteiro', 'Roteiro de produção definido', OK)

    # ── 8. Materiais ─────────────────────────────────────────────────────

    @classmethod
    def _materiais(cls, pedido, itens) -> Checagem:
        """
        Disponível OU compra autorizada — a condição tem dois caminhos.

        Quem já emitiu o pedido de compra decidiu produzir contando com a
        chegada, e travar isso seria inventar uma regra que ninguém pediu.
        Vira AVISO: a fábrica precisa saber que o tecido não está na
        prateleira, mas a decisão já foi tomada.

        O consumo sai de `consumo_bruto` da ficha — a MESMA propriedade que
        o painel de necessidade usa. A fórmula não é reescrita aqui.
        """
        from apps.estoque.models.estoque import Estoque

        faltas, em_falta = cls._faltas(pedido, itens, Estoque)
        if not faltas:
            return Checagem('materiais', 'Materiais disponíveis', OK)

        if cls._compra_autorizada(pedido, em_falta):
            return Checagem(
                'materiais', 'Materiais disponíveis ou compra autorizada', AVISO,
                'Falta material, mas há pedido de compra emitido.',
                'PCP › Necessidade de Materiais', faltas,
            )
        return Checagem(
            'materiais', 'Materiais disponíveis ou compra autorizada', BLOQUEIO,
            'Falta material e não há compra autorizada.',
            'PCP › Necessidade de Materiais', faltas,
        )

    @staticmethod
    def _faltas(pedido, itens, Estoque) -> tuple[list[str], set[int]]:
        """
        Materiais da ficha cujo saldo não cobre o que o pedido precisa.

        Devolve a lista legível E os ids dos produtos em falta: são estes,
        e não "qualquer requisição da filial", que a compra precisa cobrir.
        """
        precisa: dict[int, list] = {}
        for item in itens:
            produto = item.produto
            ficha = getattr(produto, 'ficha', None) if produto else None
            if ficha is None:
                continue
            for material in ficha.materiais.all():
                if not material.produto_estoque_id:
                    # Material sem vínculo não é falta: é cadastro
                    # incompleto, e a checagem da ficha já cobra isso.
                    continue
                chave = material.produto_estoque_id
                atual = precisa.setdefault(chave, [material, ZERO])
                atual[1] += material.consumo_bruto * item.quantidade

        if not precisa:
            return [], set()

        saldos = {
            e.produto_id: e.quantidade_atual
            for e in Estoque.objects.filter(
                produto_id__in=list(precisa), filial=pedido.filial,
            )
        }

        faltas = []
        em_falta = set()
        for produto_id, (material, quantidade) in precisa.items():
            saldo = saldos.get(produto_id, ZERO)
            if saldo >= quantidade:
                continue
            em_falta.add(produto_id)
            faltas.append(
                f'{material.descricao}: precisa {quantidade:.2f}, '
                f'tem {saldo:.2f}'.replace('.', ',')
            )
        return faltas, em_falta

    @staticmethod
    def _compra_autorizada(pedido, em_falta: set[int]) -> bool:
        """
        Existe pedido de compra emitido para os materiais QUE FALTAM.

        A checagem é pelos produtos em falta, e não por "alguma requisição da
        filial": uma compra de linha branca não autoriza produzir sem o
        tecido. Essa distinção é a diferença entre a regra e a aparência
        dela.
        """
        from apps.moda.models import RequisicaoMaterial

        if not em_falta:
            return False

        cobertos = set(
            RequisicaoMaterial.objects
            .for_filial(pedido.filial)
            .filter(pedido_compra__isnull=False,
                    itens__produto_id__in=em_falta)
            .exclude(status=RequisicaoMaterial.Status.CANCELADA)
            .values_list('itens__produto_id', flat=True)
        )
        # Todo material em falta precisa estar comprado. Cobrir metade e
        # liberar deixaria a fábrica parar no que sobrou.
        return em_falta <= cobertos

    # ── 9, 10 e 11. Prazo, valores e pagamento ───────────────────────────

    @staticmethod
    def _entrega(pedido) -> Checagem:
        if not pedido.data_prevista_entrega:
            return Checagem(
                'entrega', 'Data de entrega definida', BLOQUEIO,
                'Sem data combinada o PCP não consegue priorizar nem '
                'programar a carga.',
                'Pedido › Dados',
            )
        return Checagem('entrega', 'Data de entrega definida', OK)

    @staticmethod
    def _valores(pedido, itens) -> Checagem:
        sem_preco = [i.nome_exibicao for i in itens if i.valor_unitario is None]
        if sem_preco:
            return Checagem(
                'valores', 'Valores definidos', BLOQUEIO,
                f'{len(sem_preco)} produto(s) sem valor unitário — sem preço '
                f'não há faturamento nem margem.',
                'Pedido › Valores', sem_preco,
            )
        if pedido.valor_total < 0:
            return Checagem(
                'valores', 'Valores definidos', BLOQUEIO,
                'O valor total do pedido não pode ser negativo.',
                'Pedido › Valores',
            )
        return Checagem('valores', 'Valores definidos', OK)

    @staticmethod
    def _pagamento(pedido) -> Checagem:
        faltando = []
        if not pedido.forma_pagamento_id:
            faltando.append('forma de pagamento')
        if not pedido.condicao_pagamento_id:
            faltando.append('condição de pagamento')
        if faltando:
            return Checagem(
                'pagamento', 'Condição de pagamento definida', BLOQUEIO,
                f'Falta {" e ".join(faltando)} — sem isso o financeiro não '
                f'gera as contas a receber.',
                'Pedido › Valores',
            )
        return Checagem('pagamento', 'Condição de pagamento definida', OK)
