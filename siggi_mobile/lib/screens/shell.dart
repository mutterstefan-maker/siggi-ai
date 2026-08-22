import 'package:flutter/material.dart';
import '../theme.dart';
import 'home_tab.dart';
import 'chat_tab.dart';
import 'social_tab.dart';
import 'inbox_tab.dart';
import 'settings_tab.dart';

/// The whole app lives behind one bottom nav bar - deliberately not the long
/// side menu of the web dashboard, per request ("nicht mit einem ewig langen
/// Menü an der Seite"). Each tab is a single, glanceable screen.
class AppShell extends StatefulWidget {
  const AppShell({super.key});
  @override
  State<AppShell> createState() => _AppShellState();
}

class _AppShellState extends State<AppShell> {
  int _index = 0;

  final _tabs = const [
    HomeTab(),
    ChatTab(),
    SocialTab(),
    InboxTab(),
    SettingsTab(),
  ];

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(child: IndexedStack(index: _index, children: _tabs)),
      bottomNavigationBar: Container(
        decoration: const BoxDecoration(
          color: surface,
          border: Border(top: BorderSide(color: Color(0x22FFFFFF))),
        ),
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
    return Expanded(
      child: InkWell(
        onTap: () => setState(() => _index = i),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(icon, color: active ? accent : textDim, size: 24),
            const SizedBox(height: 4),
            Text(label, style: TextStyle(color: active ? accent : textDim, fontSize: 11, fontWeight: active ? FontWeight.w700 : FontWeight.w400)),
          ],
        ),
      ),
    );
  }
}
