package br.com.ited.forcavendas

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.remember
import br.com.ited.forcavendas.data.ItedRepository
import br.com.ited.forcavendas.data.SessionStore
import br.com.ited.forcavendas.sync.ItedSyncWorker
import br.com.ited.forcavendas.ui.theme.ITEDTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        ItedSyncWorker.schedule(applicationContext)
        enableEdgeToEdge()
        setContent {
            val repository = remember { ItedRepository(SessionStore(applicationContext)) }
            ITEDTheme {
                AppRoot(repository)
            }
        }
    }
}
