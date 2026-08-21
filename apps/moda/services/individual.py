"""
Conferência da lista de pessoas contra a grade, e importação de planilha.

A conferência responde a pergunta que a produção faz antes de cortar: "a
lista de nomes bate com o que foi pedido?". Ela compara, por item e
tamanho, a quantidade da grade com quantas pessoas foram cadastradas.
"""
import csv
import io
from dataclasses import dataclass, field

from django.db import transaction

from apps.core.services.exceptions import DadosInvalidosError

from ..models import ItemGradePedido, PersonalizacaoIndividual, Tamanho

# Nomes de coluna aceitos na planilha. Vários por campo porque cada
# confecção rotula à sua maneira, e recusar "jogador" em vez de "nome"
# faria o usuário editar o arquivo antes de importar.
COLUNAS = {
    'nome': ('nome', 'jogador', 'atleta', 'pessoa'),
    'numero': ('numero', 'número', 'num', 'n', 'camisa'),
    'tamanho': ('tamanho', 'tam', 'size'),
    'produto': ('produto', 'item', 'peca', 'peça'),
    'observacoes': ('observacao', 'observação', 'observacoes', 'obs'),
}


@dataclass
class LinhaConferencia:
    item: object
    tamanho: object
    na_grade: int
    pessoas: int

    @property
    def diferenca(self) -> int:
        """Positivo = faltam pessoas; negativo = pessoas a mais que a grade."""
        return self.na_grade - self.pessoas

    @property
    def confere(self) -> bool:
        return self.diferenca == 0


@dataclass
class ResultadoImportacao:
    criados: int = 0
    erros: list = field(default_factory=list)

    @property
    def teve_erro(self) -> bool:
        return bool(self.erros)


