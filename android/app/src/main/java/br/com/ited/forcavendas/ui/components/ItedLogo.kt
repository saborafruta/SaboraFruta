package br.com.ited.forcavendas.ui.components

import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.offset
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.unit.dp
import br.com.ited.forcavendas.R

private val OutlineOffsets = listOf(
    -1.5.dp to 0.dp,
    1.5.dp to 0.dp,
    0.dp to -1.5.dp,
    0.dp to 1.5.dp,
    -1.dp to -1.dp,
    1.dp to -1.dp,
    -1.dp to 1.dp,
    1.dp to 1.dp,
)

@Composable
fun ItedLogo(
    modifier: Modifier = Modifier,
    contentDescription: String = "ITED",
) {
    val logo = painterResource(R.drawable.logo_ited)
    Box(modifier = modifier) {
        OutlineOffsets.forEach { (x, y) ->
            Image(
                painter = logo,
                contentDescription = null,
                modifier = Modifier.fillMaxSize().offset(x, y),
                contentScale = ContentScale.Fit,
                colorFilter = ColorFilter.tint(Color.White),
            )
        }
        Image(
            painter = logo,
            contentDescription = contentDescription,
            modifier = Modifier.fillMaxSize(),
            contentScale = ContentScale.Fit,
        )
    }
}
