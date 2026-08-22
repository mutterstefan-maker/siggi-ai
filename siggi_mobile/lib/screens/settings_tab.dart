import 'package:flutter/material.dart';
import '../api.dart';
import '../theme.dart';
import 'login_screen.dart';

class SettingsTab extends StatefulWidget {
  const SettingsTab({super.key});
  @override
  State<SettingsTab> createState() => _SettingsTabState();
}

class _SettingsTabState extends State<SettingsTab> {
  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Text('Mehr', style: TextStyle(color: c.text, fontSize: 24, fontWeight: FontWeight.w800)),
        const SizedBox(height: 20),
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
