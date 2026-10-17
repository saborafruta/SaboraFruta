package br.com.ited.forcavendas.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable

private val LightColors = lightColorScheme(
    primary = ItedOrange,
    onPrimary = ItedLightSurface,
    secondary = ItedOrangeDark,
    background = ItedLightBackground,
    surface = ItedLightSurface,
    error = ItedDanger,
)

private val DarkColors = darkColorScheme(
    primary = ItedBlue,
    onPrimary = ItedLightSurface,
    secondary = ItedBlueDark,
    background = ItedDarkBackground,
    surface = ItedDarkSurface,
    error = ItedDanger,
)

@Composable
fun ITEDTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit,
) {
    MaterialTheme(
        colorScheme = if (darkTheme) DarkColors else LightColors,
        typography = ItedTypography,
        content = content,
    )
}
