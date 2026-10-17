package br.com.ited.forcavendas

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import br.com.ited.forcavendas.feature.home.HomeScreen
import br.com.ited.forcavendas.ui.theme.ITEDTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            ITEDTheme {
                HomeScreen()
            }
        }
    }
}
