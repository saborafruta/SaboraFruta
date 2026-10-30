from django import forms

from apps.core.models import Filial, Usuario

from .models import (
    ConexaoWhatsAppVendedor,
    ConfiguracaoWhatsApp,
    ConfiguracaoWhatsAppCentral,
)


STATUS_PEDIDO_NOTIFICACAO_CHOICES = (
    ('aprovado', 'Pedido aprovado'),
    ('em_separacao', 'Separação iniciada'),
    ('pendente_caixa', 'Pendente no caixa'),
    ('pago', 'Pagamento confirmado'),
    ('pronto', 'Separação concluída / pronto'),
    ('saiu_entrega', 'Saiu para entrega'),
    ('entregue', 'Pedido entregue'),
    ('cancelado', 'Pedido cancelado'),
)


class ConexaoWhatsAppVendedorForm(forms.ModelForm):
    meta_access_token = forms.CharField(
        label='Token permanente da Meta', required=False,
        widget=forms.PasswordInput(
            attrs={'autocomplete': 'new-password'}, render_value=False,
        ),
        help_text='Deixe vazio para manter o token já salvo.',
    )
    meta_verify_token = forms.CharField(
        label='Token de verificação do webhook', required=False,
        widget=forms.PasswordInput(
            attrs={'autocomplete': 'new-password'}, render_value=False,
        ),
        help_text='Se ficar vazio no primeiro cadastro, o iTED gera um token seguro.',
    )
    meta_app_secret = forms.CharField(
        label='Segredo do aplicativo Meta', required=False,
        widget=forms.PasswordInput(
            attrs={'autocomplete': 'new-password'}, render_value=False,
        ),
        help_text='Usado somente no servidor para validar eventos da Meta.',
    )

    class Meta:
        model = ConexaoWhatsAppVendedor
        fields = [
            'vendedor', 'filial_padrao', 'numero_conectado', 'nome_conectado',
            'meta_phone_number_id', 'meta_business_account_id', 'meta_graph_version',
            'meta_access_token', 'meta_verify_token', 'meta_app_secret', 'ativo',
        ]
        labels = {
            'vendedor': 'Vendedor responsável',
            'filial_padrao': 'Filial padrão',
            'numero_conectado': 'Número comercial',
            'nome_conectado': 'Nome exibido no WhatsApp',
            'meta_phone_number_id': 'ID do número na Meta',
            'meta_business_account_id': 'ID da conta WhatsApp Business',
            'meta_graph_version': 'Versão da Graph API',
            'ativo': 'Conexão ativa',
        }
        widgets = {
            'numero_conectado': forms.TextInput(attrs={
                'type': 'tel', 'inputmode': 'tel', 'placeholder': '5584999999999',
            }),
            'meta_phone_number_id': forms.TextInput(attrs={'inputmode': 'numeric'}),
            'meta_business_account_id': forms.TextInput(attrs={'inputmode': 'numeric'}),
            'meta_graph_version': forms.TextInput(attrs={'placeholder': 'v23.0'}),
        }

    def __init__(self, *args, empresa, **kwargs):
        super().__init__(*args, **kwargs)
        self.empresa = empresa
        self.fields['vendedor'].queryset = Usuario.objects.using('default').filter(
            empresa=empresa, ativo=True,
        ).order_by('nome')
        self.fields['filial_padrao'].queryset = Filial.objects.using('default').filter(
            empresa=empresa,
        ).order_by('nome_fantasia', 'razao_social')
        for campo in self.fields.values():
            classes = campo.widget.attrs.get('class', '')
            campo.widget.attrs['class'] = f'{classes} crm-control'.strip()

    def clean(self):
        dados = super().clean()
        vendedor = dados.get('vendedor')
        filial = dados.get('filial_padrao')
        if vendedor and vendedor.empresa_id != self.empresa.pk:
            self.add_error('vendedor', 'O vendedor pertence a outra empresa.')
        if filial and filial.empresa_id != self.empresa.pk:
            self.add_error('filial_padrao', 'A filial pertence a outra empresa.')
        return dados

    def save(self, commit=True):
        import secrets

        conexao = super().save(commit=False)
        conexao.empresa = self.empresa
        conexao.somente_atendimento_humano = True
        conexao.definir_meta_access_token(self.cleaned_data.get('meta_access_token'))
        verify_token = self.cleaned_data.get('meta_verify_token')
        if not verify_token and not conexao.obter_meta_verify_token():
            verify_token = secrets.token_urlsafe(32)
        conexao.definir_meta_verify_token(verify_token)
        conexao.definir_meta_app_secret(self.cleaned_data.get('meta_app_secret'))
        if conexao.status == ConexaoWhatsAppVendedor.Status.NAO_CONFIGURADO:
            conexao.status = ConexaoWhatsAppVendedor.Status.PENDENTE
        if commit:
            conexao.full_clean()
            conexao.save()
        return conexao


