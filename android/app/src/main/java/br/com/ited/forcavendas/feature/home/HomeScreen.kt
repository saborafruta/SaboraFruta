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
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.Logout
import androidx.compose.material.icons.automirrored.outlined.ReceiptLong
import androidx.compose.material.icons.outlined.CloudDone
import androidx.compose.material.icons.outlined.CloudSync
import androidx.compose.material.icons.outlined.Business
import androidx.compose.material.icons.outlined.DarkMode
import androidx.compose.material.icons.outlined.Inventory2
import androidx.compose.material.icons.outlined.LightMode
import androidx.compose.material.icons.outlined.People
import androidx.compose.material.icons.outlined.SwapHoriz
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
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
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import br.com.ited.forcavendas.data.UserSession
import br.com.ited.forcavendas.sync.SyncStatus
import br.com.ited.forcavendas.ui.components.ItedLogo
import br.com.ited.forcavendas.ui.theme.ItedSuccess
import br.com.ited.forcavendas.ui.theme.ItedWarning
import br.com.ited.forcavendas.ui.theme.SpaceGroteskFontFamily

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
    contextLoading: Boolean,
    onSwitchContext: () -> Unit,
    darkTheme: Boolean,
    onToggleTheme: () -> Unit,
    onLogout: () -> Unit,
) {
    var syncDialogOpen by remember { mutableStateOf(false) }
    val isSynced = syncStatus is SyncStatus.Synced
    val syncColor = when (syncStatus) {
        is SyncStatus.Synced -> ItedSuccess
        SyncStatus.Syncing -> ItedWarning
        else -> MaterialTheme.colorScheme.error
    }
    val syncLabel = when (syncStatus) {
        SyncStatus.Idle -> "Não sincronizado"
        is SyncStatus.Synced -> "Sincronizado"
        SyncStatus.Syncing -> "Sincronizando"
        is SyncStatus.Pending -> "Envio pendente"
        SyncStatus.Offline -> "Sem conexão"
        is SyncStatus.Failed -> "Falha na sincronização"
    }
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
            "Rascunhos, envio e acompanhamento",
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
                        Text(
                            "ITED Força de Vendas",
                            fontFamily = SpaceGroteskFontFamily,
                            fontWeight = FontWeight.Bold,
                            letterSpacing = (-0.35).sp,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                },
                actions = {
                    Surface(
                        shape = CircleShape,
                        color = syncColor.copy(alpha = 0.14f),
                    ) {
                        IconButton(onClick = { syncDialogOpen = true }) {
                            if (syncStatus == SyncStatus.Syncing) {
                                CircularProgressIndicator(
                                    modifier = Modifier.size(21.dp),
                                    color = syncColor,
                                    strokeWidth = 2.5.dp,
                                )
                            } else {
                                Icon(
                                    imageVector = Icons.Outlined.CloudSync,
                                    contentDescription = "Sincronização: $syncLabel",
                                    tint = syncColor,
                                )
                            }
                        }
                    }
                    IconButton(onClick = onToggleTheme) {
                        Icon(
                            imageVector = if (darkTheme) {
                                Icons.Outlined.LightMode
                            } else {
                                Icons.Outlined.DarkMode
                            },
                            contentDescription = if (darkTheme) {
                                "Usar tema claro"
                            } else {
                                "Usar tema escuro"
                            },
                        )
                    }
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
                Surface(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clickable(enabled = !contextLoading, onClick = onSwitchContext),
                    shape = MaterialTheme.shapes.large,
                    color = MaterialTheme.colorScheme.surfaceVariant,
                ) {
                    Row(
                        modifier = Modifier.padding(horizontal = 14.dp, vertical = 12.dp),
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Icon(
                            imageVector = Icons.Outlined.Business,
                            contentDescription = null,
                            tint = MaterialTheme.colorScheme.primary,
                        )
                        Column(modifier = Modifier.weight(1f)) {
                            Text(
                                session.context.empresaNome,
                                style = MaterialTheme.typography.labelMedium,
                            )
                            Text(
                                session.context.filialNome,
                                fontWeight = FontWeight.Bold,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                            )
                        }
                        if (contextLoading) {
                            CircularProgressIndicator(
                                modifier = Modifier.size(22.dp),
                                strokeWidth = 2.5.dp,
                            )
                        } else {
                            Icon(
                                imageVector = Icons.Outlined.SwapHoriz,
                                contentDescription = "Trocar filial",
                            )
                        }
                    }
                }
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
    if (syncDialogOpen) {
        val statusDescription = when (syncStatus) {
            SyncStatus.Idle -> "Faça a primeira atualização para baixar clientes, preços e estoque."
            is SyncStatus.Synced -> "Os dados do aplicativo estão atualizados com o ITED."
            SyncStatus.Syncing -> "Aguarde enquanto os dados são enviados e atualizados."
            is SyncStatus.Pending -> "${syncStatus.operations} operação(ões) aguardando envio."
            SyncStatus.Offline -> "Sem internet no momento. Os dados locais continuam disponíveis."
            is SyncStatus.Failed -> syncStatus.message
        }
        AlertDialog(
            onDismissRequest = { syncDialogOpen = false },
            title = { Text("Sincronização") },
            confirmButton = {},
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Row(
                        horizontalArrangement = Arrangement.spacedBy(10.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Icon(
                            imageVector = if (isSynced) Icons.Outlined.CloudDone else Icons.Outlined.CloudSync,
                            contentDescription = null,
                            tint = syncColor,
                        )
                        Column {
                            Text(syncLabel, fontWeight = FontWeight.Bold, color = syncColor)
                            Text(statusDescription, style = MaterialTheme.typography.bodySmall)
                        }
                    }
                    if (pendingOperations > 0) {
                        Text(
                            "$pendingOperations pedido(s) pendente(s)",
                            style = MaterialTheme.typography.labelMedium,
                            color = MaterialTheme.colorScheme.error,
                        )
                    }
                    Button(
                        modifier = Modifier.fillMaxWidth(),
                        enabled = syncStatus != SyncStatus.Syncing,
                        onClick = {
                            syncDialogOpen = false
                            onSync()
                        },
                    ) {
                        Text("Sincronizar agora")
                    }
                    OutlinedButton(
                        modifier = Modifier.fillMaxWidth(),
                        onClick = {
                            syncDialogOpen = false
                            onOpenSync()
                        },
                    ) {
                        Text("Abrir Central de sincronização")
                    }
                }
            },
        )
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
