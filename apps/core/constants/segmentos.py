"""
Segmento de atuação da empresa.

O segmento é o que decide quais módulos especializados (verticais) a
empresa enxerga -- ver `apps/core/constants/modulos.py`. Fica em constantes
soltas, e não como TextChoices dentro do model, porque `modulos.py` precisa
das chaves e importar models de lá criaria import circular.

Para acrescentar um vertical novo depois: adicione a chave aqui, inclua na
lista SEGMENTOS e marque os módulos dele em `modulos.py`. Nada mais no ERP
precisa mudar.
"""

MODA_CONFECCAO = 'moda_confeccao'
INDUSTRIA_ALIMENTICIA = 'industria_alimenticia'
COMERCIO = 'comercio'
DISTRIBUIDORA = 'distribuidora'
ATACADO = 'atacado'
VAREJO = 'varejo'
POLPA_FRUTAS = 'polpa_frutas'
PADARIAS = 'padarias'
EMBALAGENS = 'embalagens'

SEGMENTOS = [
    (MODA_CONFECCAO, 'Moda / Confecção'),
    (INDUSTRIA_ALIMENTICIA, 'Indústria Alimentícia'),
    (COMERCIO, 'Comércio'),
    (DISTRIBUIDORA, 'Distribuidora'),
    (ATACADO, 'Atacado'),
    (VAREJO, 'Varejo'),
    (POLPA_FRUTAS, 'Polpa de Frutas'),
    (PADARIAS, 'Padarias'),
    (EMBALAGENS, 'Embalagens'),
]

LABEL_POR_SEGMENTO = dict(SEGMENTOS)
