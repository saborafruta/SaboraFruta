package br.com.ited.forcavendas.feature.home

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.Logout
import androidx.compose.material.icons.automirrored.outlined.ReceiptLong
import androidx.compose.material.icons.outlined.CloudDone
import androidx.compose.material.icons.outlined.Inventory2
import androidx.compose.material.icons.outlined.People
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import br.com.ited.forcavendas.data.UserSession
import br.com.ited.forcavendas.sync.SyncStatus
import br.com.ited.forcavendas.ui.components.ItedLogo

private data class HomeAction(
    val title: String,
    val description: String,
    val icon: ImageVector,
    val enabled: Boolean,
    val onClick: () -> Unit,
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HomeScreen(
    session: UserSession,
    syncStatus: SyncStatus,
    pendingOperations: Int,
    onSync: () -> Unit,
    onOpenSync: () -> Unit,
    onNewOrder: () -> Unit,
    onOrders: () -> Unit,
    onClients: () -> Unit,
    onSeparation: () -> Unit,
    onLogout: () -> Unit,
) {
    val actions = listOf(
        HomeAction(
            "Novo pedido",
            "Cliente, produtos, preço e pagamento",
            Icons.AutoMirrored.Outlined.ReceiptLong,
            session.context.podeCriarPedido,
            onNewOrder,
        ),
        HomeAction(
            "Meus pedidos",
            "Envio e acompanhamento pelo número oficial",
            Icons.Outlined.CloudDone,
            true,
            onOrders,
        ),
        HomeAction(
            "Clientes",
            "Carteira, crédito e tabela comercial",
            Icons.Outlined.People,
            session.context.podeCriarPedido,
            onClients,
        ),
        HomeAction(
            "Separação",
            "Depósito, lote e quantidade conferida",
            Icons.Outlined.Inventory2,
            session.context.podeSeparar,
            onSeparation,
        ),
    )
    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text("ITED Força de Vendas", fontWeight = FontWeight.Bold)
                        Text(
                            "${session.context.empresaNome} • ${session.context.filialNome}",
                            style = MaterialTheme.typography.labelSmall,
                        )
                    }
                },
                actions = {
                    IconButton(onClick = onLogout) {
                        Icon(Icons.AutoMirrored.Outlined.Logout, contentDescription = "Sair")
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.surface,
                ),
            )
        },
    ) { insets ->
        LazyColumn(
            modifier = Modifier
                .fillMaxSize()
                .padding(insets),
            contentPadding = PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            item {
                Box(
                    modifier = Modifier.fillMaxWidth(),
                    contentAlignment = Alignment.Center,
                ) {
                    ItedLogo(modifier = Modifier.size(88.dp))
                }
            }
            item {
                SyncCard(
                    syncStatus = syncStatus,
                    pendingOperations = pendingOperations,
                    onSync = onSync,
                    onOpenSync = onOpenSync,
                )
            }
            item {
                Spacer(Modifier.height(4.dp))
                Text(
                    "Operação",
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Bold,
                )
            }
            items(actions) { action -> ActionCard(action) }
        }
    }
}

@Composable
private fun SyncCard(
    syncStatus: SyncStatus,
    pendingOperations: Int,
    onSync: () -> Unit,
    onOpenSync: () -> Unit,
) {
    val statusText = when (syncStatus) {
        SyncStatus.Idle -> "Toque para atualizar clientes, preços e pedidos"
        is SyncStatus.Synced -> "Sincronizado com o ITED"
        SyncStatus.Syncing -> "Sincronizando…"
        is SyncStatus.Pending -> "${syncStatus.operations} operação(ões) aguardando envio"
        SyncStatus.Offline -> "Sem conexão — dados locais disponíveis"
        is SyncStatus.Failed -> syncStatus.message
    }
    Card(
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.primaryContainer,
        ),
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text("Sincronização", fontWeight = FontWeight.Bold)
                    Text(statusText, style = MaterialTheme.typography.bodySmall)
                    if (pendingOperations > 0) {
                        Text(
                            "$pendingOperations pedido(s) pendente(s)",
                            style = MaterialTheme.typography.labelSmall,
                        )
                    }
                }
                Icon(Icons.Outlined.CloudDone, contentDescription = null)
            }
            Button(
                modifier = Modifier.fillMaxWidth(),
                enabled = syncStatus != SyncStatus.Syncing,
                onClick = onSync,
            ) {
                Text("Sincronizar agora")
            }
            OutlinedButton(
                modifier = Modifier.fillMaxWidth(),
                onClick = onOpenSync,
            ) {
                Text("Abrir Central de sincronização")
            }
        }
    }
}

@Composable
private fun ActionCard(action: HomeAction) {
    Surface(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(enabled = action.enabled, onClick = action.onClick),
        shape = MaterialTheme.shapes.large,
        tonalElevation = 1.dp,
        color = if (action.enabled) {
            MaterialTheme.colorScheme.surface
        } else {
            MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f)
        },
    ) {
        Row(
            modifier = Modifier.padding(16.dp),
            horizontalArrangement = Arrangement.spacedBy(14.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Icon(
                imageVector = action.icon,
                contentDescription = null,
                tint = if (action.enabled) {
                    MaterialTheme.colorScheme.primary
                } else {
                    MaterialTheme.colorScheme.onSurfaceVariant
                },
            )
            Column {
                Text(action.title, fontWeight = FontWeight.Bold)
                Text(
                    if (action.enabled) action.description else "Sem permissão nesta filial",
                    style = MaterialTheme.typography.bodySmall,
                )
            }
        }
    }
}
