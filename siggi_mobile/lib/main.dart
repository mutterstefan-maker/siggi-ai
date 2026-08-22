import 'package:flutter/material.dart';
import 'api.dart';
import 'theme.dart';
import 'screens/login_screen.dart';
import 'screens/shell.dart';

void main() {
  runApp(const SiggiApp());
}

class SiggiApp extends StatelessWidget {
  const SiggiApp({super.key});

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<ThemeMode>(
      valueListenable: ThemeController.instance.mode,
      builder: (context, mode, _) {
        return MaterialApp(
          title: 'Siggi',
          debugShowCheckedModeBanner: false,
          themeMode: mode,
          theme: buildLightTheme(),
          darkTheme: buildDarkTheme(),
          home: const _AuthGate(),
        );
      },
    );
  }
}

class _AuthGate extends StatefulWidget {
  const _AuthGate();
  @override
  State<_AuthGate> createState() => _AuthGateState();
}

class _AuthGateState extends State<_AuthGate> {
  bool _ready = false;

  @override
  void initState() {
    super.initState();
    Future.wait([Api.instance.loadPrefs(), ThemeController.instance.load()])
        .then((_) => setState(() => _ready = true));
  }

  @override
  Widget build(BuildContext context) {
    if (!_ready) return const Scaffold(body: Center(child: CircularProgressIndicator()));
    return Api.instance.isLoggedIn ? const AppShell() : const LoginScreen();
  }
}
