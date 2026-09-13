"""Peso de cada janela de vendas (7/15/30/60/90 dias) na demanda ponderada."""
from django.core.exceptions import ValidationError
from django.db import models


class ConfiguracaoDemandaPonderada(models.Model):
    """
    Uma linha por empresa. Peso 0 tira o período do cálculo -- não precisa
    zerar todos pra desligar, só os que não interessam. Sem nenhuma linha
    cadastrada, o padrão é 30d=50%, 60d=30%, 90d=20% (o mesmo exemplo do
    pedido original).
    """

    empresa = models.OneToOneField(
        'core.Empresa', on_delete=models.CASCADE, related_name='configuracao_demanda_ponderada',
    )
    peso_7_dias = models.PositiveSmallIntegerField(default=0)
    peso_15_dias = models.PositiveSmallIntegerField(default=0)
    peso_30_dias = models.PositiveSmallIntegerField(default=50)
    peso_60_dias = models.PositiveSmallIntegerField(default=30)
    peso_90_dias = models.PositiveSmallIntegerField(default=20)

    class Meta:
        db_table = 'estoque_configuracao_demanda_ponderada'
        verbose_name = 'Configuração de demanda ponderada'
        verbose_name_plural = 'Configurações de demanda ponderada'

    def __str__(self):
        return f'Demanda ponderada — {self.empresa}'

    def clean(self):
        pesos = [self.peso_7_dias, self.peso_15_dias, self.peso_30_dias, self.peso_60_dias, self.peso_90_dias]
        if sum(pesos) <= 0:
            raise ValidationError('Pelo menos um período precisa ter peso maior que zero.')

    def como_dict(self) -> dict:
        return {
            7: self.peso_7_dias, 15: self.peso_15_dias, 30: self.peso_30_dias,
            60: self.peso_60_dias, 90: self.peso_90_dias,
        }
