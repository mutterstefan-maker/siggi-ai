import 'package:flutter/material.dart';
import '../api.dart';
import '../theme.dart';
import 'login_screen.dart';

class SettingsTab extends StatelessWidget {
  const SettingsTab({super.key});

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        const Text('Mehr', style: TextStyle(color: textMain, fontSize: 24, fontWeight: FontWeight.w800)),
        const SizedBox(height: 20),
        GlassCard(
          child: Row(children: [
            const Icon(Icons.dns_outlined, color: textDim),
            const SizedBox(width: 12),
            Expanded(child: Text(Api.instance.baseUrl, style: const TextStyle(color: textDim, fontSize: 12))),
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
