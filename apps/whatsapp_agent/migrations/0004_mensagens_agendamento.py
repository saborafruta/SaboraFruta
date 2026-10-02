from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('whatsapp_agent', '0003_encerramento_automatico'),
    ]

    operations = [
        migrations.AddField(
            model_name='configuracaowhatsapp',
            name='mensagem_confirmacao_agendamento',
            field=models.TextField(
                default=(
                    'Olá, {nome}! ✅\n\n'
                    'Seu agendamento foi confirmado:\n\n'
                    '*Serviço:* {servicos}\n'
                    '*Profissional:* {profissional}\n'
                    '*Data:* {data}\n'
                    '*Horário:* {horario}\n\n'
                    'Seu horário já está reservado. Até lá!'
                ),
                help_text=(
                    'Variáveis disponíveis: {nome}, {servicos}, {profissional}, '
                    '{data} e {horario}.'
                ),
            ),
        ),
        migrations.AddField(
            model_name='configuracaowhatsapp',
            name='mensagem_lembrete_agendamento',
            field=models.TextField(
                default=(
                    'Olá, {nome}! 👋\n\n'
                    'Este é um lembrete do seu agendamento:\n\n'
                    '*Serviço:* {servicos}\n'
                    '*Profissional:* {profissional}\n'
                    '*Data:* {data}\n'
                    '*Horário:* {horario}\n\n'
                    'Esperamos você!'
                ),
                help_text=(
                    'Variáveis disponíveis: {nome}, {servicos}, {profissional}, '
                    '{data} e {horario}.'
                ),
            ),
        ),
    ]
