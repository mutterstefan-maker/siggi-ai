import 'package:flutter/material.dart';
import '../api.dart';
import '../theme.dart';

class DesktopAgentScreen extends StatefulWidget {
  const DesktopAgentScreen({super.key});
  @override
  State<DesktopAgentScreen> createState() => _DesktopAgentScreenState();
}

class _DesktopAgentScreenState extends State<DesktopAgentScreen> {
  bool _paired = false;
  bool _connected = false;
  String? _pairingToken;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final res = await Api.instance.get('/api/desktop/status');
      _paired = res?['paired'] == true;
      _connected = res?['connected'] == true;
    } catch (_) {}
    if (mounted) setState(() => _loading = false);
  }

  Future<void> _pair() async {
    final res = await Api.instance.post('/api/desktop/pair');
    setState(() => _pairingToken = res?['token']?.toString());
  }

  Future<void> _unpair() async {
    await Api.instance.post('/api/desktop/unpair');
    setState(() => _pairingToken = null);
    _load();
  }

  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    return Scaffold(
      appBar: AppBar(title: const Text('Desktop-Agent')),
      body: RefreshIndicator(
        onRefresh: _load,
        child: _loading
            ? const Center(child: CircularProgressIndicator())
            : ListView(
                padding: const EdgeInsets.all(16),
                children: [
                  GlassCard(
                    child: Row(children: [
                      Icon(Icons.desktop_windows_outlined, color: _paired ? good : c.dim),
                      const SizedBox(width: 12),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(_paired ? 'Gekoppelt' : 'Nicht gekoppelt', style: TextStyle(color: c.text, fontWeight: FontWeight.w700)),
                            Text(_connected ? 'Verbunden' : 'Nicht verbunden', style: TextStyle(color: _connected ? good : c.dim, fontSize: 12)),
                          ],
                        ),
                      ),
                    ]),
                  ),
                  const SizedBox(height: 16),
                  if (_pairingToken != null) ...[
                    GlassCard(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text('Pairing-Code (im Desktop-Agent eingeben):', style: TextStyle(color: c.dim, fontSize: 12)),
                          const SizedBox(height: 8),
                          SelectableText(_pairingToken!, style: TextStyle(color: accent, fontSize: 22, fontWeight: FontWeight.w800, letterSpacing: 2)),
                        ],
                      ),
                    ),
                    const SizedBox(height: 16),
                  ],
                  SizedBox(
                    width: double.infinity,
                    child: _paired
                        ? OutlinedButton(
                            onPressed: _unpair,
                            style: OutlinedButton.styleFrom(foregroundColor: danger, side: const BorderSide(color: danger)),
                            child: const Text('Entkoppeln'),
                          )
                        : ElevatedButton(onPressed: _pair, child: const Text('Neu koppeln')),
                  ),
                ],
              ),
      ),
    );
  }
}
