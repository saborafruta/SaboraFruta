package br.com.ited.forcavendas.feature.client

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.LocationOn
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.ui.unit.dp
import br.com.ited.forcavendas.data.Client
import br.com.ited.forcavendas.data.ClientRegistration
import br.com.ited.forcavendas.data.ItedRepository
import br.com.ited.forcavendas.data.UserSession
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ClientRegistrationScreen(
    repository: ItedRepository,
    session: UserSession,
    onBack: () -> Unit,
    onSaved: (Client) -> Unit,
) {
    val scope = rememberCoroutineScope()
    var form by remember { mutableStateOf(ClientRegistration()) }
    var saving by remember { mutableStateOf(false) }
    var cnpjLoading by remember { mutableStateOf(false) }
    var cnpjMessage by remember { mutableStateOf<String?>(null) }
    var cepLoading by remember { mutableStateOf(false) }
    var cepMessage by remember { mutableStateOf<String?>(null) }
    var error by remember { mutableStateOf<String?>(null) }

    fun lookupCompany() {
        val digits = form.document.filter(Char::isDigit)
        if (digits.length != 14 || cnpjLoading) {
            if (digits.length != 14) cnpjMessage = "Digite os 14 números do CNPJ."
            return
        }
        scope.launch {
            cnpjLoading = true
            cnpjMessage = null
            runCatching { repository.lookupCompany(session, digits) }
                .onSuccess { company ->
                    form = form.copy(
                        document = company.document,
                        legalName = company.legalName,
                        tradeName = company.tradeName,
                        stateRegistration = company.stateRegistration,
                        street = company.street,
                        number = company.number,
                        complement = company.complement,
                        neighborhood = company.neighborhood,
                        city = company.city,
                        state = company.state,
                        postalCode = company.postalCode,
                        cityCode = company.cityCode,
                        phone = company.phone,
                        email = company.email,
                    )
                    cnpjMessage = buildString {
                        append("Empresa encontrada")
                        if (company.registrationStatus.isNotBlank()) {
                            append(" • ${company.registrationStatus}")
                        }
                    }
                }
                .onFailure { cnpjMessage = it.message ?: "CNPJ não encontrado." }
            cnpjLoading = false
        }
    }

    fun lookupCep() {
        val digits = form.postalCode.filter(Char::isDigit)
        if (digits.length != 8 || cepLoading) return
        scope.launch {
            cepLoading = true
            cepMessage = null
            runCatching { repository.lookupCep(session, digits) }
                .onSuccess { address ->
                    form = form.copy(
                        postalCode = address.postalCode,
                        street = address.street,
                        complement = form.complement.ifBlank { address.complement },
                        neighborhood = address.neighborhood,
                        city = address.city,
                        state = address.state,
                        cityCode = address.cityCode,
                    )
                    cepMessage = "Endereço localizado: ${address.city}/${address.state}"
                }
                .onFailure { cepMessage = it.message ?: "CEP não encontrado." }
            cepLoading = false
        }
    }

    LaunchedEffect(form.postalCode) {
        if (form.postalCode.filter(Char::isDigit).length == 8) {
            delay(450)
            lookupCep()
        } else {
            cepMessage = null
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Novo cliente", fontWeight = FontWeight.Bold) },
                navigationIcon = {
                    IconButton(onClick = onBack, enabled = !saving) {
                        Icon(Icons.AutoMirrored.Outlined.ArrowBack, contentDescription = "Voltar")
                    }
                },
            )
        },
    ) { insets ->
        LazyColumn(
            modifier = Modifier.fillMaxSize().padding(insets),
            contentPadding = PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            item {
                FormSection("Identificação", "Dados principais do cliente") {
                    Text("Tipo de pessoa", style = MaterialTheme.typography.labelLarge)
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        FilterChip(
                            selected = form.personType == "F",
                            onClick = { form = form.copy(personType = "F") },
                            label = { Text("Pessoa física") },
                        )
                        FilterChip(
                            selected = form.personType == "J",
                            onClick = { form = form.copy(personType = "J") },
                            label = { Text("Pessoa jurídica") },
                        )
                    }
                    if (form.personType == "J") {
                        OutlinedTextField(
                            value = form.document,
                            onValueChange = {
                                form = form.copy(document = it.filter(Char::isDigit).take(14))
                                cnpjMessage = null
                            },
                            modifier = Modifier.fillMaxWidth(),
                            label = { Text("CNPJ *") },
                            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                            singleLine = true,
                            trailingIcon = {
                                if (cnpjLoading) CircularProgressIndicator(Modifier.padding(8.dp))
                                else IconButton(onClick = ::lookupCompany) {
                                    Icon(Icons.Outlined.Search, contentDescription = "Consultar CNPJ")
                                }
                            },
                        )
                        if (!cnpjMessage.isNullOrBlank()) {
                            Text(
                                cnpjMessage!!,
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                    AppTextField(
                        value = form.legalName,
                        onValueChange = { form = form.copy(legalName = it) },
                        label = if (form.personType == "F") "Nome completo *" else "Razão social *",
                    )
                    if (form.personType == "J") {
                        AppTextField(form.tradeName, { form = form.copy(tradeName = it) }, "Nome fantasia")
                    }
                    if (form.personType == "F") {
                        AppTextField(
                            form.document,
                            { form = form.copy(document = it.filter(Char::isDigit).take(11)) },
                            "CPF",
                            KeyboardType.Number,
                        )
                    }
                    AppTextField(form.identity, { form = form.copy(identity = it) }, if (form.personType == "F") "RG" else "RG / IE")
                    if (form.personType == "F") {
                        AppTextField(form.birthDate, { form = form.copy(birthDate = it) }, "Data de nascimento (AAAA-MM-DD)", KeyboardType.Number)
                    }
                    Text("Tipo de cliente", style = MaterialTheme.typography.labelLarge)
                    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        listOf("varejo" to "Varejo", "atacado" to "Atacado", "distribuidor" to "Distribuidor").forEach { option ->
                            FilterChip(
                                selected = form.customerType == option.first,
                                onClick = { form = form.copy(customerType = option.first) },
                                label = { Text(option.second) },
                            )
                        }
                    }
                }
            }
            item {
                FormSection("Contato", "Como falar com o cliente") {
                    AppTextField(form.mobile, { form = form.copy(mobile = it) }, "Celular", KeyboardType.Phone)
                    AppTextField(form.phone, { form = form.copy(phone = it) }, "Telefone", KeyboardType.Phone)
                    AppTextField(form.email, { form = form.copy(email = it) }, "E-mail", KeyboardType.Email)
                    AppTextField(form.contactName, { form = form.copy(contactName = it) }, "Nome do contato")
                }
            }
            item {
                FormSection("Endereço", "O CEP preenche os dados automaticamente") {
                    OutlinedTextField(
                        value = form.postalCode,
                        onValueChange = { value ->
                            form = form.copy(postalCode = value.filter(Char::isDigit).take(8))
                        },
                        modifier = Modifier.fillMaxWidth(),
                        label = { Text("CEP") },
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                        singleLine = true,
                        trailingIcon = {
                            if (cepLoading) CircularProgressIndicator(Modifier.padding(8.dp))
                            else IconButton(onClick = ::lookupCep) {
                                Icon(Icons.Outlined.Search, contentDescription = "Buscar CEP")
                            }
                        },
                    )
                    if (!cepMessage.isNullOrBlank()) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Icon(Icons.Outlined.LocationOn, contentDescription = null)
                            Text(
                                cepMessage!!,
                                modifier = Modifier.padding(start = 8.dp),
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                    AppTextField(form.street, { form = form.copy(street = it) }, "Endereço")
                    AppTextField(form.number, { form = form.copy(number = it) }, "Número")
                    AppTextField(form.complement, { form = form.copy(complement = it) }, "Complemento")
                    AppTextField(form.referencePoint, { form = form.copy(referencePoint = it) }, "Ponto de referência")
                    AppTextField(form.neighborhood, { form = form.copy(neighborhood = it) }, "Bairro")
                    AppTextField(form.city, { form = form.copy(city = it) }, "Cidade")
                    AppTextField(form.state, { form = form.copy(state = it.uppercase().take(2)) }, "UF")
                    AppTextField(form.cityCode, { form = form.copy(cityCode = it.filter(Char::isDigit).take(7)) }, "Código IBGE", KeyboardType.Number)
                }
            }
            if (form.personType == "J") {
                item {
                    FormSection("Dados fiscais", "Informações da pessoa jurídica") {
                        AppTextField(form.stateRegistration, { form = form.copy(stateRegistration = it) }, "Inscrição estadual")
                        AppTextField(form.municipalRegistration, { form = form.copy(municipalRegistration = it) }, "Inscrição municipal")
                        AppTextField(form.invoiceEmail, { form = form.copy(invoiceEmail = it) }, "E-mail NF-e", KeyboardType.Email)
                        ToggleRow("Contribuinte de ICMS", form.icmsTaxpayer) { form = form.copy(icmsTaxpayer = it) }
                        ToggleRow("Optante pelo Simples", form.simpleTaxpayer) { form = form.copy(simpleTaxpayer = it) }
                        ToggleRow("Consumidor final", form.finalConsumer) { form = form.copy(finalConsumer = it) }
                    }
                }
            }
            item {
                FormSection("Comercial", "Condições e observações") {
                    AppTextField(form.creditLimit, { form = form.copy(creditLimit = it) }, "Limite de crédito (R$)", KeyboardType.Decimal)
                    AppTextField(form.paymentTermDays, { form = form.copy(paymentTermDays = it.filter(Char::isDigit)) }, "Prazo de pagamento (dias)", KeyboardType.Number)
                    ToggleRow("Cliente bloqueado", form.blocked) { form = form.copy(blocked = it) }
                    if (form.blocked) {
                        AppTextField(form.blockReason, { form = form.copy(blockReason = it) }, "Motivo do bloqueio")
                    }
                    OutlinedTextField(
                        value = form.notes,
                        onValueChange = { form = form.copy(notes = it) },
                        modifier = Modifier.fillMaxWidth(),
                        label = { Text("Observações") },
                        minLines = 3,
                    )
                }
            }
            if (!error.isNullOrBlank()) {
                item { Text(error!!, color = MaterialTheme.colorScheme.error) }
            }
            item {
                Button(
                    modifier = Modifier.fillMaxWidth(),
                    enabled = !saving && form.legalName.isNotBlank(),
                    onClick = {
                        scope.launch {
                            saving = true
                            error = null
                            runCatching { repository.createClient(session, form) }
                                .onSuccess(onSaved)
                                .onFailure { error = it.message ?: "Não foi possível salvar o cliente." }
                            saving = false
                        }
                    },
                ) {
                    if (saving) CircularProgressIndicator(Modifier.padding(end = 10.dp))
                    Text(if (saving) "Salvando..." else "Salvar cliente")
                }
            }
        }
    }
}

@Composable
private fun FormSection(
    title: String,
    subtitle: String,
    content: @Composable ColumnScope.() -> Unit,
) {
    Card(Modifier.fillMaxWidth()) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text(title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
            Text(subtitle, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            content()
        }
    }
}

@Composable
private fun AppTextField(
    value: String,
    onValueChange: (String) -> Unit,
    label: String,
    keyboardType: KeyboardType = KeyboardType.Text,
) {
    OutlinedTextField(
        value = value,
        onValueChange = onValueChange,
        modifier = Modifier.fillMaxWidth(),
        label = { Text(label) },
        keyboardOptions = KeyboardOptions(keyboardType = keyboardType),
        singleLine = true,
    )
}

@Composable
private fun ToggleRow(label: String, checked: Boolean, onCheckedChange: (Boolean) -> Unit) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Text(label)
        Switch(checked = checked, onCheckedChange = onCheckedChange)
    }
}
