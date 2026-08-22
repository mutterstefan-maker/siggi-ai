import 'package:flutter/material.dart';

/// Dark, gradient-accented theme matching the reference mood board
/// (deep navy surfaces, indigo/violet accent, rounded cards).
const bg = Color(0xFF0E1220);
const surface = Color(0xFF171C2E);
const surface2 = Color(0xFF1F2540);
const accent = Color(0xFF6C6CF5);
const accent2 = Color(0xFF8B5CF6);
const textMain = Color(0xFFF2F3F8);
const textDim = Color(0xFFA0A6C0);
const good = Color(0xFF34D399);
const warn = Color(0xFFF59E0B);
const danger = Color(0xFFF87171);

const accentGradient = LinearGradient(
  colors: [accent, accent2],
  begin: Alignment.topLeft,
  end: Alignment.bottomRight,
);

ThemeData buildTheme() {
  return ThemeData(
    useMaterial3: true,
    scaffoldBackgroundColor: bg,
    colorScheme: ColorScheme.dark(
      primary: accent,
      secondary: accent2,
      surface: surface,
      error: danger,
    ),
    fontFamily: 'Roboto',
    textTheme: const TextTheme(
      bodyMedium: TextStyle(color: textMain),
      bodySmall: TextStyle(color: textDim),
    ),
    appBarTheme: const AppBarTheme(
      backgroundColor: bg,
      elevation: 0,
      foregroundColor: textMain,
      centerTitle: false,
    ),
    cardTheme: CardThemeData(
      color: surface,
      elevation: 0,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(18)),
      margin: EdgeInsets.zero,
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: surface2,
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: BorderSide.none,
      ),
      hintStyle: const TextStyle(color: textDim),
    ),
    elevatedButtonTheme: ElevatedButtonThemeData(
      style: ElevatedButton.styleFrom(
        backgroundColor: accent,
        foregroundColor: Colors.white,
        elevation: 0,
        padding: const EdgeInsets.symmetric(vertical: 14, horizontal: 20),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
      ),
    ),
  );
}

/// Rounded stat/action tile used across the dashboard grid - keeps the whole
/// app "glanceable" (per request: minimal scrolling, no long side menu).
class GlassCard extends StatelessWidget {
  final Widget child;
  final EdgeInsets padding;
  final VoidCallback? onTap;
  const GlassCard({super.key, required this.child, this.padding = const EdgeInsets.all(16), this.onTap});

  @override
  Widget build(BuildContext context) {
    return Material(
      color: surface,
      borderRadius: BorderRadius.circular(18),
      child: InkWell(
        borderRadius: BorderRadius.circular(18),
        onTap: onTap,
        child: Padding(padding: padding, child: child),
      ),
    );
  }
}
