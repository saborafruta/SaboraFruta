"""
Parâmetros do cálculo de recompra.

Todos os números que definem "o que é um cliente semanal" ou "quando o
alerta fica amarelo" vivem aqui — é o único arquivo a mexer para ajustar
o comportamento do módulo sem tocar na lógica.
"""

# Mínimo de compras concluídas para classificar um cliente. Com 4 compras
# temos 3 intervalos, o suficiente para uma média com algum sentido.
MIN_COMPRAS_PARA_PADRAO = 4

# Quantas das compras mais recentes entram na média. Limitar evita que um
# padrão antigo (cliente que comprava toda semana há um ano) mascare o
# comportamento atual.
MAX_COMPRAS_CONSIDERADAS = 12

# Janela de histórico varrida no recálculo.
JANELA_HISTORICO_DIAS = 460  # ~15 meses

# Faixas de classificação: (mínimo, máximo) em dias, ambos inclusivos.
FAIXA_SEMANAL = (6, 8)
FAIXA_QUINZENAL = (13, 17)
FAIXA_MENSAL = (28, 32)

# Cards de padrão de recompra na tela de alertas. Os quatro primeiros são
# fixos; os três últimos o usuário ajusta no próprio card.
FAIXAS_CARD_FIXAS = [7, 14, 21, 30]
FAIXAS_CARD_PERSONALIZADAS_PADRAO = [90, 120, 360]

# Dias de antecedência em que o alerta passa de verde para amarelo.
DIAS_ALERTA_AMARELO = 3

# Pesos do score de prioridade (0-100). Somam 100.
PESO_URGENCIA = 35       # atraso relativo à própria média do cliente
PESO_VALOR = 25          # ticket médio comparado ao resto da base
PESO_REGULARIDADE = 20   # quão previsível é o cliente (desvio padrão baixo)
PESO_RECORRENCIA = 10    # quantidade de compras no histórico
PESO_RELACIONAMENTO = 10 # há quanto tempo é cliente

# Tetos usados para normalizar os pesos acima.
TETO_COMPRAS_SCORE = 12       # nº de compras que já vale nota cheia
TETO_RELACIONAMENTO_DIAS = 365  # 1 ano de casa já vale nota cheia
TETO_ATRASO_RELATIVO = 2.0    # atraso de 2x a média já vale nota cheia

# Horas até os dados serem considerados obsoletos e valer um recálculo
# em lote quando alguém abre a tela.
HORAS_ATE_OBSOLETO = 6
