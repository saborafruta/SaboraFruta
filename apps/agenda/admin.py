from django.contrib import admin

from .models import Agendamento, AgendamentoItem, BloqueioAgenda, JornadaTrabalho, ProfissionalAgenda, ProfissionalServico

admin.site.register([ProfissionalAgenda, ProfissionalServico, JornadaTrabalho, BloqueioAgenda, Agendamento, AgendamentoItem])
