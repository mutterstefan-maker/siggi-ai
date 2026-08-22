import 'package:flutter/material.dart';
import '../api.dart';
import '../theme.dart';
import 'shell.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});
  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _user = TextEditingController();
  final _pass = TextEditingController();
  final _url = TextEditingController(text: Api.instance.baseUrl);
  bool _loading = false;
  String? _error;

  Future<void> _submit() async {
    setState(() { _loading = true; _error = null; });
    await Api.instance.setBaseUrl(_url.text.trim());
    final ok = await Api.instance.login(_user.text.trim(), _pass.text);
    if (!mounted) return;
    setState(() => _loading = false);
    if (ok) {
      Navigator.of(context).pushReplacement(MaterialPageRoute(builder: (_) => const AppShell()));
    } else {
      setState(() => _error = 'Login fehlgeschlagen. Zugangsdaten prüfen.');
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Center(
                child: Container(
                  width: 84, height: 84,
                  decoration: const BoxDecoration(gradient: accentGradient, shape: BoxShape.circle),
                  child: const Icon(Icons.auto_awesome, color: Colors.white, size: 40),
                ),
              ),
              const SizedBox(height: 20),
              const Text('SIGGI', textAlign: TextAlign.center,
                  style: TextStyle(color: textMain, fontSize: 28, fontWeight: FontWeight.w800, letterSpacing: 2)),
              const SizedBox(height: 4),
              const Text('Chefblick Assistant', textAlign: TextAlign.center, style: TextStyle(color: textDim)),
              const SizedBox(height: 36),
              TextField(controller: _user, style: const TextStyle(color: textMain),
                  decoration: const InputDecoration(hintText: 'Benutzername', prefixIcon: Icon(Icons.person_outline, color: textDim))),
              const SizedBox(height: 12),
              TextField(controller: _pass, obscureText: true, style: const TextStyle(color: textMain),
                  decoration: const InputDecoration(hintText: 'Passwort', prefixIcon: Icon(Icons.lock_outline, color: textDim))),
              const SizedBox(height: 12),
              TextField(controller: _url, style: const TextStyle(color: textDim, fontSize: 12),
                  decoration: const InputDecoration(hintText: 'Server-URL', prefixIcon: Icon(Icons.dns_outlined, color: textDim))),
              if (_error != null) Padding(
                padding: const EdgeInsets.only(top: 12),
                child: Text(_error!, style: const TextStyle(color: danger), textAlign: TextAlign.center),
              ),
              const SizedBox(height: 20),
              ElevatedButton(
                onPressed: _loading ? null : _submit,
                child: _loading
                    ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                    : const Text('Anmelden'),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
