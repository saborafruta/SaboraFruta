package br.com.ited.forcavendas.feature.sync

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.CloudDone
import androidx.compose.material.icons.outlined.ErrorOutline
import androidx.compose.material.icons.outlined.Schedule
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import br.com.ited.forcavendas.data.PendingOrderInfo
import br.com.ited.forcavendas.data.SyncDashboard
import br.com.ited.forcavendas.data.UserSession
import br.com.ited.forcavendas.sync.SyncStatus
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SyncCenterScreen(
    session: UserSession,
    dashboard: SyncDashboard,
    status: SyncStatus,
    onSync: () -> Unit,
    onBack: () -> Unit,
) {
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Central de sincronização") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
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
                Card(modifier = Modifier.fillMaxWidth()) {
                    Column(
                        modifier = Modifier.padding(16.dp),
                        verticalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        Text(session.context.empresaNome, fontWeight = FontWeight.Bold)
                        Text(session.context.filialNome)
                        Text(
                            "Última sincronização: ${formatInstant(dashboard.lastSuccessAt) ?: "ainda não realizada"}",
                            style = MaterialTheme.typography.bodySmall,
                        )
                        if (dashboard.cachedResources.isNotEmpty()) {
                            Text("Dados disponíveis neste aparelho", fontWeight = FontWeight.SemiBold)
                            dashboard.cachedResources.forEach { resource ->
                                AssistChip(
                                    onClick = {},
                                    label = { Text(resource) },
                                    leadingIcon = {
                                        Icon(Icons.Outlined.CloudDone, contentDescription = null)
                                    },
                                )
                            }
                        }
                    }
                }
            }

            if (!dashboard.lastError.isNullOrBlank()) {
                item {
                    Card(modifier = Modifier.fillMaxWidth()) {
                        Row(
                            modifier = Modifier.padding(16.dp),
                            horizontalArrangement = Arrangement.spacedBy(10.dp),
                            verticalAlignment = Alignment.Top,
                        ) {
                            Icon(
                                Icons.Outlined.ErrorOutline,
                                contentDescription = null,
                                tint = MaterialTheme.colorScheme.error,
                            )
                            Column {
                                Text("Última pendência", fontWeight = FontWeight.Bold)
                                Text(
                                    dashboard.lastError,
                                    style = MaterialTheme.typography.bodySmall,
                                )
                            }
                        }
                    }
                }
            }

            item {
                Button(
                    modifier = Modifier.fillMaxWidth(),
                    enabled = status != SyncStatus.Syncing,
                    onClick = onSync,
                ) {
                    if (status == SyncStatus.Syncing) {
                        CircularProgressIndicator(strokeWidth = 2.dp)
                    } else {
                        Text("Sincronizar e repetir envios")
                    }
                }
            }

            item {
                Text(
                    "Pedidos aguardando envio (${dashboard.pendingOrders.size})",
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Bold,
                )
            }
            if (dashboard.hasOrderDraft) {
                item {
                    Text(
                        "Há um rascunho de pedido salvo neste aparelho. Abra Novo pedido para continuar.",
                        color = MaterialTheme.colorScheme.primary,
                        fontWeight = FontWeight.SemiBold,
                    )
                }
            }
            if (dashboard.pendingOrders.isEmpty()) {
                item {
                    Text(
                        "Nenhum pedido pendente nesta empresa e filial.",
                        style = MaterialTheme.typography.bodyMedium,
                    )
                }
            } else {
                items(dashboard.pendingOrders, key = { it.key }) { pending ->
                    PendingOrderCard(pending)
                }
            }
            item {
                Text(
                    "A separação exige conexão porque estoque, depósito e lotes são revalidados no servidor antes da baixa.",
                    style = MaterialTheme.typography.bodySmall,
                )
            }
        }
    }
}

@Composable
private fun PendingOrderCard(pending: PendingOrderInfo) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(
            modifier = Modifier.padding(14.dp),
            verticalArrangement = Arrangement.spacedBy(5.dp),
        ) {
            Text(pending.clientName, fontWeight = FontWeight.Bold)
            Row(
                horizontalArrangement = Arrangement.spacedBy(6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Icon(Icons.Outlined.Schedule, contentDescription = null)
                Text(
                    formatInstant(pending.createdAt) ?: "Horário não registrado",
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            Text(
                if (pending.attempts == 0) "Aguardando primeiro envio" else
                    "${pending.attempts} tentativa(s) realizada(s)",
                style = MaterialTheme.typography.labelMedium,
            )
            if (!pending.lastError.isNullOrBlank()) {
                Text(
                    pending.lastError,
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodySmall,
                )
            }
        }
    }
}

private fun formatInstant(raw: String?): String? = runCatching {
    raw?.takeIf(String::isNotBlank)?.let {
        DateTimeFormatter.ofPattern("dd/MM/yyyy HH:mm")
            .withZone(ZoneId.systemDefault())
            .format(Instant.parse(it))
    }
}.getOrNull()
