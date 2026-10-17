package br.com.ited.forcavendas.sync

import java.time.Instant

sealed interface SyncStatus {
    data object Idle : SyncStatus
    data class Synced(val at: Instant) : SyncStatus
    data object Syncing : SyncStatus
    data class Pending(val operations: Int) : SyncStatus
    data object Offline : SyncStatus
    data class Failed(val message: String) : SyncStatus
}
