import 'package:flutter/material.dart';
import '../api.dart';
import '../theme.dart';
import 'login_screen.dart';
import 'improvements_screen.dart';
import 'audit_screen.dart';
import 'stats_screen.dart';
import 'desktop_agent_screen.dart';

class SettingsTab extends StatelessWidget {
  const SettingsTab({super.key});

  Future<void> _open(BuildContext context, Widget screen) {
    return Navigator.of(context).push(MaterialPageRoute(builder: (_) => screen));
  }

  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Text('Mehr', style: TextStyle(color: c.text, fontSize: 24, fontWeight: FontWeight.w800)),
        const SizedBox(height: 20),
        GridView.count(
          crossAxisCount: 3,
          shrinkWrap: true,
          physics: const NeverScrollableScrollPhysics(),
          mainAxisSpacing: 12,
          crossAxisSpacing: 12,
          childAspectRatio: 0.95,
          children: [
            IconTile(icon: Icons.psychology_outlined, label: 'Verbesserung', color: warn, onTap: () => _open(context, const ImprovementsScreen())),
            IconTile(icon: Icons.travel_explore_outlined, label: 'Website-Audit', color: accent, onTap: () => _open(context, const AuditScreen())),
            IconTile(icon: Icons.bar_chart_outlined, label: 'Statistiken', color: good, onTap: () => _open(context, const StatsScreen())),
            IconTile(icon: Icons.desktop_windows_outlined, label: 'Desktop-Agent', color: accent2, onTap: () => _open(context, const DesktopAgentScreen())),
          ],
        ),
        const SizedBox(height: 24),
        Text('Darstellung', style: TextStyle(color: c.dim, fontSize: 12, fontWeight: FontWeight.w700, letterSpacing: 1)),
        const SizedBox(height: 8),
        ValueListenableBuilder<ThemeMode>(
          valueListenable: ThemeController.instance.mode,
          builder: (context, mode, _) => GlassCard(
            child: Row(children: [
              Icon(mode == ThemeMode.dark ? Icons.dark_mode_outlined : Icons.light_mode_outlined, color: accent),
              const SizedBox(width: 12),
              Expanded(child: Text(mode == ThemeMode.dark ? 'Dunkelmodus' : 'Hellmodus', style: TextStyle(color: c.text, fontWeight: FontWeight.w600))),
              Switch(
                value: mode == ThemeMode.dark,
                activeThumbColor: accent,
                onChanged: (dark) => ThemeController.instance.toggle(dark),
              ),
            ]),
          ),
        ),
        const SizedBox(height: 20),
        Text('Server', style: TextStyle(color: c.dim, fontSize: 12, fontWeight: FontWeight.w700, letterSpacing: 1)),
        const SizedBox(height: 8),
        GlassCard(
          child: Row(children: [
            Icon(Icons.dns_outlined, color: c.dim),
            const SizedBox(width: 12),
            Expanded(child: Text(Api.instance.baseUrl, style: TextStyle(color: c.dim, fontSize: 12))),
          ]),
        ),
        const SizedBox(height: 12),
        GlassCard(
          onTap: () async {
            await Api.instance.logout();
            if (context.mounted) {
              Navigator.of(context).pushAndRemoveUntil(
                MaterialPageRoute(builder: (_) => const LoginScreen()),
                (route) => false,
              );
            }
          },
          child: const Row(children: [
            Icon(Icons.logout, color: danger),
            SizedBox(width: 12),
            Text('Abmelden', style: TextStyle(color: danger, fontWeight: FontWeight.w600)),
          ]),
        ),
      ],
    );
  }
}