class ConfiguracaoWhatsAppForm(forms.ModelForm):
    meta_access_token = forms.CharField(
        label='Token permanente da Meta', required=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}, render_value=False),
        help_text='Fica criptografado. Deixe vazio para manter o token atual.',
    )
    meta_verify_token = forms.CharField(
        label='Token de verificação do webhook', required=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}, render_value=False),
        help_text='Escolha um segredo e informe o mesmo ao cadastrar o webhook na Meta.',
    )
    meta_app_secret = forms.CharField(
        label='Segredo do aplicativo Meta', required=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}, render_value=False),
        help_text='Usado para validar a assinatura dos eventos recebidos.',
    )
    cobranca_dias_relativos = forms.MultipleChoiceField(
        label='Quando enviar os avisos',
        choices=(
            ('-7', '7 dias antes'), ('-3', '3 dias antes'), ('-1', '1 dia antes'),
            ('0', 'No vencimento'), ('1', '1 dia depois'), ('3', '3 dias depois'),
            ('7', '7 dias depois'),
        ),
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )

    class Meta:
        model = ConfiguracaoWhatsApp
        fields = [
            'provedor',
            'meta_phone_number_id', 'meta_business_account_id', 'meta_graph_version',
            'meta_access_token', 'meta_verify_token', 'meta_app_secret',
            'agente_ativo', 'ativo',
            'cobranca_ativa', 'cobranca_exigir_consentimento',
            'cobranca_horario_inicio', 'cobranca_horario_fim',
            'cobranca_intervalo_minutos', 'cobranca_limite_diario',
            'cobranca_max_tentativas', 'cobranca_dias_relativos',
            'cobranca_mensagem', 'cobranca_template_meta', 'cobranca_template_idioma',
        ]
        labels = {
            'provedor': 'Provedor do WhatsApp',
            'meta_phone_number_id': 'ID do número de telefone',
            'meta_business_account_id': 'ID da conta WhatsApp Business',
            'meta_graph_version': 'Versão da Graph API',
            'agente_ativo': 'Responder clientes automaticamente',
            'ativo': 'Integração ativa',
            'cobranca_ativa': 'Ativar avisos automáticos de boletos',
            'cobranca_exigir_consentimento': 'Exigir autorização registrada do cliente',
            'cobranca_horario_inicio': 'Iniciar os envios às',
            'cobranca_horario_fim': 'Encerrar os envios às',
            'cobranca_intervalo_minutos': 'Intervalo mínimo entre mensagens (minutos)',
            'cobranca_limite_diario': 'Limite diário por número',
            'cobranca_max_tentativas': 'Máximo de tentativas',
            'cobranca_mensagem': 'Mensagem para Evolution API',
            'cobranca_template_meta': 'Nome do template aprovado na Meta',
            'cobranca_template_idioma': 'Idioma do template',
        }
        widgets = {
            'meta_phone_number_id': forms.TextInput(attrs={'inputmode': 'numeric'}),
            'meta_business_account_id': forms.TextInput(attrs={'inputmode': 'numeric'}),
            'meta_graph_version': forms.TextInput(attrs={'placeholder': 'v23.0'}),
            'cobranca_horario_inicio': forms.TimeInput(attrs={'type': 'time'}),
            'cobranca_horario_fim': forms.TimeInput(attrs={'type': 'time'}),
            'cobranca_intervalo_minutos': forms.NumberInput(attrs={'min': 1, 'max': 120}),
            'cobranca_limite_diario': forms.NumberInput(attrs={'min': 1, 'max': 5000}),
            'cobranca_max_tentativas': forms.NumberInput(attrs={'min': 1, 'max': 5}),
            'cobranca_mensagem': forms.Textarea(attrs={'rows': 7}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and not self.is_bound:
            self.initial['cobranca_dias_relativos'] = [
                str(item) for item in (self.instance.cobranca_dias_relativos or [])
            ]

    def clean_cobranca_dias_relativos(self):
        return sorted({int(item) for item in self.cleaned_data.get('cobranca_dias_relativos') or []})

    def clean(self):
        dados = super().clean()
        if dados.get('cobranca_horario_inicio') and dados.get('cobranca_horario_fim'):
            if dados['cobranca_horario_inicio'] >= dados['cobranca_horario_fim']:
                self.add_error('cobranca_horario_fim', 'O fim deve ser posterior ao início.')
        if dados.get('provedor') == ConfiguracaoWhatsApp.Provedor.META_CLOUD:
            if not dados.get('meta_phone_number_id'):
                self.add_error('meta_phone_number_id', 'Informe o ID do número da Meta.')
            if not (
                dados.get('meta_access_token')
                or (self.instance and self.instance.obter_meta_access_token())
            ):
                self.add_error('meta_access_token', 'Informe o token permanente da Meta.')
            if not (
                dados.get('meta_verify_token')
                or (self.instance and self.instance.obter_meta_verify_token())
            ):
                self.add_error('meta_verify_token', 'Informe o token de verificação do webhook.')
            if not (
                dados.get('meta_app_secret')
                or (self.instance and self.instance.obter_meta_app_secret())
            ):
                self.add_error(
                    'meta_app_secret',
                    'Informe o segredo do aplicativo para validar os webhooks da Meta.',
                )
            if dados.get('cobranca_ativa') and not dados.get('cobranca_template_meta'):
                self.add_error('cobranca_template_meta', 'Informe o template aprovado para cobranças.')
            if dados.get('cobranca_ativa') and not dados.get('cobranca_exigir_consentimento'):
                self.add_error(
                    'cobranca_exigir_consentimento',
                    'A API oficial exige autorização prévia registrada do destinatário.',
                )
        if dados.get('cobranca_ativa') and not dados.get('cobranca_dias_relativos'):
            self.add_error('cobranca_dias_relativos', 'Selecione ao menos um momento de envio.')
        return dados

    def save(self, commit=True):
        instancia = super().save(commit=False)
        instancia.definir_meta_access_token(self.cleaned_data.get('meta_access_token'))
        instancia.definir_meta_verify_token(self.cleaned_data.get('meta_verify_token'))
        instancia.definir_meta_app_secret(self.cleaned_data.get('meta_app_secret'))
        if commit:
            instancia.save()
            self.save_m2m()
        return instancia


class FluxoWhatsAppForm(forms.ModelForm):
    ia_api_key = forms.CharField(
        label='Chave da API da OpenAI',
        required=False,
        widget=forms.PasswordInput(
            attrs={
                'autocomplete': 'new-password',
                'placeholder': 'Cole a nova chave da OpenAI',
            },
            render_value=False,
        ),
        help_text='A chave será criptografada e nunca voltará a aparecer na tela.',
    )
    pedido_status_notificados = forms.MultipleChoiceField(
        label='Enviar atualização nestas etapas',
        choices=STATUS_PEDIDO_NOTIFICACAO_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        help_text='Desmarque as etapas internas que não devem gerar mensagem no WhatsApp.',
    )

    class Meta:
        model = ConfiguracaoWhatsApp
        fields = [
            'ia_ativa', 'ia_restringir_horario', 'ia_horario_inicio', 'ia_horario_fim',
            'ia_mensagem_fora_horario', 'ia_mensagem_erro',
            'ia_audio_ativo', 'ia_modelo', 'ia_modelo_transcricao',
            'ia_cotacao_dolar', 'ia_instrucoes',
            'modo_atendimento',
            'mensagem_saudacao', 'mensagem_transferencia',
            'mensagem_encerramento', 'mensagem_opcao_invalida',
            'mensagem_confirmacao_agendamento', 'mensagem_lembrete_agendamento',
            'mensagem_resumo_pedido', 'mensagem_pedido_recebido', 'mensagem_atualizacao_pedido',
            'mensagem_pedido_pronto_retirada',
            'pedido_status_notificados',
            'recuperacao_agendamento_ativa', 'recuperacao_atraso_minutos',
            'recuperacao_horario_inicio', 'recuperacao_horario_fim',
            'mensagem_recuperacao_agendamento',
            'mensagem_motivo_nao_agendamento', 'mensagem_feedback_agendamento',
            'encerramento_automatico_ativo', 'tempo_inatividade_minutos',
        ]
        labels = {
            'ia_ativa': 'Ativar agente com IA',
            'ia_restringir_horario': 'Atender com IA somente neste horário',
            'ia_horario_inicio': 'IA inicia o atendimento às',
            'ia_horario_fim': 'IA encerra o atendimento às',
            'ia_mensagem_fora_horario': 'Mensagem fora do horário',
            'ia_mensagem_erro': 'Mensagem quando a IA estiver indisponível',
            'ia_audio_ativo': 'Entender mensagens de áudio',
            'ia_modelo': 'Modelo para respostas',
            'ia_modelo_transcricao': 'Modelo para transcrição',
            'ia_cotacao_dolar': 'Cotação do dólar para estimativa em reais',
            'ia_instrucoes': 'Orientações adicionais para a IA',
            'modo_atendimento': 'Serviços oferecidos pelo agente',
            'mensagem_saudacao': 'Mensagem de saudação',
            'mensagem_transferencia': 'Mensagem de transferência',
            'mensagem_encerramento': 'Mensagem de encerramento',
            'mensagem_opcao_invalida': 'Mensagem para opção não reconhecida',
            'mensagem_confirmacao_agendamento': 'Confirmação do agendamento',
            'mensagem_lembrete_agendamento': 'Lembrete do agendamento',
            'mensagem_resumo_pedido': 'Resumo final do pedido',
            'mensagem_pedido_recebido': 'Pedido confirmado pelo cliente',
            'mensagem_atualizacao_pedido': 'Atualização enviada pela loja',
            'mensagem_pedido_pronto_retirada': 'Pedido pronto para retirada',
            'recuperacao_agendamento_ativa': 'Acompanhar agendamentos não concluídos',
            'recuperacao_atraso_minutos': 'Enviar acompanhamento após (minutos)',
            'recuperacao_horario_inicio': 'Enviar a partir de',
            'recuperacao_horario_fim': 'Enviar até',
            'mensagem_recuperacao_agendamento': 'Mensagem de recuperação',
            'mensagem_motivo_nao_agendamento': 'Pergunta sobre o motivo',
            'mensagem_feedback_agendamento': 'Agradecimento após a resposta',
            'encerramento_automatico_ativo': 'Encerrar conversas automaticamente',
            'tempo_inatividade_minutos': 'Tempo de inatividade (minutos)',
        }
        widgets = {
            'ia_modelo': forms.TextInput(attrs={'placeholder': 'gpt-4o-mini'}),
            'ia_modelo_transcricao': forms.TextInput(
                attrs={'placeholder': 'gpt-4o-mini-transcribe'},
            ),
            'ia_cotacao_dolar': forms.NumberInput(attrs={'min': '0.01', 'step': '0.0001'}),
            'ia_instrucoes': forms.Textarea(attrs={'rows': 4}),
            'ia_horario_inicio': forms.TimeInput(attrs={'type': 'time'}),
            'ia_horario_fim': forms.TimeInput(attrs={'type': 'time'}),
            'ia_mensagem_fora_horario': forms.Textarea(attrs={'rows': 4}),
            'ia_mensagem_erro': forms.Textarea(attrs={'rows': 3}),
            'mensagem_saudacao': forms.Textarea(attrs={'rows': 3}),
            'mensagem_transferencia': forms.Textarea(attrs={'rows': 3}),
            'mensagem_encerramento': forms.Textarea(attrs={'rows': 3}),
            'mensagem_opcao_invalida': forms.Textarea(attrs={'rows': 2}),
            'mensagem_confirmacao_agendamento': forms.Textarea(attrs={'rows': 9}),
            'mensagem_lembrete_agendamento': forms.Textarea(attrs={'rows': 9}),
            'mensagem_resumo_pedido': forms.Textarea(attrs={'rows': 13}),
            'mensagem_pedido_recebido': forms.Textarea(attrs={'rows': 3}),
            'mensagem_atualizacao_pedido': forms.Textarea(attrs={'rows': 5}),
            'mensagem_pedido_pronto_retirada': forms.Textarea(attrs={'rows': 5}),
            'mensagem_recuperacao_agendamento': forms.Textarea(attrs={'rows': 9}),
            'mensagem_motivo_nao_agendamento': forms.Textarea(attrs={'rows': 8}),
            'mensagem_feedback_agendamento': forms.Textarea(attrs={'rows': 3}),
            'recuperacao_atraso_minutos': forms.NumberInput(attrs={'min': 5, 'max': 10080}),
            'recuperacao_horario_inicio': forms.TimeInput(attrs={'type': 'time'}),
            'recuperacao_horario_fim': forms.TimeInput(attrs={'type': 'time'}),
            'tempo_inatividade_minutos': forms.NumberInput(attrs={'min': 1, 'max': 10080}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and not self.is_bound:
            self.initial['pedido_status_notificados'] = list(
                self.instance.pedido_status_notificados or [],
            )

    def clean_pedido_status_notificados(self):
        return list(self.cleaned_data.get('pedido_status_notificados') or [])

    def clean(self):
        dados = super().clean()
        if (
            dados.get('ia_ativa')
            and not (dados.get('ia_api_key') or self.instance.ia_api_key_configurada)
        ):
            self.add_error(
                'ia_api_key',
                'Informe a chave da OpenAI antes de ativar o agente com IA.',
            )
        if (
            dados.get('ia_restringir_horario')
            and dados.get('ia_horario_inicio') == dados.get('ia_horario_fim')
        ):
            self.add_error(
                'ia_horario_fim',
                'O início e o fim devem ser diferentes. Desative a restrição para atendimento 24 horas.',
            )
        return dados

    def save(self, commit=True):
        instancia = super().save(commit=False)
        instancia.definir_ia_api_key(self.cleaned_data.get('ia_api_key'))
        if commit:
            instancia.save()
            self.save_m2m()
        return instancia


class ConfiguracaoWhatsAppCentralForm(forms.ModelForm):
    class Meta:
        model = ConfiguracaoWhatsAppCentral
        fields = [
            'instancia', 'resumos_ativos', 'horario_inicio', 'horario_fim',
            'intervalo_entre_envios_minutos', 'limite_diario', 'max_tentativas',
            'mensagem_resumo',
        ]
        labels = {
            'instancia': 'Nome da instância central',
            'resumos_ativos': 'Ativar envios automáticos',
            'horario_inicio': 'Iniciar os envios às',
            'horario_fim': 'Encerrar os envios às',
            'intervalo_entre_envios_minutos': 'Intervalo mínimo entre mensagens (minutos)',
            'limite_diario': 'Limite máximo de mensagens por dia',
            'max_tentativas': 'Máximo de tentativas por mensagem',
            'mensagem_resumo': 'Modelo da mensagem diária',
        }
        widgets = {
            'horario_inicio': forms.TimeInput(attrs={'type': 'time'}),
            'horario_fim': forms.TimeInput(attrs={'type': 'time'}),
            'intervalo_entre_envios_minutos': forms.NumberInput(attrs={'min': 1, 'max': 120}),
            'limite_diario': forms.NumberInput(attrs={'min': 1, 'max': 5000}),
            'max_tentativas': forms.NumberInput(attrs={'min': 1, 'max': 5}),
            'mensagem_resumo': forms.Textarea(attrs={'rows': 16}),
        }

    def clean(self):
        dados = super().clean()
        inicio = dados.get('horario_inicio')
        fim = dados.get('horario_fim')
        if inicio and fim and inicio >= fim:
            raise forms.ValidationError('O fim da janela deve ser posterior ao início.')
        return dados
