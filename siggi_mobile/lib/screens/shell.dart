import 'package:flutter/material.dart';
import '../theme.dart';
import 'home_tab.dart';
import 'chat_tab.dart';
import 'social_hub.dart';
import 'inbox_hub.dart';
import 'settings_tab.dart';

/// The whole app lives behind one bottom nav bar - deliberately not the long
/// side menu of the web dashboard, per request ("nicht mit einem ewig langen
/// Menü an der Seite"). Social/Inbox open their own icon-grid sub-menu
/// (see social_hub.dart / inbox_hub.dart) instead of cramming everything
/// into one screen.
class AppShell extends StatefulWidget {
  const AppShell({super.key});
  @override
  State<AppShell> createState() => _AppShellState();
}

class _AppShellState extends State<AppShell> {
  int _index = 0;

  void _goTo(int i) => setState(() => _index = i);

  late final _tabs = [
    HomeTab(onNavigate: _goTo),
    const ChatTab(),
    const SocialHub(),
    const InboxHub(),
    const SettingsTab(),
  ];

  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    return Scaffold(
      body: SafeArea(
        child: AnimatedSwitcher(
          duration: const Duration(milliseconds: 220),
          child: KeyedSubtree(key: ValueKey(_index), child: _tabs[_index]),
        ),
      ),
      bottomNavigationBar: Container(
        decoration: BoxDecoration(color: c.surface, border: const Border(top: BorderSide(color: Color(0x22808080)))),
        child: SafeArea(
          top: false,
          child: SizedBox(
            height: 64,
            child: Row(
              children: [
                _navItem(Icons.grid_view_rounded, 'Home', 0),
                _navItem(Icons.auto_awesome, 'Chat', 1),
                _navItem(Icons.movie_creation_outlined, 'Social', 2),
                _navItem(Icons.mail_outline, 'Inbox', 3),
                _navItem(Icons.settings_outlined, 'Mehr', 4),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _navItem(IconData icon, String label, int i) {
    final active = _index == i;
    final c = context.colors;
    return Expanded(
      child: InkWell(
        onTap: () => _goTo(i),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            AnimatedScale(
              scale: active ? 1.15 : 1.0,
              duration: const Duration(milliseconds: 180),
              curve: Curves.easeOut,
              child: Icon(icon, color: active ? accent : c.dim, size: 24),
            ),
            const SizedBox(height: 4),
            AnimatedDefaultTextStyle(
              duration: const Duration(milliseconds: 180),
              style: TextStyle(color: active ? accent : c.dim, fontSize: 11, fontWeight: active ? FontWeight.w700 : FontWeight.w400),
              child: Text(label),
            ),
          ],
        ),
      ),
    );
  }
}
