"""
O carregamento: conferir a carga e medir o baú antes de fechar a porta.

O DOCUMENTO DA CARGA É O DO ERP. `logistica.RomaneioCarga` já tem
motorista, placa, transportadora, as paradas e o peso; um "carregamento"
próprio deste vertical daria dois documentos para o mesmo caminhão, e a
entrega ficaria com dois donos. Aqui mora só a ficha fria
(`CargaFria`), com o dado que não vale para todo mundo: quem entrega
parafuso não mede o baú.

A TEMPERATURA EXIGIDA NÃO É DIGITADA, É DEDUZIDA. Cada produto acabado tem
a sua no cadastro (seção 1); a carga inteira responde pela MAIS EXIGENTE
que está dentro dela -- um baú a -12°C serve para o creme e estraga a
polpa, e pedir para o conferente lembrar disso é pedir para errar num dia
corrido.

CONFERIR NÃO É DESPACHAR. Marcar uma parada como carregada é dizer "esta
já subiu"; despachar é dizer "a porta fechou e o caminhão saiu". Um botão
só para as duas coisas transformaria uma conferência interrompida em
caminhão despachado pela metade.

O QUE FALTA AVISA, NÃO TRAVA -- menos a medição. Parada que ainda não subiu
vira aviso, porque quem está na doca sabe o que está fazendo e o software
não vai segurar um caminhão. A temperatura é a exceção: ela é a última
prova da cadeia de frio, some para sempre depois que o caminhão sai, e
custa dez segundos de termômetro. Por isso ela é pedida no mesmo formulário
do despacho -- não é uma trava, é a pergunta feita na hora em que ela ainda
é barata.
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.services.exceptions import DomainError
from apps.logistica.models import ItemRomaneioCarga, RomaneioCarga
from apps.polpa.models import CargaFria
from apps.vendas.models.separacao import SeparacaoPedido

# A carga que ainda está na doca. `EM_ROTA` fica de fora: depois que o
# caminhão sai, o trabalho é da entrega, não do carregamento.
ZERO = Decimal('0')

# Os mesmos estados que a separacao considera abertos -- a carga e' o passo
# seguinte dela, e duas definicoes de "pedido em aberto" divergiriam.
from apps.polpa.services.separacao import ABERTOS  # noqa: E402

NA_DOCA = (
    RomaneioCarga.Status.RASCUNHO,
    RomaneioCarga.Status.EM_CARREGAMENTO,
)


class CarregamentoService:

    # ── Leitura ──────────────────────────────────────────────────────────

    @staticmethod
    def ficha(romaneio) -> CargaFria:
        """A ficha fria do romaneio — criada na primeira vez que se olha."""
        ficha, _ = CargaFria.objects.get_or_create(
            romaneio=romaneio, defaults={'filial': romaneio.filial},
        )
        return ficha

    @staticmethod
    def temperatura_exigida(romaneio) -> Decimal | None:
        """
        A mais exigente entre os produtos que estão na carga.

        `None` quando nenhum produto declara temperatura: é "ninguém
        cadastrou", e a tela diz isso em vez de aprovar qualquer número.
        """
        pedidos = [
            i.pedido_venda_id for i in romaneio.itens.all() if i.pedido_venda_id
        ]
        if not pedidos:
            return None

        from apps.vendas.models import ItemPedidoVenda

        temperaturas = [
            t for t in ItemPedidoVenda.objects
            .filter(pedido_id__in=pedidos)
            .values_list('produto__temperatura_maxima', flat=True)
            if t is not None
        ]
        return min(temperaturas) if temperaturas else None

    @classmethod
    def cargas(cls, filial, filtros: dict | None = None) -> list[dict]:
        """As cargas na doca, e as que saíram hoje."""
        filtros = filtros or {}
        hoje = timezone.localdate()

        qs = (
            RomaneioCarga.objects.filter(filial=filial)
            .select_related('transportadora')
            .prefetch_related('itens')
        )
        if filtros.get('busca'):
            from django.db.models import Q
            termo = filtros['busca']
            qs = qs.filter(
                Q(motorista_nome__icontains=termo)
                | Q(veiculo_placa__icontains=termo)
                | Q(destino_rota__icontains=termo)
            )

        qs = qs.filter(status__in=(*NA_DOCA, RomaneioCarga.Status.EM_ROTA))

        linhas = []
        for romaneio in qs:
            if romaneio.status == RomaneioCarga.Status.EM_ROTA:
                ficha = getattr(romaneio, 'ficha_polpa', None)
                saiu = ficha.saida_em if ficha else None
                # Carga em rota só interessa aqui no dia em que saiu — depois
                # ela é assunto da entrega.
                if not saiu or timezone.localtime(saiu).date() != hoje:
                    continue
            else:
                ficha = getattr(romaneio, 'ficha_polpa', None)

            paradas = list(romaneio.itens.all())
            carregadas = [
                p for p in paradas
                if p.status_entrega != ItemRomaneioCarga.StatusEntrega.PENDENTE
            ]
            linhas.append({
                'romaneio': romaneio,
                'ficha': ficha,
                'paradas': len(paradas),
                'carregadas': len(carregadas),
                'temperatura': ficha.temperatura_bau if ficha else None,
                'sem_medicao': not (ficha and ficha.medida),
                'na_doca': romaneio.status in NA_DOCA,
            })

        linhas.sort(key=lambda l: (not l['na_doca'], l['romaneio'].numero))
        return linhas

    @classmethod
    def carga(cls, romaneio) -> dict:
        """A conferência de uma carga: cada parada com o que ela leva."""
        ficha = cls.ficha(romaneio)
        exigida = cls.temperatura_exigida(romaneio)
        temperatura = ficha.temperatura_bau

        paradas = []
        for item in romaneio.itens.select_related('pedido_venda').all():
            pedido = item.pedido_venda
            separacao = None
            if pedido:
                separacao = (
                    pedido.separacoes
                    .filter(status=SeparacaoPedido.Status.CONCLUIDA)
                    .order_by('-data_inicio').first()
                )
            paradas.append({
                'item': item,
                'pedido': pedido,
                'separacao': separacao,
                # SEM SEPARAÇÃO É CARGA SEM LOTE IDENTIFICADO: sobe no
                # caminhão e, num recall, ninguém sabe o que foi para quem.
                'sem_separacao': bool(pedido and not separacao),
                'carregada': (
                    item.status_entrega
                    != ItemRomaneioCarga.StatusEntrega.PENDENTE
                ),
            })

        return {
            'romaneio': romaneio,
            'ficha': ficha,
            'paradas': paradas,
            'exigida': exigida,
            'temperatura': temperatura,
            'fora_da_faixa': bool(
                temperatura is not None and exigida is not None
                and temperatura > exigida
            ),
            'pendencias': cls.pendencias(romaneio, paradas, ficha),
            'na_doca': romaneio.status in NA_DOCA,
        }

    @staticmethod
    def pendencias(romaneio, paradas, ficha) -> list[str]:
        """
        O que falta para esta carga poder sair — em texto, não em trava.

        Quem está na doca às 4h sabe o que está fazendo, e software que
        segura caminhão é software que a fábrica aprende a contornar. O que
        falta aparece; a decisão continua de quem carrega.
        """
        faltando = []
        pendentes = [p for p in paradas if not p['carregada']]
        if pendentes:
            faltando.append(
                f'{len(pendentes)} parada(s) ainda não conferida(s) como '
                'carregada(s).'
            )
        sem_separacao = [p for p in paradas if p['sem_separacao']]
        if sem_separacao:
            faltando.append(
                f'{len(sem_separacao)} pedido(s) sem separação fechada — a '
                'carga sai sem lote identificado.'
            )
        if not romaneio.veiculo_placa:
            faltando.append('Romaneio sem placa do veículo.')
        if not ficha.medida:
            faltando.append('Baú ainda não medido.')
        return faltando

    # ── Ação ─────────────────────────────────────────────────────────────

    @classmethod
    def registrar_temperatura(cls, romaneio, temperatura, usuario=None):
        """Grava a medição do baú, com quem mediu e quando."""
        if temperatura is None:
            raise DomainError('Informe a temperatura medida no baú.')

        ficha = cls.ficha(romaneio)
        ficha.temperatura_bau = temperatura
        ficha.medida_em = timezone.now()
        ficha.medido_por = usuario
        ficha.save(update_fields=[
            'temperatura_bau', 'medida_em', 'medido_por', 'updated_at',
        ])
        return ficha

    @staticmethod
    def conferir(item, carregada: bool = True):
        """
        Marca uma parada como carregada — ou volta atrás.

        VOLTAR ATRÁS PRECISA EXISTIR: conferência é feita com a mão na
        caixa, e marcar a parada errada é o erro mais comum da doca. Sem o
        caminho de volta, a correção vira um romaneio novo.
        """
        item.status_entrega = (
            ItemRomaneioCarga.StatusEntrega.CARREGADO if carregada
            else ItemRomaneioCarga.StatusEntrega.PENDENTE
        )
        item.save(update_fields=['status_entrega', 'updated_at'])
        return item

    @classmethod
    @transaction.atomic
    def despachar(cls, romaneio, temperatura, usuario=None):
        """
        Fecha a porta: grava a medição, marca a hora e põe o romaneio em rota.

        A MEDIÇÃO VEM JUNTO porque depois não vem mais. É a última
        temperatura conhecida antes de o produto sumir de vista, e a próxima
        é a do cliente — que já não pode ser corrigida.
        """
        if romaneio.status not in NA_DOCA:
            raise DomainError(
                f'Esta carga não está na doca (situação: '
                f'{romaneio.get_status_display()}).'
            )

        ficha = cls.registrar_temperatura(romaneio, temperatura, usuario)
        ficha.saida_em = timezone.now()
        ficha.save(update_fields=['saida_em', 'updated_at'])

        romaneio.status = RomaneioCarga.Status.EM_ROTA
        romaneio.save(update_fields=['status', 'updated_at'])
        return ficha
    # ── Montar a carga a partir das vendas ───────────────────────────────

    @staticmethod
    def _acabados(filial) -> set[int]:
        """Os produtos que este vertical fabrica — o resto não sai da câmara."""
        from apps.polpa.models import FichaProduto

        return set(
            FichaProduto.objects.for_filial(filial)
            .filter(classe=FichaProduto.Classe.ACABADO)
            .values_list('produto_id', flat=True)
        )

    @classmethod
    def vendas_para_carregar(cls, filial, filtros: dict | None = None) -> list[dict]:
        """
        As vendas que ainda esperam caminhão.

        A CARGA COMEÇAVA NA LOGÍSTICA, e a doca ficava vazia até alguém montar
        o romaneio lá. Quem está na doca com o caminhão encostado não vai a
        outro módulo pedir que montem a carga — ele carrega e anota depois, que
        é como a expedição deixa de ter registro.

        VENDA JÁ EM CARGA NÃO APARECE. É a regra que impede a mesma mercadoria
        de ser carregada em dois caminhões: basta ela estar num romaneio que
        não foi cancelado. Cancelado volta para a lista, porque aquela carga
        deixou de existir.

        SÓ O QUE A FÁBRICA PRODUZ. Pedido de item que este vertical não fabrica
        não sai desta câmara, e enfileirá-lo aqui faria a doca prometer o que
        ela não tem.
        """
        from django.db.models import Q

        from apps.vendas.models.pedido import PedidoVenda

        filtros = filtros or {}
        acabados = cls._acabados(filial)
        if not acabados:
            return []

        ja_em_carga = set(
            ItemRomaneioCarga.objects
            .filter(
                pedido_venda__isnull=False,
                romaneio__filial=filial,
            )
            .exclude(romaneio__status=RomaneioCarga.Status.CANCELADO)
            .values_list('pedido_venda_id', flat=True)
        )

        qs = (
            PedidoVenda.objects.filter(filial=filial, status__in=ABERTOS)
            .filter(itens__produto_id__in=acabados)
            .exclude(pk__in=ja_em_carga)
            .select_related('cliente')
            .prefetch_related('itens__produto')
            .distinct()
        )
        if filtros.get('busca_venda'):
            termo = filtros['busca_venda']
            qs = qs.filter(
                Q(numero_pedido__icontains=termo)
                | Q(cliente__razao_social__icontains=termo)
                | Q(cliente__nome_fantasia__icontains=termo)
            )

        linhas = []
        for pedido in qs:
            itens = [i for i in pedido.itens.all() if i.produto_id in acabados]
            linhas.append({
                'pedido': pedido,
                'itens': len(itens),
                'volumes': sum((i.quantidade or ZERO for i in itens), ZERO),
                'valor': pedido.valor_total or ZERO,
                'entrega': pedido.data_entrega_prevista,
            })
        # ORDENADO PELA ENTREGA, como a separação: quem carrega trabalha contra
        # a data em que o caminhão precisa sair. Sem data vai para o fim e não
        # some -- sumir é como o pedido atrasa.
        # `None` nao compara com `date`; a queda para hoje e' a mesma que a
        # separacao ja' usa -- o primeiro termo do par e' quem manda o
        # pedido sem data para o fim.
        linhas.sort(key=lambda l: (
            l['entrega'] is None, l['entrega'] or timezone.localdate(),
        ))
        return linhas

    @classmethod
    @transaction.atomic
    def montar_carga(cls, filial, pedidos, dados: dict, usuario=None) -> RomaneioCarga:
        """
        Cria o romaneio de carga a partir das vendas escolhidas.

        UM CAMINHÃO LEVA VÁRIOS PEDIDOS, e é por isso que a carga existe como
        documento próprio em vez de o carregamento ser por pedido: a
        temperatura do baú, o motorista e a placa são da viagem, não de cada
        cliente.

        O NÚMERO É GERADO AQUI. Na Logística ele é digitado, e faz sentido lá --
        quem monta a carga no escritório tem a numeração à mão. Na doca, pedir
        um número inventado é atrito puro, e número repetido bate na
        `unique_together` depois de a pessoa já ter escolhido tudo.
        """
        pedidos = list(pedidos)
        if not pedidos:
            raise DomainError(
                'Escolha ao menos uma venda para montar a carga.'
            )

        motorista = (dados.get('motorista_nome') or '').strip()
        placa = (dados.get('veiculo_placa') or '').strip().upper()
        if not motorista and not placa:
            raise DomainError(
                'Informe o motorista ou a placa — sem um dos dois a carga sai '
                'sem dizer quem levou.'
            )

        ultimo = (
            RomaneioCarga.objects.filter(filial=filial)
            .order_by('-numero')
            .values_list('numero', flat=True)
            .first()
        )
        romaneio = RomaneioCarga.objects.create(
            filial=filial,
            numero=(ultimo or 0) + 1,
            data=timezone.localdate(),
            status=RomaneioCarga.Status.EM_CARREGAMENTO,
            responsavel=usuario,
            motorista_nome=motorista,
            veiculo_placa=placa,
            destino_rota=(dados.get('destino_rota') or '').strip(),
        )

        peso = ZERO
        valor = ZERO
        for ordem, pedido in enumerate(pedidos, start=1):
            cliente = pedido.cliente
            ItemRomaneioCarga.objects.create(
                romaneio=romaneio,
                pedido_venda=pedido,
                ordem=ordem,
                cliente_nome=str(cliente) if cliente else 'Sem cliente',
                documento=getattr(cliente, 'cpf_cnpj', '') or '',
                # O ENDEREÇO É COPIADO, e não apontado: é para onde o caminhão
                # foi naquele dia. Cliente que muda de endereço depois não pode
                # reescrever o histórico de uma entrega já feita.
                endereco_entrega=cls._endereco(cliente),
                valor=pedido.valor_total or ZERO,
            )
            valor += pedido.valor_total or ZERO

        romaneio.peso_total_kg = peso
        romaneio.valor_total = valor
        romaneio.save(update_fields=['peso_total_kg', 'valor_total', 'updated_at'])
        return romaneio

    @staticmethod
    def _endereco(cliente) -> dict:
        """O endereço de entrega, copiado do cliente no momento da carga."""
        if cliente is None:
            return {}
        return {
            campo: getattr(cliente, campo, '') or ''
            for campo in ('endereco', 'numero', 'bairro', 'cidade', 'uf', 'cep')
        }