class IndividualService:

    # ── Conferência ──────────────────────────────────────────────────────

    @staticmethod
    def conferir(pedido) -> dict:
        """
        Compara a grade com a lista de pessoas, célula a célula.

        Percorre as células da grade — e não a lista de pessoas — para que
        um tamanho pedido sem ninguém cadastrado apareça como pendência.
        Fosse ao contrário, esse caso simplesmente não seria listado.
        """
        pessoas_por_celula = {}
        for p in pedido.individuais.all():
            chave = (p.item_id, p.tamanho_id)
            pessoas_por_celula[chave] = pessoas_por_celula.get(chave, 0) + 1

        linhas = []
        celulas = (
            ItemGradePedido.objects
            .filter(item__pedido=pedido)
            .select_related('item', 'item__produto', 'tamanho')
            .order_by('item__ordem', 'tamanho__ordem', 'tamanho__sigla')
        )
        vistas = set()
        for celula in celulas:
            chave = (celula.item_id, celula.tamanho_id)
            vistas.add(chave)
            linhas.append(LinhaConferencia(
                item=celula.item, tamanho=celula.tamanho,
                na_grade=celula.quantidade,
                pessoas=pessoas_por_celula.get(chave, 0),
            ))

        # Pessoa em tamanho que não existe na grade: a grade diz que aquele
        # tamanho não foi pedido, mas alguém está cadastrado nele. Sem esta
        # varredura o erro passaria despercebido.
        for p in pedido.individuais.select_related('item', 'tamanho'):
            chave = (p.item_id, p.tamanho_id)
            if chave in vistas:
                continue
            vistas.add(chave)
            linhas.append(LinhaConferencia(
                item=p.item, tamanho=p.tamanho, na_grade=0,
                pessoas=pessoas_por_celula.get(chave, 0),
            ))

        divergentes = [l for l in linhas if not l.confere]
        return {
            'linhas': linhas,
            'divergentes': divergentes,
            'confere': not divergentes,
            'total_grade': sum(l.na_grade for l in linhas),
            'total_pessoas': sum(l.pessoas for l in linhas),
        }

    # ── Vagas ────────────────────────────────────────────────────────────

    @classmethod
    def vagas(cls, pedido, ignorar=None) -> dict:
        """
        Quantas pessoas ainda cabem em cada tamanho de cada produto.

        É A GRADE QUE MANDA. Se o pedido tem 3 camisas P e uma já foi
        personalizada, sobram 2 — e oferecer P como se coubesse mais é
        deixar a pessoa digitar uma lista que a fábrica não consegue
        produzir, e descobrir isso só na conferência.

        `ignorar` é a pessoa que está sendo EDITADA: a vaga dela não pode
        contar contra ela mesma, senão corrigir o nome de quem ocupou a
        última vaga viraria "não há mais vaga".

        Devolve {item_id: [{'id', 'sigla', 'na_grade', 'pessoas',
        'restam'}]}, com os tamanhos na ordem da grade.
        """
        ocupadas = {}
        for p in pedido.individuais.all():
            if ignorar is not None and p.pk == ignorar:
                continue
            chave = (p.item_id, p.tamanho_id)
            ocupadas[chave] = ocupadas.get(chave, 0) + 1

        por_item = {}
        celulas = (
            ItemGradePedido.objects
            .filter(item__pedido=pedido, quantidade__gt=0)
            .select_related('tamanho')
            .order_by('item__ordem', 'tamanho__ordem', 'tamanho__sigla')
        )
        for celula in celulas:
            pessoas = ocupadas.get((celula.item_id, celula.tamanho_id), 0)
            por_item.setdefault(celula.item_id, []).append({
                'id': celula.tamanho_id,
                'sigla': celula.tamanho.sigla,
                'na_grade': celula.quantidade,
                'pessoas': pessoas,
                'restam': max(celula.quantidade - pessoas, 0),
            })
        return por_item

    @classmethod
    def vaga_livre(cls, pedido, item_id, tamanho_id, ignorar=None) -> int:
        """Quantas vagas restam naquela célula. Zero também é resposta."""
        for linha in cls.vagas(pedido, ignorar=ignorar).get(item_id, []):
            if linha['id'] == tamanho_id:
                return linha['restam']
        return 0

    # ── Importação ───────────────────────────────────────────────────────

    @staticmethod
    def _normalizar(texto: str) -> str:
        import unicodedata
        base = unicodedata.normalize('NFD', str(texto or ''))
        base = ''.join(c for c in base if unicodedata.category(c) != 'Mn')
        return base.strip().lower()

    @classmethod
    def _mapear_colunas(cls, cabecalho: list) -> dict:
        """Descobre qual coluna da planilha é qual campo."""
        mapa = {}
        for indice, titulo in enumerate(cabecalho):
            normalizado = cls._normalizar(titulo)
            for campo, aceitos in COLUNAS.items():
                if normalizado in aceitos and campo not in mapa:
                    mapa[campo] = indice
        return mapa

    @staticmethod
    def _ler_csv(conteudo: bytes) -> list:
        # UTF-8 primeiro, latin-1 depois: planilha exportada do Excel em
        # português quase sempre vem em latin-1, e falhar aí seria o erro
        # mais comum do recurso.
        for codificacao in ('utf-8-sig', 'utf-8', 'latin-1'):
            try:
                texto = conteudo.decode(codificacao)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise DadosInvalidosError('Não consegui ler o arquivo — verifique a codificação.')

        # Descobre o separador: ; é o padrão do Excel em português, , o do
        # resto do mundo.
        amostra = texto[:2000]
        delimitador = ';' if amostra.count(';') >= amostra.count(',') else ','
        return [linha for linha in csv.reader(io.StringIO(texto), delimiter=delimitador)]

    @staticmethod
    def _ler_xlsx(conteudo: bytes) -> list:
        try:
            import openpyxl
        except ImportError:  # pragma: no cover
            raise DadosInvalidosError(
                'Leitura de Excel indisponível no servidor. Exporte como CSV.'
            )
        planilha = openpyxl.load_workbook(io.BytesIO(conteudo), read_only=True, data_only=True)
        aba = planilha.active
        return [
            ['' if c is None else str(c) for c in linha]
            for linha in aba.iter_rows(values_only=True)
        ]

    @classmethod
    @transaction.atomic
    def importar(cls, pedido, arquivo, nome_arquivo: str) -> ResultadoImportacao:
        """
        Importa a lista de pessoas de um CSV ou XLSX.

        Uma linha com erro não impede as outras: a planilha do cliente quase
        sempre tem uma linha torta, e recusar o arquivo inteiro por causa
        dela obrigaria a corrigir e reenviar tudo.
        """
        conteudo = arquivo.read()
        extensao = nome_arquivo.rsplit('.', 1)[-1].lower() if '.' in nome_arquivo else ''

        if extensao in ('xlsx', 'xlsm'):
            linhas = cls._ler_xlsx(conteudo)
        elif extensao == 'csv':
            linhas = cls._ler_csv(conteudo)
        else:
            raise DadosInvalidosError('Envie um arquivo .csv ou .xlsx.')

        if not linhas:
            raise DadosInvalidosError('O arquivo está vazio.')

        mapa = cls._mapear_colunas(linhas[0])
        if 'tamanho' not in mapa:
            raise DadosInvalidosError(
                'Não encontrei a coluna de tamanho. O cabeçalho precisa ter '
                '"Tamanho" — e, opcionalmente, Nome, Número e Produto.'
            )

        itens = list(pedido.itens.select_related('produto').all())
        if not itens:
            raise DadosInvalidosError('Adicione ao menos um produto ao pedido antes de importar.')

        # Pedido com um item só dispensa a coluna Produto: é o caso comum
        # (um time pedindo um kit), e exigir a coluna seria burocracia.
        item_padrao = itens[0] if len(itens) == 1 else None

        tamanhos = {
            cls._normalizar(t.sigla): t
            for t in Tamanho.objects.for_filial(pedido.filial)
        }

        resultado = ResultadoImportacao()
        novos = []
        ultima_ordem = pedido.individuais.count() * 10

        for numero_linha, linha in enumerate(linhas[1:], start=2):
            def coluna(campo):
                indice = mapa.get(campo)
                if indice is None or indice >= len(linha):
                    return ''
                return str(linha[indice] or '').strip()

            sigla = coluna('tamanho')
            nome = coluna('nome')
            # Linha totalmente vazia é fim de planilha, não erro.
            if not any([sigla, nome, coluna('numero')]):
                continue

            tamanho = tamanhos.get(cls._normalizar(sigla))
            if tamanho is None:
                resultado.erros.append({
                    'linha': numero_linha,
                    'erro': f'Tamanho "{sigla}" não está cadastrado.',
                })
                continue

            item = item_padrao
            rotulo_produto = coluna('produto')
            if rotulo_produto:
                alvo = cls._normalizar(rotulo_produto)
                item = next(
                    (i for i in itens if alvo in cls._normalizar(i.nome_exibicao)),
                    None,
                )
            if item is None:
                resultado.erros.append({
                    'linha': numero_linha,
                    'erro': (
                        f'Produto "{rotulo_produto}" não bate com nenhum item do pedido.'
                        if rotulo_produto else
                        'O pedido tem mais de um produto — informe a coluna Produto.'
                    ),
                })
                continue

            ultima_ordem += 10
            novos.append(PersonalizacaoIndividual(
                pedido=pedido, item=item, tamanho=tamanho,
                nome=nome[:80], numero=coluna('numero')[:10],
                observacoes=coluna('observacoes')[:160], ordem=ultima_ordem,
            ))

        if novos:
            PersonalizacaoIndividual.objects.bulk_create(novos)
            resultado.criados = len(novos)
        return resultado
