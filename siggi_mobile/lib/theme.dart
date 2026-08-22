import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Dark, gradient-accented theme matching the reference mood board
/// (deep navy surfaces, indigo/violet accent, rounded cards) - plus a light
/// counterpart for the "Tag/Nacht"-Umschalter in den Einstellungen.
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

const bgLight = Color(0xFFF4F5FA);
const surfaceLight = Color(0xFFFFFFFF);
const surface2Light = Color(0xFFECEDF6);
const textMainLight = Color(0xFF1B1E2B);
const textDimLight = Color(0xFF6B7086);

const accentGradient = LinearGradient(
  colors: [accent, accent2],
  begin: Alignment.topLeft,
  end: Alignment.bottomRight,
);

/// Global, persisted light/dark switch - kept deliberately simple (a
/// ValueNotifier the whole app listens to) rather than a full state
/// management package, since this is the only piece of cross-cutting state.
class ThemeController {
  ThemeController._();
  static final ThemeController instance = ThemeController._();
  final ValueNotifier<ThemeMode> mode = ValueNotifier(ThemeMode.dark);

  Future<void> load() async {
    final prefs = await SharedPreferences.getInstance();
    final saved = prefs.getString('theme_mode');
    mode.value = saved == 'light' ? ThemeMode.light : ThemeMode.dark;
  }

  Future<void> toggle(bool dark) async {
    mode.value = dark ? ThemeMode.dark : ThemeMode.light;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('theme_mode', dark ? 'dark' : 'light');
  }
}

ThemeData buildDarkTheme() => _build(
      bg: bg, surface: surface, surface2: surface2, text: textMain, dim: textDim, brightness: Brightness.dark,
    );

ThemeData buildLightTheme() => _build(
      bg: bgLight, surface: surfaceLight, surface2: surface2Light, text: textMainLight, dim: textDimLight, brightness: Brightness.light,
    );

ThemeData _build({required Color bg, required Color surface, required Color surface2, required Color text, required Color dim, required Brightness brightness}) {
  return ThemeData(
    useMaterial3: true,
    brightness: brightness,
    scaffoldBackgroundColor: bg,
    colorScheme: ColorScheme(
      brightness: brightness,
      primary: accent, onPrimary: Colors.white,
      secondary: accent2, onSecondary: Colors.white,
      surface: surface, onSurface: text,
      error: danger, onError: Colors.white,
    ),
    extensions: [AppColors(bg: bg, surface: surface, surface2: surface2, text: text, dim: dim)],
    textTheme: TextTheme(bodyMedium: TextStyle(color: text), bodySmall: TextStyle(color: dim)),
    appBarTheme: AppBarTheme(backgroundColor: bg, elevation: 0, foregroundColor: text, centerTitle: false),
    cardTheme: CardThemeData(
      color: surface, elevation: 0,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(18)),
      margin: EdgeInsets.zero,
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true, fillColor: surface2,
      border: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: BorderSide.none),
      hintStyle: TextStyle(color: dim),
    ),
    elevatedButtonTheme: ElevatedButtonThemeData(
      style: ElevatedButton.styleFrom(
        backgroundColor: accent, foregroundColor: Colors.white, elevation: 0,
        padding: const EdgeInsets.symmetric(vertical: 14, horizontal: 20),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
      ),
    ),
  );
}

/// Theme-aware colors, fetched via `context.colors` instead of the raw dark
/// constants so every screen respects the light/dark toggle automatically.
class AppColors extends ThemeExtension<AppColors> {
  final Color bg, surface, surface2, text, dim;
  const AppColors({required this.bg, required this.surface, required this.surface2, required this.text, required this.dim});
  @override
  AppColors copyWith({Color? bg, Color? surface, Color? surface2, Color? text, Color? dim}) =>
      AppColors(bg: bg ?? this.bg, surface: surface ?? this.surface, surface2: surface2 ?? this.surface2, text: text ?? this.text, dim: dim ?? this.dim);
  @override
  AppColors lerp(ThemeExtension<AppColors>? other, double t) => this;
}

extension AppColorsX on BuildContext {
  AppColors get colors => Theme.of(this).extension<AppColors>()!;
}

/// Rounded stat/action tile used across the dashboard grids - keeps the app
/// "glanceable" with minimal scrolling. Fades/scales in for a bit of polish.
class GlassCard extends StatelessWidget {
  final Widget child;
  final EdgeInsets padding;
  final VoidCallback? onTap;
  const GlassCard({super.key, required this.child, this.padding = const EdgeInsets.all(16), this.onTap});

  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    return Material(
      color: c.surface,
      borderRadius: BorderRadius.circular(18),
      child: InkWell(
        borderRadius: BorderRadius.circular(18),
        onTap: onTap,
        child: Padding(padding: padding, child: child),
      ),
    );
  }
}

/// A tappable icon tile for the sub-menus that open under each bottom-nav
/// icon (Social/Inbox hubs), per request - big icon, short label, small badge.
class IconTile extends StatelessWidget {
  final IconData icon;
  final String label;
  final Color color;
  final int? badge;
  final VoidCallback onTap;
  const IconTile({super.key, required this.icon, required this.label, required this.color, required this.onTap, this.badge});

  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    return GlassCard(
      onTap: onTap,
      child: Stack(
        children: [
          Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Container(
                width: 48, height: 48,
                decoration: BoxDecoration(color: color.withValues(alpha: 0.15), borderRadius: BorderRadius.circular(14)),
                child: Icon(icon, color: color, size: 24),
              ),
              const SizedBox(height: 10),
              Text(label, textAlign: TextAlign.center, style: TextStyle(color: c.text, fontSize: 12, fontWeight: FontWeight.w700)),
            ],
          ),
          if (badge != null && badge! > 0)
            Positioned(
              top: -6, right: -6,
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                decoration: BoxDecoration(color: danger, borderRadius: BorderRadius.circular(10)),
                child: Text('$badge', style: const TextStyle(color: Colors.white, fontSize: 10, fontWeight: FontWeight.w700)),
              ),
            ),
        ],
      ),
    );
  }
}
