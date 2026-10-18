package br.com.ited.forcavendas.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.Density

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
    val systemDensity = LocalDensity.current
    val appDensity = Density(
        density = systemDensity.density,
        fontScale = systemDensity.fontScale.coerceAtMost(MAX_APP_FONT_SCALE),
    )

    CompositionLocalProvider(LocalDensity provides appDensity) {
        MaterialTheme(
            colorScheme = if (darkTheme) DarkColors else LightColors,
            typography = ItedTypography,
            content = content,
        )
    }
}

private const val MAX_APP_FONT_SCALE = 1.15f
