package br.com.ited.forcavendas

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
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
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.background,
                    contentColor = MaterialTheme.colorScheme.onBackground,
                ) {
                    AppRoot(repository)
                }
            }
        }
    }
}
