package br.com.ited.forcavendas.feature.auth

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.Business
import androidx.compose.material.icons.outlined.Fingerprint
import androidx.compose.material.icons.outlined.Lock
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.Switch
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import br.com.ited.forcavendas.data.LoginResult
import br.com.ited.forcavendas.data.WorkContext
import br.com.ited.forcavendas.ui.components.ItedLogo
import br.com.ited.forcavendas.ui.theme.SpaceGroteskFontFamily

@Composable
fun LoginScreen(
    loading: Boolean,
    error: String?,
    deviceSecurityAvailable: Boolean,
    onLogin: (String, String, Boolean) -> Unit,
) {
    var email by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var secureAccess by remember(deviceSecurityAvailable) {
        mutableStateOf(deviceSecurityAvailable)
    }
    Box(
        modifier = Modifier
            .fillMaxSize()
            .padding(24.dp),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            modifier = Modifier.fillMaxWidth(),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            ItedLogo(modifier = Modifier.size(148.dp))
            Text(
                "Força de Vendas",
                style = MaterialTheme.typography.headlineSmall,
                fontFamily = SpaceGroteskFontFamily,
                fontWeight = FontWeight.Bold,
            )
            Text(
                "Entre com o mesmo usuário do ITED",
                style = MaterialTheme.typography.bodyMedium,
            )
            OutlinedTextField(
                value = email,
                onValueChange = { email = it },
                modifier = Modifier.fillMaxWidth(),
                label = { Text("E-mail") },
                singleLine = true,
                keyboardOptions = KeyboardOptions(
                    keyboardType = KeyboardType.Email,
                    imeAction = ImeAction.Next,
                ),
            )
            OutlinedTextField(
                value = password,
                onValueChange = { password = it },
                modifier = Modifier.fillMaxWidth(),
                label = { Text("Senha") },
                singleLine = true,
                visualTransformation = PasswordVisualTransformation(),
                keyboardOptions = KeyboardOptions(
                    keyboardType = KeyboardType.Password,
                    imeAction = ImeAction.Done,
                ),
            )
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(12.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Icon(Icons.Outlined.Lock, contentDescription = null)
                Column(modifier = Modifier.weight(1f)) {
                    Text("Salvar acesso com segurança", fontWeight = FontWeight.SemiBold)
                    Text(
                        if (deviceSecurityAvailable) {
                            "Use biometria, PIN, padrão ou senha do celular nos próximos acessos."
                        } else {
                            "Configure um bloqueio de tela seguro no celular para habilitar."
                        },
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
                Switch(
                    checked = secureAccess,
                    enabled = deviceSecurityAvailable && !loading,
                    onCheckedChange = { secureAccess = it },
                )
            }
            if (!error.isNullOrBlank()) {
                Text(error, color = MaterialTheme.colorScheme.error)
            }
            Button(
                modifier = Modifier.fillMaxWidth(),
                enabled = !loading && email.isNotBlank() && password.isNotBlank(),
                onClick = { onLogin(email, password, secureAccess) },
            ) {
                if (loading) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(20.dp),
                        strokeWidth = 2.dp,
                    )
                } else {
                    Text("Entrar")
                }
            }
        }
    }
}

@Composable
fun UnlockScreen(
    userName: String,
    error: String?,
    onUnlock: () -> Unit,
    onUseAnotherAccount: () -> Unit,
) {
    Box(
        modifier = Modifier
            .fillMaxSize()
            .padding(24.dp),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            modifier = Modifier.fillMaxWidth(),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            ItedLogo(modifier = Modifier.size(132.dp))
            Icon(
                imageVector = Icons.Outlined.Fingerprint,
                contentDescription = null,
                modifier = Modifier.size(44.dp),
                tint = MaterialTheme.colorScheme.primary,
            )
            Text(
                "Acesso protegido",
                style = MaterialTheme.typography.headlineSmall,
                fontFamily = SpaceGroteskFontFamily,
                fontWeight = FontWeight.Bold,
            )
            Text(
                "Olá, $userName. Confirme sua identidade usando a segurança do aparelho.",
                style = MaterialTheme.typography.bodyMedium,
            )
            if (!error.isNullOrBlank()) {
                Text(error, color = MaterialTheme.colorScheme.error)
            }
            Button(modifier = Modifier.fillMaxWidth(), onClick = onUnlock) {
                Text("Desbloquear")
            }
            androidx.compose.material3.TextButton(onClick = onUseAnotherAccount) {
                Text("Entrar com outra conta")
            }
        }
    }
}

@Composable
fun ContextSelectionScreen(
    result: LoginResult,
    currentContext: WorkContext? = null,
    onBack: (() -> Unit)? = null,
    onSelect: (WorkContext) -> Unit,
) {
    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(20.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            if (onBack != null) {
                IconButton(onClick = onBack) {
                    Icon(
                        Icons.AutoMirrored.Outlined.ArrowBack,
                        contentDescription = "Voltar",
                    )
                }
            }
            Text(
                if (currentContext == null) "Onde você vai trabalhar?" else "Trocar filial",
                style = MaterialTheme.typography.headlineSmall,
                fontWeight = FontWeight.Bold,
            )
            Text(
                "Olá, ${result.usuarioNome}. Selecione a empresa e a filial.",
                modifier = Modifier.padding(top = 6.dp),
            )
        }
        items(result.contexts) { context ->
            Card(
                modifier = Modifier
                    .fillMaxWidth()
                    .clickable { onSelect(context) },
            ) {
                Row(
                    modifier = Modifier.padding(16.dp),
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Icon(Icons.Outlined.Business, contentDescription = null)
                    Column {
                        Text(context.empresaNome, fontWeight = FontWeight.Bold)
                        Text(context.filialNome)
                        Text(
                            context.filialCnpj,
                            style = MaterialTheme.typography.labelSmall,
                        )
                    }
                    if (
                        currentContext != null &&
                        context.tenantAlias == currentContext.tenantAlias &&
                        context.filialId == currentContext.filialId
                    ) {
                        Text(
                            "Atual",
                            color = MaterialTheme.colorScheme.primary,
                            style = MaterialTheme.typography.labelMedium,
                            fontWeight = FontWeight.Bold,
                        )
                    }
                }
            }
        }
    }
}
