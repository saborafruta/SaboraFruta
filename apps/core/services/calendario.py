"""Regras de calendário usadas nos vencimentos financeiros."""

from datetime import date, timedelta
from functools import lru_cache
import unicodedata


def _pascoa(ano: int) -> date:
    """Calcula a Páscoa pelo algoritmo gregoriano de Meeus/Jones/Butcher."""
    a = ano % 19
    b, c = divmod(ano, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes = (h + l - 7 * m + 114) // 31
    dia = (h + l - 7 * m + 114) % 31 + 1
    return date(ano, mes, dia)


def _normalizar(texto: str) -> str:
    return ''.join(
        caractere for caractere in unicodedata.normalize('NFD', texto or '')
        if unicodedata.category(caractere) != 'Mn'
    ).casefold().strip()


@lru_cache(maxsize=128)
def feriados(ano: int, uf: str = '', cidade: str = '', codigo_ibge: str = '') -> frozenset[date]:
    """Retorna feriados nacionais e os locais conhecidos da filial."""
    datas = {
        date(ano, 1, 1),
        date(ano, 4, 21),
        date(ano, 5, 1),
        date(ano, 9, 7),
        date(ano, 10, 12),
        date(ano, 11, 2),
        date(ano, 11, 15),
        date(ano, 11, 20),
        date(ano, 12, 25),
    }

    if (uf or '').upper() == 'RN':
        datas.add(date(ano, 10, 3))

    eh_natal = codigo_ibge == '2408102' or _normalizar(cidade) == 'natal'
    if eh_natal:
        pascoa = _pascoa(ano)
        datas.update({
            date(ano, 1, 6),
            pascoa - timedelta(days=2),
            pascoa + timedelta(days=60),
            date(ano, 11, 21),
        })

    return frozenset(datas)


def proximo_dia_util(data_original: date, filial) -> date:
    """Avança domingos e feriados; sábado é considerado um dia permitido."""
    empresa = getattr(filial, 'empresa', None)
    uf = (getattr(filial, 'uf', '') or getattr(empresa, 'uf', '') or '').upper()
    cidade = getattr(filial, 'cidade', '') or getattr(empresa, 'cidade', '') or ''
    codigo_ibge = (
        getattr(filial, 'codigo_municipio_ibge', '')
        or getattr(empresa, 'codigo_municipio_ibge', '')
        or ''
    )

    data_ajustada = data_original
    while (
        data_ajustada.weekday() == 6
        or data_ajustada in feriados(data_ajustada.year, uf, cidade, codigo_ibge)
    ):
        data_ajustada += timedelta(days=1)
    return data_ajustada


def adicionar_dias_uteis_bancarios(data_original: date, dias: int, filial) -> date:
    """Soma dias de compensacao bancaria, excluindo fins de semana e feriados."""
    empresa = getattr(filial, 'empresa', None)
    uf = (getattr(filial, 'uf', '') or getattr(empresa, 'uf', '') or '').upper()
    cidade = getattr(filial, 'cidade', '') or getattr(empresa, 'cidade', '') or ''
    codigo_ibge = (
        getattr(filial, 'codigo_municipio_ibge', '')
        or getattr(empresa, 'codigo_municipio_ibge', '')
        or ''
    )
    restante = max(int(dias or 0), 0)
    data_ajustada = data_original
    while restante:
        data_ajustada += timedelta(days=1)
        if (
            data_ajustada.weekday() < 5
            and data_ajustada not in feriados(data_ajustada.year, uf, cidade, codigo_ibge)
        ):
            restante -= 1
    return data_ajustada
