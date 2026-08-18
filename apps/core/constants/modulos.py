"""
Registro dos módulos (seções do menu) que podem ser ligados/desligados.

Duas camadas se combinam para decidir o que uma filial enxerga:

  1. DISPONIBILIDADE (empresa) -- um módulo marcado com `segmentos` só
     existe para empresas daquele vertical. Módulo com `segmentos` vazio é
     universal. O admin pode liberar um módulo fora do segmento pela lista
     `Empresa.modulos_extras`.
  2. ATIVAÇÃO (filial) -- dentro do que está disponível, a filial pode
     desligar o que não usa, via `Filial.modulos_desativados`.

Quem resolve essa combinação é `apps/core/services/modulos.py` -- é de lá
que middleware, sidebar e Central Administrativa tiram a resposta, para as
três não divergirem.

Para acrescentar um vertical novo: cadastre o segmento em
`constants/segmentos.py`, acrescente os módulos dele aqui com `segmentos`
preenchido e registre os prefixos de URL. O resto do ERP não muda.

`chave` é o mesmo nome usado no Alpine `secoes.<chave>` do sidebar
(`apps/core/templates/core/_sidebar.html`).
"""
from dataclasses import dataclass, field

from apps.core.constants import segmentos as seg


@dataclass(frozen=True)
class Modulo:
    chave: str
    label: str
    descricao: str
    # Prefixos de URL da seção -- o middleware usa para barrar acesso direto,
    # não só esconder do menu.
    prefixos: tuple[str, ...] = ()
    # Vazio = universal. Preenchido = só para empresas destes segmentos.
    segmentos: tuple[str, ...] = field(default_factory=tuple)

    @property
    def e_universal(self) -> bool:
        return not self.segmentos


MODULOS = [
    Modulo(
        'cadastros', 'Cadastros',
        'Clientes, fornecedores e produtos.',
        prefixos=('/cadastros/', '/produtos/'),
    ),
    Modulo(
        'operacoes', 'Operações',
        'Estoque, compras, produção e lotes.',
        prefixos=('/estoque/', '/compras/', '/producao/', '/lotes/'),
    ),
    Modulo(
        'financeiro', 'Financeiro',
        'Contas a pagar/receber, fluxo de caixa, conciliação e DRE.',
        prefixos=('/financeiro/',),
    ),
    Modulo(
        'logistica', 'Logística',
        'Romaneios, coletas, manifestos, CT-e/MDF-e.',
        prefixos=('/logistica/',),
    ),
    Modulo(
        'avancado', 'Avançado',
        'PDV, fiscal, qualidade e relatórios (Analytics).',
        prefixos=('/pdv/', '/fiscal/', '/qualidade/', '/analytics/'),
    ),
    # ── Verticais ────────────────────────────────────────────────────────
    Modulo(
        'food_service', 'Food Service',
        'Mesas, comandas, cozinha (KDS) e cardápio digital.',
        prefixos=('/food-service/',),
        # Deixou de ser universal: mesa e comanda não fazem sentido numa
        # distribuidora ou num atacado, e o menu delas ficava poluído.
        # Para conceder a outro ramo, acrescente o segmento aqui — ou ligue
        # na mão pela Central, que é o caminho para quem não se encaixa em
        # nenhum destes.
        segmentos=(seg.PADARIAS, seg.INDUSTRIA_ALIMENTICIA),
    ),
    Modulo(
        'moda', 'Moda / Confecção',
        'Ficha de produção, engenharia do produto, corte, costura e acabamento.',
        prefixos=('/moda/',),
        segmentos=(seg.MODA_CONFECCAO,),
    ),
]

MODULOS_POR_CHAVE = {m.chave: m for m in MODULOS}

# Mantido porque `Filial.modulos_desativados` guarda chaves: se um módulo
# for renomeado, o valor antigo no banco deixa de casar e o módulo volta a
# aparecer ligado. Renomear chave exige migration de dados.
CHAVES_VALIDAS = frozenset(MODULOS_POR_CHAVE)
